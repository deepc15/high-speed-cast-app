"""``hscast desktop`` -- send the Windows desktop to the Android app.

The PC is the sender here, so it listens and the phone dials in: over USB via
``adb reverse``, over Wi-Fi straight to this machine's LAN address.

Capture and encode run on the main thread; the socket write runs on its own
thread behind a bounded queue, so a stalled link sheds frames instead of
stalling the encoder.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from . import protocol as P
from .capture import draw_cursor, open_capture
from .encoder import Encoder
from .pipeline import FrameWriter
from .transport import (
    DESKTOP_VIDEO_PORT,
    Adb,
    TransportError,
    Tunnels,
    listen_one,
    local_ipv4,
)
from .util import Meter, log, now_us


@dataclass
class DesktopOptions:
    usb: bool = True
    serial: str | None = None
    port: int = DESKTOP_VIDEO_PORT
    monitor: int = 0
    fps: int = 60
    bitrate: int = 12_000_000
    codec: str = "h264"
    encoder: str | None = None
    max_size: int = 1920
    scale_filter: str = "AREA"
    capture_backend: str = "auto"
    cursor: bool = True
    launch: bool = True
    accept_timeout: float = 120.0
    stats_interval: float = 2.0
    queue_depth: int = 3


def _target_size(width: int, height: int, max_size: int) -> tuple[int, int]:
    """Scale so the longest edge fits ``max_size``, keeping even dimensions.

    Encoders need even dimensions for 4:2:0 chroma, and downscaling on the PC
    is far cheaper than making the phone decode more pixels than its panel has.
    """
    longest = max(width, height)
    if max_size <= 0 or longest <= max_size:
        scale = 1.0
    else:
        scale = max_size / longest
    out_w = max(2, int(width * scale) & ~1)
    out_h = max(2, int(height * scale) & ~1)
    return out_w, out_h


class _Requests(threading.Thread):
    """Reads keyframe/bitrate/ping requests the phone sends back up the video socket."""

    def __init__(self, conn: P.Conn):
        super().__init__(name="request-reader", daemon=True)
        self.conn = conn
        self.running = True
        self._lock = threading.Lock()
        self._keyframe = False
        self._bitrate: int | None = None

    def run(self) -> None:
        try:
            while self.running:
                packet = self.conn.read_packet()
                if packet.type == P.P_REQUEST_KEYFRAME:
                    with self._lock:
                        self._keyframe = True
                elif packet.type == P.P_SET_BITRATE:
                    with self._lock:
                        self._bitrate = P.parse_u32(packet.payload)
                elif packet.type == P.P_PING:
                    self.conn.send_pong(P.parse_u64(packet.payload))
        except (OSError, P.ProtocolError, ConnectionError):
            pass
        finally:
            self.running = False

    def take(self) -> tuple[bool, int | None]:
        with self._lock:
            keyframe, self._keyframe = self._keyframe, False
            bitrate, self._bitrate = self._bitrate, None
            return keyframe, bitrate


def _setup_transport(opts: DesktopOptions, tunnels: Tunnels) -> None:
    if not opts.usb:
        log(f"point the Android app at  {local_ipv4()}:{opts.port}  (Wi-Fi mode)")
        return

    adb = Adb(opts.serial)
    serial = adb.require_device()
    tunnels.adb = adb
    log(f"usb device: {serial}")

    # Mode validation for Desktop Receiver mode: if USB is selected on PC, verify Android app is not set to Wi-Fi
    try:
        pref_out = adb.run(
            "shell", "run-as", "com.hscast", "cat", "/data/data/com.hscast/shared_prefs/hscast.xml",
            check=False, timeout=1.5,
        )
        match = re.search(r'name="receiver_mode_type"[^>]*>([^<]+)<', pref_out)
        if match and match.group(1).strip() == "wifi":
            try:
                adb.run(
                    "shell", "am", "broadcast", "-a", "com.hscast.VALIDATION_ERROR",
                    "--es", "message", "Please select USB option in Android to proceed",
                    check=False, timeout=1.0,
                )
            except Exception:
                pass
            raise TransportError("Please select USB option in Android to proceed")
    except TransportError:
        raise
    except Exception:
        pass

    tunnels.reverse(opts.port, opts.port)
    if opts.launch:
        if not adb.app_installed():
            log("warning: com.hscast is not installed on the device")
        try:
            adb.launch_app({"mode": "recv", "host": "127.0.0.1", "port": str(opts.port)})
            log("launched com.hscast in receive mode")
        except Exception as exc:
            log(f"could not auto-launch the app ({exc}); start it by hand")


def run_desktop(opts: DesktopOptions) -> int:
    with Tunnels() as tunnels:
        try:
            _setup_transport(opts, tunnels)
        except TransportError as exc:
            msg = str(exc)
            if "Please select" in msg:
                log(f"validation failed: {msg}")
                return 2
            raise

        conn_mode = "usb" if opts.usb else "wifi"
        try:
            conn = listen_one(opts.port, P.CH_VIDEO, P.ROLE_SENDER,
                              timeout=opts.accept_timeout, conn_mode=conn_mode)
            return _session(opts, conn)
        except TransportError as exc:
            msg = str(exc)
            if "Please select" in msg:
                log(f"validation failed: {msg}")
                return 2
            raise
        finally:
            if 'conn' in locals() and conn:
                conn.close()


def _session(opts: DesktopOptions, conn: P.Conn) -> int:
    capture = open_capture(opts.monitor, opts.fps, None, opts.capture_backend)
    out_w, out_h = _target_size(capture.width, capture.height, opts.max_size)
    encoder = Encoder(out_w, out_h, opts.fps, opts.bitrate, opts.codec, opts.encoder,
                      scale_filter=opts.scale_filter)

    def announce() -> None:
        conn.send_stream_info(P.StreamInfo(
            codec=P.CODEC_IDS.get(opts.codec, P.CODEC_H264),
            width=encoder.width,
            height=encoder.height,
            fps=opts.fps,
            bitrate=encoder.bitrate,
            extra=encoder.extradata,
        ))

    announce()
    writer = FrameWriter(conn, max_queue=opts.queue_depth)
    writer.start()
    requests = _Requests(conn)
    requests.start()

    meter = Meter("send", interval=opts.stats_interval)
    origin_x, origin_y = capture.origin
    force_key = True  # first frame must be an IDR
    t0 = now_us()
    reported_drops = 0

    try:
        while writer.error is None and requests.running:
            frame = capture.grab()
            if frame is None:
                continue
            if opts.cursor:
                draw_cursor(frame, origin_x, origin_y)

            wanted_key, new_bitrate = requests.take()
            if writer.take_keyframe_request():
                wanted_key = True
            if new_bitrate is not None:
                encoder.set_bitrate(new_bitrate)
                announce()
                wanted_key = True
            force_key = force_key or wanted_key

            pts_us = now_us() - t0
            encode_started = now_us()
            packets = encoder.encode(frame, pts_us, force_key)
            force_key = False
            encode_ms = (now_us() - encode_started) / 1000.0

            for pts, data, keyframe in packets:
                writer.submit(pts, data, keyframe)
                meter.frame(len(data), encode_ms)
            if writer.dropped != reported_drops:
                meter.drop(writer.dropped - reported_drops)
                reported_drops = writer.dropped
            meter.maybe_report()
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        for pts, data, keyframe in encoder.flush():
            writer.submit(pts, data, keyframe)
        requests.running = False
        writer.stop()
        writer.join(timeout=1.0)
        encoder.close()
        capture.close()

    if writer.dropped:
        log(f"dropped {writer.dropped} frames to keep latency down")
    return 0
