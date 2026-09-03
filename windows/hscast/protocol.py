"""HSCast wire protocol v1. See PROTOCOL.md for the byte layout."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

MAGIC = b"HSC1"
VERSION = 1

CH_VIDEO = 1
CH_CONTROL = 2

ROLE_SENDER = 1
ROLE_RECEIVER = 2

CODEC_H264 = 1
CODEC_HEVC = 2
CODEC_NAMES = {CODEC_H264: "h264", CODEC_HEVC: "hevc"}
CODEC_IDS = {v: k for k, v in CODEC_NAMES.items()}

P_STREAM_INFO = 0x01
P_VIDEO_CONFIG = 0x02
P_VIDEO_FRAME = 0x03

P_TOUCH = 0x10
P_KEY = 0x11
P_TEXT = 0x12
P_SCROLL = 0x13
P_ACTION = 0x14

P_REQUEST_KEYFRAME = 0x20
P_SET_BITRATE = 0x21
P_PING = 0x30
P_PONG = 0x31

FLAG_KEYFRAME = 0x01
FLAG_CONFIG = 0x02

TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE, TOUCH_CANCEL = 0, 1, 2, 3
KEY_DOWN, KEY_UP = 0, 1

ACTION_BACK = 1
ACTION_HOME = 2
ACTION_RECENTS = 3
ACTION_NOTIFICATIONS = 4
ACTION_POWER = 5
ACTION_WAKE = 6

COORD_MAX = 65535

_HANDSHAKE = struct.Struct("!4sBBBB")
_HEADER = struct.Struct("!BBI")
_STREAM_INFO = struct.Struct("!BHHHIH")
_FRAME_HEAD = struct.Struct("!QB")
_TOUCH = struct.Struct("!BBHHH")
_KEY = struct.Struct("!BII")
_SCROLL = struct.Struct("!HHhh")
_U32 = struct.Struct("!I")
_U64 = struct.Struct("!Q")

MAX_PAYLOAD = 32 << 20  # a 1080p IDR is ~200 KB; anything near this is corruption


class ProtocolError(Exception):
    pass


@dataclass(slots=True)
class StreamInfo:
    codec: int
    width: int
    height: int
    fps: int
    bitrate: int
    extra: bytes = b""

    @property
    def codec_name(self) -> str:
        return CODEC_NAMES.get(self.codec, "h264")

    def pack(self) -> bytes:
        return _STREAM_INFO.pack(
            self.codec, self.width, self.height, self.fps, self.bitrate, len(self.extra)
        ) + self.extra

    @classmethod
    def unpack(cls, payload: bytes) -> "StreamInfo":
        size = _STREAM_INFO.size
        if len(payload) < size:
            raise ProtocolError("short STREAM_INFO")
        codec, w, h, fps, bitrate, extra_len = _STREAM_INFO.unpack_from(payload)
        return cls(codec, w, h, fps, bitrate, bytes(payload[size:size + extra_len]))


@dataclass(slots=True)
class Packet:
    type: int
    flags: int
    payload: bytes


FLAG_MODE_UNSPECIFIED = 0x00
FLAG_MODE_WIFI = 0x01
FLAG_MODE_USB = 0x02
FLAG_ERROR_PLEASE_SELECT_USB = 0x41
FLAG_ERROR_PLEASE_SELECT_WIFI = 0x42
FLAG_CANCELLED = 0x80


class ModeMismatchError(ProtocolError):
    """Raised when the Android app and Windows app have mismatched connection modes (USB vs Wi-Fi)."""
    pass


class Conn:
    """Framed packet transport over one TCP socket.

    Reads go through a single reusable buffer so a 200 KB keyframe costs one
    ``recv_into`` per TCP segment and one slice, not a chain of concatenations.
    """

    def __init__(self, sock: socket.socket, channel: int, role: int):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = sock
        self.channel = channel
        self.role = role
        self._buf = bytearray(1 << 20)
        self._view = memoryview(self._buf)

    # -- setup ---------------------------------------------------------------

    def handshake(self, mode: int = FLAG_MODE_UNSPECIFIED) -> None:
        self.sock.sendall(_HANDSHAKE.pack(MAGIC, VERSION, self.channel, self.role, mode))
        raw = self._read_exact(_HANDSHAKE.size)
        magic, version, channel, role, flags = _HANDSHAKE.unpack(raw)
        if flags == FLAG_ERROR_PLEASE_SELECT_USB:
            raise ModeMismatchError("Please select USB option in Android to proceed")
        if flags == FLAG_ERROR_PLEASE_SELECT_WIFI:
            raise ModeMismatchError("Please select Wi-Fi option in Android to proceed")
        if flags & FLAG_CANCELLED:
            raise ProtocolError("Screen capture was cancelled on the Android device.")

        # Check mode mismatch if mode is specified and peer announced its mode
        peer_mode = flags & 0x03
        if mode != FLAG_MODE_UNSPECIFIED and peer_mode != FLAG_MODE_UNSPECIFIED:
            if mode == FLAG_MODE_USB and peer_mode == FLAG_MODE_WIFI:
                raise ModeMismatchError("Please select USB option in Android to proceed")
            if mode == FLAG_MODE_WIFI and peer_mode == FLAG_MODE_USB:
                raise ModeMismatchError("Please select Wi-Fi option in Android to proceed")

        if magic != MAGIC:
            raise ProtocolError(f"not an HSCast peer (magic {magic!r})")
        if version != VERSION:
            raise ProtocolError(f"peer speaks protocol v{version}, we speak v{VERSION}")
        if channel != self.channel:
            raise ProtocolError(f"peer opened channel {channel}, expected {self.channel}")
        if role == self.role:
            raise ProtocolError("both peers claim the same role")

    # -- reading -------------------------------------------------------------

    def _read_exact(self, n: int) -> memoryview:
        if len(self._buf) < n:
            self._buf = bytearray(n)
            self._view = memoryview(self._buf)
        got = 0
        while got < n:
            read = self.sock.recv_into(self._view[got:n], n - got)
            if read == 0:
                raise ConnectionError("peer closed the connection")
            got += read
        return self._view[:n]

    def read_packet(self) -> Packet:
        ptype, flags, length = _HEADER.unpack(self._read_exact(_HEADER.size))
        if length > MAX_PAYLOAD:
            raise ProtocolError(f"payload of {length} bytes is out of range")
        payload = bytes(self._read_exact(length)) if length else b""
        return Packet(ptype, flags, payload)

    # -- writing -------------------------------------------------------------

    def send(self, ptype: int, payload: bytes = b"", flags: int = 0) -> None:
        header = _HEADER.pack(ptype, flags, len(payload))
        # One sendall of a concatenated buffer beats two syscalls with NODELAY
        # on: it keeps header and payload in the same segment.
        self.sock.sendall(header + payload if payload else header)

    def send_stream_info(self, info: StreamInfo) -> None:
        self.send(P_STREAM_INFO, info.pack())

    def send_video_config(self, extra: bytes) -> None:
        self.send(P_VIDEO_CONFIG, extra, FLAG_CONFIG)

    def send_video_frame(self, pts_us: int, data: bytes, keyframe: bool) -> None:
        self.send(
            P_VIDEO_FRAME,
            _FRAME_HEAD.pack(pts_us, 1 if keyframe else 0) + data,
            FLAG_KEYFRAME if keyframe else 0,
        )

    def send_touch(self, action: int, pointer_id: int, x: int, y: int, pressure: int = COORD_MAX) -> None:
        self.send(P_TOUCH, _TOUCH.pack(action, pointer_id, x, y, pressure))

    def send_key(self, action: int, keycode: int, meta: int = 0) -> None:
        self.send(P_KEY, _KEY.pack(action, keycode, meta))

    def send_text(self, text: str) -> None:
        self.send(P_TEXT, text.encode("utf-8"))

    def send_scroll(self, x: int, y: int, hscroll: int, vscroll: int) -> None:
        self.send(P_SCROLL, _SCROLL.pack(x, y, hscroll, vscroll))

    def send_action(self, action_id: int) -> None:
        self.send(P_ACTION, bytes([action_id]))

    def send_request_keyframe(self) -> None:
        self.send(P_REQUEST_KEYFRAME)

    def send_set_bitrate(self, bps: int) -> None:
        self.send(P_SET_BITRATE, _U32.pack(bps))

    def send_ping(self, t_us: int) -> None:
        self.send(P_PING, _U64.pack(t_us))

    def send_pong(self, t_us: int) -> None:
        self.send(P_PONG, _U64.pack(t_us))

    # -- teardown ------------------------------------------------------------

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "Conn":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def parse_video_frame(payload: bytes) -> tuple[int, bool, memoryview]:
    """Split a VIDEO_FRAME payload into ``(pts_us, keyframe, access_unit)``."""
    if len(payload) < _FRAME_HEAD.size:
        raise ProtocolError("short VIDEO_FRAME")
    pts_us, keyframe = _FRAME_HEAD.unpack_from(payload)
    return pts_us, bool(keyframe), memoryview(payload)[_FRAME_HEAD.size:]


def parse_u32(payload: bytes) -> int:
    if len(payload) < 4:
        raise ProtocolError("short u32 payload")
    return _U32.unpack_from(payload)[0]


def parse_u64(payload: bytes) -> int:
    if len(payload) < 8:
        raise ProtocolError("short u64 payload")
    return _U64.unpack_from(payload)[0]
