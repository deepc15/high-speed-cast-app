"""``hscast doctor`` -- tell the user exactly which piece is missing."""

from __future__ import annotations

import platform
import sys

_OK = "  ok  "
_WARN = " warn "
_FAIL = " fail "


def _line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f"  --  {detail}" if detail else ""))


def _check_python() -> bool:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 10):
        _line(_FAIL, "python", f"{version}: needs 3.10 or newer")
        return False
    _line(_OK, "python", version)
    return True


def _check_pyav() -> bool:
    try:
        import av
    except ImportError as exc:
        _line(_FAIL, "PyAV", f"{exc}; pip install av")
        return False
    _line(_OK, "PyAV", f"{av.__version__} (libavcodec {'.'.join(map(str, av.library_versions['libavcodec']))})")

    for codec in ("h264", "hevc"):
        try:
            av.codec.Codec(codec, "r")
            _line(_OK, f"decoder {codec}")
        except Exception as exc:
            _line(_FAIL, f"decoder {codec}", str(exc))

    from .encoder import _H264_ORDER, _HEVC_ORDER, probe_encoder

    # Probing means actually opening each encoder: being present in the build
    # says nothing about whether this machine's GPU can run it.
    usable = [name for name in _H264_ORDER + _HEVC_ORDER if probe_encoder(name)]
    if usable:
        _line(_OK, "encoders", ", ".join(usable))
        if not any(not name.startswith("lib") for name in usable):
            _line(_WARN, "hw encode", "no usable GPU encoder; software encoding will "
                                      "cost more CPU and a few ms more latency")
    else:
        _line(_FAIL, "encoders", "no usable h264/hevc encoder on this machine")
    return True


def _check_sdl() -> bool:
    try:
        import sdl2
    except (ImportError, RuntimeError) as exc:
        _line(_FAIL, "PySDL2", f"{exc}; pip install PySDL2 pysdl2-dll")
        return False
    version = sdl2.SDL_version()
    sdl2.SDL_GetVersion(version)
    text = f"{version.major}.{version.minor}.{version.patch}"
    if (version.major, version.minor, version.patch) < (2, 0, 16):
        _line(_WARN, "SDL2", f"{text}: NV12 texture upload needs 2.0.16+, "
                             "run mirror with --no-hwaccel")
    else:
        _line(_OK, "SDL2", text)
    return True


def _check_capture() -> None:
    try:
        import dxcam  # noqa: F401
        _line(_OK, "dxcam", "DXGI Desktop Duplication available")
    except ImportError:
        _line(_WARN, "dxcam", "not installed; falling back to the slower mss backend")
    try:
        import mss  # noqa: F401
        _line(_OK, "mss", "fallback capture available")
    except ImportError:
        _line(_WARN, "mss", "not installed")


def _check_adb() -> None:
    from .transport import Adb, TransportError

    try:
        adb = Adb()
    except TransportError as exc:
        _line(_WARN, "adb", f"{exc}")
        return
    _line(_OK, "adb", adb.exe)
    serials = adb.devices()
    if not serials:
        _line(_WARN, "device", "no authorised device; plug in and allow USB debugging")
        return
    _line(_OK, "device", ", ".join(serials))
    adb.serial = serials[0]
    try:
        installed = adb.app_installed()
    except Exception as exc:
        _line(_WARN, "com.hscast", str(exc))
        return
    if installed:
        _line(_OK, "com.hscast", "installed on the device")
    else:
        _line(_WARN, "com.hscast", "not installed; build and install android/")


def doctor() -> int:
    print(f"HSCast environment check on {platform.platform()}\n")
    ok = _check_python()
    ok = _check_pyav() and ok
    ok = _check_sdl() and ok
    _check_capture()
    _check_adb()
    print("\nmirror  = phone screen on this PC     desktop = this PC on the phone")
    return 0 if ok else 1
