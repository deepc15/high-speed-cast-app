"""Transport setup: ADB tunnels for USB, plain TCP for Wi-Fi."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field

from .protocol import Conn, ProtocolError
from .util import log

# Ports the Android app listens on when it is the sender (phone -> PC).
MIRROR_VIDEO_PORT = 8765
MIRROR_CONTROL_PORT = 8766
# Port the PC listens on when it is the sender (PC -> phone).
DESKTOP_VIDEO_PORT = 8767

ANDROID_PACKAGE = "com.hscast"
ANDROID_MAIN_ACTIVITY = f"{ANDROID_PACKAGE}/.MainActivity"

_ADB_HINTS = (
    os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
    os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe"),
    os.path.expandvars(r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe"),
    r"C:\Program Files\Android\platform-tools\adb.exe",
)


class TransportError(Exception):
    pass


def find_adb() -> str:
    custom = os.environ.get("HSCAST_ADB")
    if custom and os.path.isfile(custom):
        return custom
    found = shutil.which("adb")
    if found:
        return found
    for hint in _ADB_HINTS:
        if hint and os.path.isfile(hint):
            return hint
    raise TransportError(
        "adb not found. Install Android platform-tools and put adb on PATH, "
        "or use --wifi <phone-ip> to skip USB entirely."
    )


class Adb:
    def __init__(self, serial: str | None = None):
        self.exe = find_adb()
        self.serial = serial

    def _argv(self, args: list[str]) -> list[str]:
        argv = [self.exe]
        if self.serial:
            argv += ["-s", self.serial]
        return argv + args

    def run(self, *args: str, check: bool = True, timeout: float = 20.0) -> str:
        proc = subprocess.run(
            self._argv(list(args)),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check and proc.returncode != 0:
            raise TransportError(
                f"adb {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}"
            )
        return proc.stdout

    def devices(self) -> list[str]:
        out = subprocess.run(
            [self.exe, "devices"], capture_output=True, text=True, timeout=20
        ).stdout
        serials = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def require_device(self) -> str:
        serials = self.devices()
        if not serials:
            raise TransportError(
                "No authorised device. Plug the phone in, enable USB debugging, "
                "and accept the RSA prompt on the phone."
            )
        if self.serial:
            if self.serial not in serials:
                raise TransportError(f"device {self.serial} not connected")
            return self.serial
        if len(serials) > 1:
            raise TransportError(
                f"Multiple devices connected ({', '.join(serials)}). Pass --serial."
            )
        self.serial = serials[0]
        return self.serial

    # -- tunnels -------------------------------------------------------------

    def forward(self, local_port: int, remote_port: int) -> None:
        """PC:local_port -> device:remote_port."""
        self.run("forward", f"tcp:{local_port}", f"tcp:{remote_port}")

    def forward_remove(self, local_port: int) -> None:
        self.run("forward", "--remove", f"tcp:{local_port}", check=False)

    def reverse(self, device_port: int, local_port: int) -> None:
        """device:device_port -> PC:local_port."""
        self.run("reverse", f"tcp:{device_port}", f"tcp:{local_port}")

    def reverse_remove(self, device_port: int) -> None:
        self.run("reverse", "--remove", f"tcp:{device_port}", check=False)

    def launch_app(self, extras: dict[str, str] | None = None) -> None:
        args = ["shell", "am", "start", "-n", ANDROID_MAIN_ACTIVITY]
        for key, value in (extras or {}).items():
            args += ["--es", key, value]
        self.run(*args)

    def app_installed(self) -> bool:
        out = self.run("shell", "pm", "list", "packages", ANDROID_PACKAGE, check=False)
        return ANDROID_PACKAGE in out


@dataclass
class Tunnels:
    """Owns whatever ADB tunnels a session needed, and tears them down."""

    adb: Adb | None = None
    _forwards: list[int] = field(default_factory=list)
    _reverses: list[int] = field(default_factory=list)

    def forward(self, local_port: int, remote_port: int) -> None:
        assert self.adb is not None
        self.adb.forward(local_port, remote_port)
        self._forwards.append(local_port)
        log(f"usb tunnel: localhost:{local_port} -> device:{remote_port}")

    def reverse(self, device_port: int, local_port: int) -> None:
        assert self.adb is not None
        self.adb.reverse(device_port, local_port)
        self._reverses.append(device_port)
        log(f"usb tunnel: device:{device_port} -> localhost:{local_port}")

    def close(self) -> None:
        if self.adb is None:
            return
        for port in self._forwards:
            self.adb.forward_remove(port)
        for port in self._reverses:
            self.adb.reverse_remove(port)
        self._forwards.clear()
        self._reverses.clear()

    def __enter__(self) -> "Tunnels":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def connect(host: str, port: int, channel: int, role: int,
            timeout: float = 120.0, retry_interval: float = 0.3,
            check_cancelled=None, conn_mode: str = "usb") -> Conn:
    """Dial the peer, retrying until ``timeout`` — the phone may not be listening yet."""
    from hscast.protocol import (
        FLAG_MODE_USB, FLAG_MODE_WIFI, FLAG_MODE_UNSPECIFIED, ModeMismatchError
    )
    mode_flag = FLAG_MODE_USB if conn_mode == "usb" else (FLAG_MODE_WIFI if conn_mode == "wifi" else FLAG_MODE_UNSPECIFIED)
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    attempt = 0
    last_cancel_check = 0.0

    while time.monotonic() < deadline:
        attempt += 1
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.settimeout(2.0)
            conn = Conn(sock, channel, role)
            conn.handshake(mode=mode_flag)
            sock.settimeout(None)
            log(f"connected to {host}:{port} (channel {channel})")
            return conn
        except (OSError, ConnectionError, ProtocolError) as exc:
            last = exc
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

            if isinstance(exc, ModeMismatchError):
                # Fail immediately so user sees the validation message right away
                raise TransportError(str(exc))

            if isinstance(exc, ProtocolError) and "cancelled" in str(exc).lower():
                raise TransportError("Screen capture was cancelled on the Android device.")

            now = time.monotonic()
            if attempt == 1:
                log(f"waiting for Android device to accept screen capture prompt on {host}:{port} ...")
            elif attempt % 15 == 0:
                remaining = max(1, int(deadline - now))
                log(f"waiting for capture prompt on Android device ({remaining}s remaining)...")

            if check_cancelled and (now - last_cancel_check >= 0.8):
                last_cancel_check = now
                if check_cancelled():
                    raise TransportError("Screen capture was cancelled on the Android device.")

            time.sleep(retry_interval)
            continue

    raise TransportError(f"could not reach {host}:{port} within {timeout:g}s: {last}")


def listen_one(port: int, channel: int, role: int, timeout: float = 120.0,
               bind: str = "0.0.0.0", conn_mode: str = "usb") -> Conn:
    """Accept exactly one valid connection on ``port`` and hand back the framed conn."""
    from hscast.protocol import (
        FLAG_MODE_USB, FLAG_MODE_WIFI, FLAG_MODE_UNSPECIFIED, ModeMismatchError
    )
    mode_flag = FLAG_MODE_USB if conn_mode == "usb" else (FLAG_MODE_WIFI if conn_mode == "wifi" else FLAG_MODE_UNSPECIFIED)
    deadline = time.monotonic() + timeout
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((bind, port))
        server.listen(5)
        log(f"listening on {bind}:{port} (channel {channel})")

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            server.settimeout(min(remaining, 2.0))
            try:
                sock, addr = server.accept()
            except socket.timeout:
                continue

            sock.settimeout(3.0)
            conn = Conn(sock, channel, role)
            try:
                conn.handshake(mode=mode_flag)
                sock.settimeout(None)
                log(f"accepted {addr[0]}:{addr[1]} (channel {channel})")
                return conn
            except ModeMismatchError as exc:
                log(f"validation failed: {exc}")
                conn.close()
                raise TransportError(str(exc))
            except (ConnectionError, OSError, ProtocolError) as exc:
                log(f"ignored probe or incomplete handshake from {addr[0]}:{addr[1]} ({exc})")
                conn.close()
                continue

        raise TransportError(f"no peer connected on port {port} within {timeout:g}s")
    finally:
        server.close()


def local_ipv4() -> str:
    """Best-effort LAN address of this machine, for the 'point the phone here' hint."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()
