"""Threading primitives that keep latency flat instead of buffering it away."""

from __future__ import annotations

import threading
from collections import deque

from .protocol import Conn
from .util import log


class Mailbox:
    """A one-slot handoff. Writing over an unread value is the point.

    Used between the decode thread and the render thread: if rendering falls
    behind, showing the newest frame is always better than working through a
    backlog of stale ones.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._value = None
        self._closed = False
        self.overwritten = 0

    def put(self, value) -> None:
        with self._cond:
            if self._value is not None:
                self.overwritten += 1
            self._value = value
            self._cond.notify()

    def take(self, timeout: float | None = None):
        with self._cond:
            if self._value is None and not self._closed:
                self._cond.wait(timeout)
            value, self._value = self._value, None
            return value

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class FrameWriter(threading.Thread):
    """Writes encoded frames to a socket without ever blocking the encoder.

    A slow link shows up as dropped frames, not as growing delay. Non-keyframes
    are shed first; when even that is not enough the newest frame is dropped and
    a keyframe is requested so the receiver resynchronises cleanly rather than
    displaying garbage.
    """

    def __init__(self, conn: Conn, max_queue: int = 3, name: str = "video-writer"):
        super().__init__(name=name, daemon=True)
        self.conn = conn
        self.max_queue = max_queue
        self._queue: deque[tuple[int, bytes, bool]] = deque()
        self._cond = threading.Condition()
        self._running = True
        self.dropped = 0
        self.needs_keyframe = False
        self.error: BaseException | None = None

    def submit(self, pts_us: int, data: bytes, keyframe: bool) -> None:
        with self._cond:
            if not self._running:
                return
            if len(self._queue) >= self.max_queue:
                self._shed_locked()
            if len(self._queue) >= self.max_queue:
                self.dropped += 1
                self.needs_keyframe = True
                return
            self._queue.append((pts_us, data, keyframe))
            self._cond.notify()

    def _shed_locked(self) -> None:
        kept = deque(item for item in self._queue if item[2])
        shed = len(self._queue) - len(kept)
        if shed:
            self.dropped += shed
            self.needs_keyframe = True
            self._queue = kept

    def take_keyframe_request(self) -> bool:
        with self._cond:
            wanted, self.needs_keyframe = self.needs_keyframe, False
            return wanted

    def run(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait()
                if not self._queue:
                    return
                pts_us, data, keyframe = self._queue.popleft()
            try:
                self.conn.send_video_frame(pts_us, data, keyframe)
            except OSError as exc:
                self.error = exc
                log(f"video writer stopped: {exc}")
                with self._cond:
                    self._running = False
                    self._queue.clear()
                return

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
