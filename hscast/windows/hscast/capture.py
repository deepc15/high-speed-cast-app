"""Windows desktop capture.

Prefers ``dxcam`` (DXGI Desktop Duplication — the GPU hands us the already
composited desktop surface, no GDI blit) and falls back to ``mss``.
"""

from __future__ import annotations

import ctypes
import time

import numpy as np

from .util import log


class CaptureError(Exception):
    pass


# --- mouse cursor overlay --------------------------------------------------
# Desktop Duplication delivers the desktop without the pointer, so a plain
# mirror would show no cursor at all. We composite a small arrow ourselves,
# which costs a couple of hundred blended pixels per frame.

_ARROW_ROWS = [
    "X         ", "XX        ", "XOX       ", "XOOX      ", "XOOOX     ",
    "XOOOOX    ", "XOOOOOX   ", "XOOOOOOX  ", "XOOOOOOOX ", "XOOOOOOOOX",
    "XOOOOOOXXX", "XOOOXOOX  ", "XOOX XOOX ", "XOX   XOOX", "XX     XOX",
    "X       XX",
]


def _build_cursor():
    h, w = len(_ARROW_ROWS), max(len(r) for r in _ARROW_ROWS)
    alpha = np.zeros((h, w), dtype=np.float32)
    colour = np.zeros((h, w, 3), dtype=np.float32)
    for y, row in enumerate(_ARROW_ROWS):
        for x, ch in enumerate(row):
            if ch == "X":
                alpha[y, x] = 1.0            # black outline
            elif ch == "O":
                alpha[y, x] = 1.0
                colour[y, x] = 255.0         # white fill
    return colour, alpha[..., None]


_CURSOR_COLOUR, _CURSOR_ALPHA = _build_cursor()


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_position() -> tuple[int, int]:
    point = _Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def draw_cursor(bgra: np.ndarray, origin_x: int = 0, origin_y: int = 0) -> None:
    """Alpha-blend the pointer into a BGRA frame, in place."""
    cx, cy = _cursor_position()
    x0, y0 = cx - origin_x, cy - origin_y
    ch, cw = _CURSOR_ALPHA.shape[:2]
    fh, fw = bgra.shape[:2]
    if x0 >= fw or y0 >= fh or x0 + cw <= 0 or y0 + ch <= 0:
        return
    sx, sy = max(0, -x0), max(0, -y0)
    x0, y0 = max(0, x0), max(0, y0)
    w = min(cw - sx, fw - x0)
    h = min(ch - sy, fh - y0)
    if w <= 0 or h <= 0:
        return
    dst = bgra[y0:y0 + h, x0:x0 + w, :3]
    alpha = _CURSOR_ALPHA[sy:sy + h, sx:sx + w]
    src = _CURSOR_COLOUR[sy:sy + h, sx:sx + w]
    dst[:] = (dst * (1.0 - alpha) + src * alpha).astype(np.uint8)


# --- capture backends ------------------------------------------------------


class DxcamCapture:
    name = "dxcam (DXGI Desktop Duplication)"

    def __init__(self, monitor: int, fps: int, region: tuple[int, int, int, int] | None):
        import dxcam

        self._cam = dxcam.create(output_idx=monitor, output_color="BGRA")
        if self._cam is None:
            raise CaptureError(f"dxcam could not open output {monitor}")
        self._region = region
        self._cam.start(target_fps=fps, video_mode=True, region=region)
        probe = self._cam.get_latest_frame()
        if probe is None:
            raise CaptureError("dxcam produced no frames")
        self.height, self.width = probe.shape[:2]
        self.origin = (region[0], region[1]) if region else (0, 0)
        self._first = probe

    def grab(self) -> np.ndarray | None:
        if self._first is not None:
            frame, self._first = self._first, None
            return frame
        return self._cam.get_latest_frame()

    def close(self) -> None:
        try:
            self._cam.stop()
        except Exception:
            pass
        try:
            self._cam.release()
        except Exception:
            pass


class MssCapture:
    name = "mss (GDI)"

    def __init__(self, monitor: int, fps: int, region: tuple[int, int, int, int] | None):
        import mss

        self._sct = mss.mss()
        monitors = self._sct.monitors
        index = monitor + 1  # monitors[0] is the virtual union of all screens
        if index >= len(monitors):
            raise CaptureError(f"no monitor {monitor} (found {len(monitors) - 1})")
        base = monitors[index]
        if region:
            left, top, right, bottom = region
            self._box = {"left": left, "top": top,
                         "width": right - left, "height": bottom - top}
        else:
            self._box = base
        self.width = self._box["width"]
        self.height = self._box["height"]
        self.origin = (self._box["left"], self._box["top"])
        self._interval = 1.0 / max(fps, 1)
        self._next = time.perf_counter()

    def grab(self) -> np.ndarray | None:
        now = time.perf_counter()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = max(self._next + self._interval, time.perf_counter())
        shot = self._sct.grab(self._box)
        return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(
            shot.height, shot.width, 4
        ).copy()

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass


def open_capture(monitor: int = 0, fps: int = 60,
                 region: tuple[int, int, int, int] | None = None,
                 backend: str = "auto"):
    """Open the fastest available desktop capture backend."""
    errors = []
    order = ("dxcam", "mss") if backend == "auto" else (backend,)
    for name in order:
        cls = {"dxcam": DxcamCapture, "mss": MssCapture}.get(name)
        if cls is None:
            raise CaptureError(f"unknown capture backend {name!r}")
        try:
            capture = cls(monitor, fps, region)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        log(f"capture: {capture.name} {capture.width}x{capture.height} @ {fps} fps")
        return capture
    raise CaptureError("no desktop capture backend available -- " + "; ".join(errors))
