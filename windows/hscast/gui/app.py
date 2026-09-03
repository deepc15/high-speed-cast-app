"""Main application controller and launcher for HSCast Windows GUI."""

from __future__ import annotations

import collections
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any

from .config import load_config, save_config
from .server import start_server


class HSCastManager:
    def __init__(self) -> None:
        self.config = load_config()
        self.session_process: subprocess.Popen | None = None
        self.demo_process: subprocess.Popen | None = None
        self.state = "idle"  # "idle" | "starting" | "running" | "error"
        self.state_msg = "Idle"
        self.start_time: float | None = None
        self.active_mode: str | None = None
        self.logs: collections.deque[dict[str, Any]] = collections.deque(maxlen=600)
        self.log_id_counter = 0
        self._log_lock = threading.Lock()
        self._status_cache: dict[str, Any] | None = None
        self._last_status_check = 0.0

        # Apply custom ADB path if configured
        if self.config.get("custom_adb_path"):
            os.environ["HSCAST_ADB"] = self.config["custom_adb_path"]

    def _append_log(self, text: str, stream: str = "stdout") -> None:
        level = "info"
        clean = text.strip()
        if not clean:
            return
        lower = clean.lower()
        if "error" in lower or "fail" in lower or "exception" in lower:
            level = "error"
        elif "warn" in lower or "dropped" in lower:
            level = "warn"
        elif "stream:" in lower or "fps" in lower or "rtt" in lower:
            level = "success"

        with self._log_lock:
            self.log_id_counter += 1
            entry = {
                "id": self.log_id_counter,
                "time": time.strftime("%H:%M:%S"),
                "text": clean,
                "level": level,
                "stream": stream,
            }
            self.logs.append(entry)

    def get_config(self) -> dict[str, Any]:
        return dict(self.config)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        self.config = save_config(updates)
        if "custom_adb_path" in updates:
            path = updates["custom_adb_path"].strip()
            if path:
                os.environ["HSCAST_ADB"] = path
            elif "HSCAST_ADB" in os.environ:
                del os.environ["HSCAST_ADB"]
        return dict(self.config)

    def get_system_status(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._status_cache and (now - self._last_status_check < 3.0):
            return self._status_cache

        # 1. Python version
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        # 2. PyAV
        pyav_ok = False
        pyav_version = "Missing"
        try:
            import av
            pyav_ok = True
            pyav_version = av.__version__
        except Exception as e:
            pyav_version = str(e)

        # 3. SDL2
        sdl2_ok = False
        sdl2_version = "Missing"
        try:
            import sdl2
            v = sdl2.SDL_version()
            sdl2.SDL_GetVersion(v)
            sdl2_ok = True
            sdl2_version = f"{v.major}.{v.minor}.{v.patch}"
        except Exception as e:
            sdl2_version = str(e)

        # 4. Encoders
        encoders: list[str] = []
        try:
            from hscast.encoder import _H264_ORDER, _HEVC_ORDER, probe_encoder
            all_codecs = _H264_ORDER + _HEVC_ORDER
            encoders = [name for name in all_codecs if probe_encoder(name)]
        except Exception:
            pass

        # 5. Capture backends
        dxcam_ok = False
        try:
            import dxcam  # noqa: F401
            dxcam_ok = True
        except Exception:
            pass

        mss_ok = False
        try:
            import mss  # noqa: F401
            mss_ok = True
        except Exception:
            pass

        # 6. ADB
        adb_found = False
        adb_path = ""
        adb_error = ""
        try:
            from hscast.transport import find_adb
            adb_path = find_adb()
            adb_found = True
        except Exception as exc:
            adb_error = str(exc)

        # 7. Devices
        devices = self.get_devices()

        res = {
            "os": platform.platform(),
            "python": {"version": py_ver, "ok": sys.version_info >= (3, 10)},
            "pyav": {"version": pyav_version, "ok": pyav_ok},
            "sdl2": {"version": sdl2_version, "ok": sdl2_ok},
            "encoders": encoders,
            "has_gpu_encoder": any(not e.startswith("lib") for e in encoders),
            "dxcam": dxcam_ok,
            "mss": mss_ok,
            "adb": {
                "ok": adb_found,
                "path": adb_path,
                "error": adb_error,
            },
            "devices": devices,
            "monitors": self.get_monitors(),
            "local_ips": self.get_local_ips(),
        }
        self._status_cache = res
        self._last_status_check = now
        return res

    def get_devices(self) -> list[dict[str, str]]:
        try:
            from hscast.transport import find_adb
            exe = find_adb()
        except Exception:
            return []

        try:
            proc = subprocess.run(
                [exe, "devices", "-l"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            devices = []
            for line in proc.stdout.splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    status = parts[1]
                    model = serial
                    for part in parts[2:]:
                        if part.startswith("model:"):
                            model = part.split(":", 1)[1].replace("_", " ")
                    devices.append({
                        "serial": serial,
                        "status": status,
                        "model": model,
                    })
            return devices
        except Exception:
            return []

    def get_local_ips(self) -> list[str]:
        ips = []
        try:
            # First try the primary outward-facing IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                primary = s.getsockname()[0]
                if primary and primary != "127.0.0.1":
                    ips.append(primary)
        except Exception:
            pass

        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips and not ip.startswith("127."):
                    ips.append(ip)
        except Exception:
            pass

        return ips or ["127.0.0.1"]

    def get_monitors(self) -> list[dict[str, Any]]:
        monitors = []
        try:
            import mss
            with mss.mss() as sct:
                # monitor 0 is all monitors combined, 1..n are individual
                for idx, mon in enumerate(sct.monitors):
                    if idx == 0 and len(sct.monitors) > 2:
                        continue
                    mon_index = 0 if len(sct.monitors) <= 2 else (idx - 1)
                    monitors.append({
                        "index": mon_index,
                        "name": f"Monitor {idx} ({mon['width']}x{mon['height']})" + (" [Primary]" if idx == 1 else ""),
                        "width": mon["width"],
                        "height": mon["height"],
                    })
        except Exception:
            monitors.append({"index": 0, "name": "Primary Display (Default)", "width": 1920, "height": 1080})
        return monitors

    def get_session_state(self, since: int = 0) -> dict[str, Any]:
        # Check if process exited
        if self.session_process:
            ret = self.session_process.poll()
            if ret is not None:
                self.session_process = None
                self.state = "idle"
                self.state_msg = "Idle"
                self.start_time = None

        # For normal cases when not connected, always keep state as idle and message as Idle
        if self.session_process is None and self.state != "starting":
            self.state = "idle"
            self.state_msg = "Idle"
            self.start_time = None

        with self._log_lock:
            filtered_logs = [log for log in self.logs if log["id"] > since]

        uptime_s = int(time.time() - self.start_time) if (self.start_time and self.state == "running") else 0

        return {
            "state": self.state,
            "state_msg": self.state_msg,
            "mode": self.active_mode,
            "uptime_seconds": uptime_s,
            "logs": filtered_logs,
            "demo_running": self.demo_process is not None and self.demo_process.poll() is None,
        }

    def start_session(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.session_process and self.session_process.poll() is None:
            return {"ok": False, "error": "A session is already running. Stop it first."}

        mode = params.get("mode", "mirror")
        conn_type = params.get("conn_type", "usb")
        self.active_mode = mode
        self.state = "starting"
        self.state_msg = f"Starting {mode} session..."
        self._append_log(f"--- Starting {mode.upper()} session ({conn_type}) ---")

        cmd = [sys.executable, "-u", "-m", "hscast", mode]

        # Arguments mapping
        if mode == "mirror":
            cmd += ["--timeout", "120.0"]
            if conn_type == "wifi":
                host = params.get("phone_ip", "").strip()
                if not host:
                    self.state = "idle"
                    self.state_msg = "Idle"
                    return {"ok": False, "error": "Phone IP address is required for Wi-Fi mode"}
                cmd += ["--wifi", host]

                # Pre-validation check: if Wi-Fi is selected on PC, verify Android is not in USB mode
                try:
                    from hscast.transport import Adb
                    adb = Adb(params.get("serial") or None)
                    pref_out = adb.run(
                        "shell", "run-as", "com.hscast", "cat", "/data/data/com.hscast/shared_prefs/hscast.xml",
                        check=False, timeout=1.0,
                    )
                    if 'name="mode_type"' in pref_out and ('>usb<' in pref_out or 'value="usb"' in pref_out):
                        try:
                            adb.run(
                                "shell", "am", "broadcast", "-a", "com.hscast.VALIDATION_ERROR",
                                "--es", "message", "Please select Wi-Fi option in Android to proceed",
                                check=False, timeout=1.0,
                            )
                        except Exception:
                            pass
                        self.state = "idle"
                        self.state_msg = "Idle"
                        self._append_log("[ERROR] Validation failed: Please select Wi-Fi option in Android to proceed")
                        return {"ok": False, "error": "Please select Wi-Fi option in Android to proceed"}
                except Exception:
                    pass
            else:
                serial = params.get("serial", "").strip()
                if serial:
                    cmd += ["--serial", serial]

                # Pre-validation check: if USB is selected on PC, verify Android is not in Wi-Fi mode
                try:
                    from hscast.transport import Adb
                    adb = Adb(serial or None)
                    pref_out = adb.run(
                        "shell", "run-as", "com.hscast", "cat", "/data/data/com.hscast/shared_prefs/hscast.xml",
                        check=False, timeout=1.0,
                    )
                    if 'name="mode_type"' in pref_out and ('>wifi<' in pref_out or 'value="wifi"' in pref_out):
                        try:
                            adb.run(
                                "shell", "am", "broadcast", "-a", "com.hscast.VALIDATION_ERROR",
                                "--es", "message", "Please select USB option in Android to proceed",
                                check=False, timeout=1.0,
                            )
                        except Exception:
                            pass
                        self.state = "idle"
                        self.state_msg = "Idle"
                        self._append_log("[ERROR] Validation failed: Please select USB option in Android to proceed")
                        return {"ok": False, "error": "Please select USB option in Android to proceed"}
                except Exception:
                    pass

            if not params.get("control", True):
                cmd.append("--no-control")
            if not params.get("hwaccel", True):
                cmd.append("--no-hwaccel")
            if params.get("vsync", False):
                cmd.append("--vsync")
            if params.get("record"):
                cmd += ["--record", params["record"]]

        elif mode == "desktop":
            if conn_type == "wifi":
                cmd.append("--wifi")
            else:
                serial = params.get("serial", "").strip()
                if serial:
                    cmd += ["--serial", serial]

            fps = params.get("fps", 60)
            cmd += ["--fps", str(fps)]

            bitrate = params.get("bitrate", 12_000_000)
            cmd += ["--bitrate", str(bitrate)]

            codec = params.get("codec", "h264")
            cmd += ["--codec", codec]

            max_size = params.get("max_size", 1920)
            cmd += ["--max-size", str(max_size)]

            monitor = params.get("monitor", 0)
            cmd += ["--monitor", str(monitor)]

            if not params.get("cursor", True):
                cmd.append("--no-cursor")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if self.config.get("custom_adb_path"):
            env["HSCAST_ADB"] = self.config["custom_adb_path"]
        else:
            try:
                from hscast.transport import find_adb
                env["HSCAST_ADB"] = find_adb()
            except Exception:
                pass

        try:
            self._append_log(f"Command: {' '.join(cmd)}")
            self.session_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=env,
            )
            self.start_time = time.time()
            self.state = "running"
            self.state_msg = f"{mode.capitalize()} session active"

            # Background thread to capture process output
            def stream_reader(proc: subprocess.Popen) -> None:
                assert proc.stdout is not None
                try:
                    for line in iter(proc.stdout.readline, ""):
                        if line:
                            self._append_log(line)
                except Exception:
                    pass
                finally:
                    proc.stdout.close()

            threading.Thread(target=stream_reader, args=(self.session_process,), daemon=True).start()
            return {"ok": True, "message": "Session started"}
        except Exception as exc:
            self.state = "error"
            self.state_msg = f"Failed to launch: {exc}"
            self._append_log(f"Error starting process: {exc}", stream="stderr")
            return {"ok": False, "error": str(exc)}

    def stop_session(self) -> dict[str, Any]:
        if not self.session_process or self.session_process.poll() is not None:
            self.state = "idle"
            self.state_msg = "Idle"
            return {"ok": True, "message": "Session already stopped"}

        self.state = "stopping"
        self.state_msg = "Stopping session..."
        self._append_log("--- Stopping session ---")

        proc = self.session_process
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            self._append_log(f"Error terminating process: {e}")

        self.session_process = None
        self.state = "idle"
        self.state_msg = "Idle"
        self.start_time = None
        return {"ok": True, "message": "Session stopped"}

    def start_demo(self, role: str = "sender") -> dict[str, Any]:
        """Start fake Android stand-in for immediate testing without a real device."""
        if self.demo_process and self.demo_process.poll() is None:
            return {"ok": False, "error": "A demo simulator is already running"}

        fake_script = Path(__file__).resolve().parent.parent.parent / "tools" / "fake_android.py"
        if not fake_script.is_file():
            return {"ok": False, "error": f"fake_android.py not found at {fake_script}"}

        cmd = [sys.executable, "-u", str(fake_script), role]
        self._append_log(f"--- Launching Virtual Android ({role}) for Demo Mode ---")
        try:
            self.demo_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            def demo_reader(proc: subprocess.Popen) -> None:
                assert proc.stdout is not None
                try:
                    for line in iter(proc.stdout.readline, ""):
                        if line:
                            self._append_log(f"[Virtual Android] {line.strip()}")
                except Exception:
                    pass

            threading.Thread(target=demo_reader, args=(self.demo_process,), daemon=True).start()
            return {"ok": True, "message": f"Virtual Android ({role}) started"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def stop_demo(self) -> dict[str, Any]:
        if self.demo_process and self.demo_process.poll() is None:
            try:
                self.demo_process.terminate()
                self.demo_process.wait(timeout=1.5)
            except Exception:
                self.demo_process.kill()
        self.demo_process = None
        self._append_log("--- Virtual Android stopped ---")
        return {"ok": True, "message": "Virtual Android stopped"}

    def trigger_action(self, action: str) -> dict[str, Any]:
        """Trigger Android remote control shortcuts via ADB."""
        key_map = {
            "back": "4",
            "home": "3",
            "recents": "187",
            "lock": "26",
            "wake": "224",
            "vol_up": "24",
            "vol_down": "25",
        }
        keycode = key_map.get(action)
        if not keycode:
            return {"ok": False, "error": f"Unknown action: {action}"}

        try:
            from hscast.transport import find_adb
            exe = find_adb()
            serial = self.config.get("serial")
            cmd = [exe]
            if serial:
                cmd += ["-s", serial]
            cmd += ["shell", "input", "keyevent", keycode]
            subprocess.run(cmd, timeout=3, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._append_log(f"Sent Android command: {action} (keyevent {keycode})")
            return {"ok": True, "action": action}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def browse_adb(self) -> dict[str, Any]:
        """Open native file dialog to locate adb.exe."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askopenfilename(
                title="Select adb.exe",
                filetypes=[("Executable", "adb.exe"), ("All files", "*.*")],
            )
            root.destroy()
            if selected and os.path.isfile(selected):
                self.update_config({"custom_adb_path": selected})
                return {"ok": True, "path": selected}
            return {"ok": False, "error": "No file selected"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def browse_record_file(self) -> dict[str, Any]:
        """Open native file dialog to choose output recording path."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.asksaveasfilename(
                title="Save Raw Stream Recording",
                defaultextension=".h264",
                filetypes=[("H.264 video", "*.h264"), ("HEVC video", "*.hevc"), ("All files", "*.*")],
            )
            root.destroy()
            if selected:
                self.update_config({"record": selected})
                return {"ok": True, "path": selected}
            return {"ok": False, "error": "Cancelled"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def launch_gui(browser_mode: bool = False, port: int | None = None) -> int:
    """Entry point for launching the HSCast Windows GUI."""
    manager = HSCastManager()
    server, active_port = start_server(manager, port)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{active_port}"
    print(f"\n=======================================================")
    print(f"  HSCast Windows Application running at:")
    print(f"  --> {url}")
    print(f"=======================================================\n")

    if browser_mode:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            manager.stop_session()
            manager.stop_demo()
            return 0

    # Native Edge WebView2 Window
    try:
        import webview
        window = webview.create_window(
            title="HSCast - High Speed Screen Casting",
            url=url,
            width=960,
            height=760,
            min_size=(820, 620),
            background_color="#f1f5f9",
            text_select=True,
        )

        def on_closing():
            manager.stop_session()
            manager.stop_demo()

        window.events.closing += on_closing
        webview.start(debug=False)
        return 0
    except Exception as exc:
        print(f"Native window unavailable ({exc}); falling back to default web browser.")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            manager.stop_session()
            manager.stop_demo()
            return 0
