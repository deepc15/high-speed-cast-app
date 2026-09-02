"""Logging, clocks and Windows-specific latency tweaks."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time
from collections import deque

_T0 = time.perf_counter()
_VERBOSE = True


def set_verbose(value: bool) -> None:
    global _VERBOSE
    _VERBOSE = value


def log(msg: str) -> None:
    if _VERBOSE:
        print(f"[{time.perf_counter() - _T0:7.3f}] {msg}", file=sys.stderr, flush=True)


def now_us() -> int:
    return time.perf_counter_ns() // 1000


@contextlib.contextmanager
def ffmpeg_log_silenced():
    """Silence libavcodec entirely for the duration of the block.

    Only for code where a failure is an expected outcome rather than a
    problem -- encoder probing deliberately opens encoders that cannot work on
    this machine, and each one announces itself ("Cannot load nvcuda.dll",
    "DLL amfrt64.dll failed to open"). We report the verdict ourselves.
    """
    try:
        import av.logging
    except Exception:
        yield
        return
    # PyAV dropped QUIET; PANIC is the quietest level every version defines.
    silent = getattr(av.logging, "QUIET", None)
    if silent is None:
        silent = getattr(av.logging, "PANIC", None)
    if silent is None:
        yield
        return
    previous = av.logging.get_level()
    av.logging.set_level(silent)
    try:
        yield
    finally:
        av.logging.set_level(previous)


def quiet_ffmpeg() -> None:
    """Mute libavcodec's own chatter.

    Probing encoders means deliberately opening ones that may fail, and each
    failure prints its own diagnostic ("Cannot load nvcuda.dll", x265's 20-line
    banner). We report the outcome ourselves, so the raw noise is not useful.
    """
    try:
        import av.logging

        av.logging.set_level(av.logging.ERROR)
    except Exception:
        pass


def raise_timer_resolution() -> None:
    """Ask Windows for a 1 ms scheduler tick.

    Without this, any ``sleep``/wait in the pipeline can overshoot by up to
    ~15 ms, which is a whole frame at 60 fps.
    """
    try:
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
    except (OSError, AttributeError):
        pass


def drop_timer_resolution() -> None:
    try:
        ctypes.WinDLL("winmm").timeEndPeriod(1)
    except (OSError, AttributeError):
        pass


def boost_priority() -> None:
    """HIGH_PRIORITY_CLASS keeps capture/encode off the back of the run queue."""
    HIGH_PRIORITY_CLASS = 0x00000080
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
    except (OSError, AttributeError):
        pass


class Meter:
    """Rolling throughput/latency counters, printed on a fixed interval."""

    def __init__(self, name: str, interval: float = 2.0, window: int = 240):
        self.name = name
        self.interval = interval
        self._frames = 0
        self._bytes = 0
        self._dropped = 0
        self._latencies: deque[float] = deque(maxlen=window)
        self._last_report = time.perf_counter()
        self._last_frames = 0
        self._last_bytes = 0

    def frame(self, size: int = 0, latency_ms: float | None = None) -> None:
        self._frames += 1
        self._bytes += size
        if latency_ms is not None:
            self._latencies.append(latency_ms)

    def drop(self, count: int = 1) -> None:
        self._dropped += count

    def maybe_report(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last_report
        if elapsed < self.interval:
            return
        frames = self._frames - self._last_frames
        nbytes = self._bytes - self._last_bytes
        self._last_report = now
        self._last_frames = self._frames
        self._last_bytes = self._bytes

        fps = frames / elapsed
        mbps = nbytes * 8 / elapsed / 1e6
        parts = [f"{self.name}: {fps:5.1f} fps", f"{mbps:6.2f} Mb/s"]
        if self._latencies:
            ordered = sorted(self._latencies)
            p50 = ordered[len(ordered) // 2]
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            parts.append(f"lat p50 {p50:.1f} ms / p95 {p95:.1f} ms")
        if self._dropped:
            parts.append(f"dropped {self._dropped}")
        log("  ".join(parts))
