"""``hscast mirror`` -- show the Android screen on Windows, with input control.

Threading model:

* reader thread  -- socket read, decode, hand the newest frame to a mailbox
* control thread -- reads PONGs so we can report real round-trip latency
* main thread    -- owns the SDL window: polls input every ~2 ms and draws

Input is handled on its own tight loop rather than once per video frame, so a
click is on the wire in single-digit milliseconds even at 30 fps.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from . import protocol as P
from .control import HOTKEY_HELP, ControlSender
from .decoder import Decoder
from .pipeline import Mailbox
from .renderer import Renderer
from .transport import (
    MIRROR_CONTROL_PORT,
    MIRROR_VIDEO_PORT,
    Adb,
    Tunnels,
    connect,
)
from .util import Meter, log, now_us


@dataclass
class MirrorOptions:
    usb: bool = True
    host: str = "127.0.0.1"
    serial: str | None = None
    video_port: int = MIRROR_VIDEO_PORT
    control_port: int = MIRROR_CONTROL_PORT
    control: bool = True
    hwaccel: bool = True
    vsync: bool = False
    launch: bool = True
    connect_timeout: float = 60.0
    record: str | None = None
    stats_interval: float = 2.0
    exit_after: float = 0.0
    extra_launch: dict[str, str] = field(default_factory=dict)


class _Reader(threading.Thread):
    def __init__(self, conn: P.Conn, mailbox: Mailbox, hwaccel: bool,
                 control: P.Conn | None, record_path: str | None,
                 info: P.StreamInfo):
        super().__init__(name="video-reader", daemon=True)
        self.conn = conn
        self.mailbox = mailbox
        self.hwaccel = hwaccel
        self.control = control
        self.info = info
        self.error: BaseException | None = None
        self.running = True
        self.meter = Meter("recv")
        self._decoder: Decoder | None = None
        self._recording = open(record_path, "wb") if record_path else None
        self._pending_keyframe_request = False

    def _new_decoder(self, info: P.StreamInfo) -> None:
        if self._decoder is not None:
            self._decoder.close()
        self.info = info
        self._decoder = Decoder(info.codec_name, info.extra, self.hwaccel)
        if self._recording and info.extra:
            self._recording.write(info.extra)

    def _request_keyframe(self) -> None:
        if self.control is None:
            return
        try:
            self.control.send_request_keyframe()
        except OSError:
            pass

    def run(self) -> None:
        try:
            self._new_decoder(self.info)
            while self.running:
                packet = self.conn.read_packet()
                if packet.type == P.P_VIDEO_FRAME:
                    self._on_frame(packet)
                elif packet.type == P.P_STREAM_INFO:
                    info = P.StreamInfo.unpack(packet.payload)
                    log(f"stream: {info.codec_name} {info.width}x{info.height} "
                        f"@ {info.fps} fps, {info.bitrate / 1e6:.1f} Mb/s")
                    self._new_decoder(info)
                elif packet.type == P.P_VIDEO_CONFIG:
                    # Encoder restarted (rotation, bitrate change): rebuild with
                    # the new SPS/PPS but keep the announced geometry.
                    self.info.extra = packet.payload
                    self._new_decoder(self.info)
                elif packet.type == P.P_PING:
                    try:
                        self.conn.send_pong(P.parse_u64(packet.payload))
                    except OSError:
                        pass
        except BaseException as exc:  # surfaced to the main loop
            self.error = exc
        finally:
            self.running = False
            self.mailbox.close()
            if self._decoder:
                self._decoder.close()
            if self._recording:
                self._recording.close()

    def _on_frame(self, packet: P.Packet) -> None:
        assert self._decoder is not None
        _pts_us, _keyframe, access_unit = P.parse_video_frame(packet.payload)
        data = bytes(access_unit)
        if self._recording:
            self._recording.write(data)
        before = self._decoder.corrupt_frames
        frames = self._decoder.decode(data)
        if self._decoder.corrupt_frames != before:
            self._request_keyframe()
        self.meter.frame(len(data))
        for frame in frames:
            self.mailbox.put(frame)


class _PongReader(threading.Thread):
    """Reads the control socket so PING/PONG gives us a real latency figure."""

    def __init__(self, conn: P.Conn):
        super().__init__(name="control-reader", daemon=True)
        self.conn = conn
        self.rtt_ms: float | None = None
        self.running = True

    def run(self) -> None:
        try:
            while self.running:
                packet = self.conn.read_packet()
                if packet.type == P.P_PONG:
                    sent = P.parse_u64(packet.payload)
                    self.rtt_ms = (now_us() - sent) / 1000.0
                elif packet.type == P.P_PING:
                    self.conn.send_pong(P.parse_u64(packet.payload))
        except (OSError, P.ProtocolError, ConnectionError):
            self.running = False


def _setup_transport(opts: MirrorOptions, tunnels: Tunnels) -> tuple[str, int, int]:
    if not opts.usb:
        return opts.host, opts.video_port, opts.control_port

    adb = Adb(opts.serial)
    serial = adb.require_device()
    tunnels.adb = adb
    log(f"usb device: {serial}")
    if not adb.app_installed():
        log("warning: com.hscast is not installed on the device -- "
            "build android/ and install it, or start the sender manually")
    tunnels.forward(opts.video_port, MIRROR_VIDEO_PORT)
    tunnels.forward(opts.control_port, MIRROR_CONTROL_PORT)
    if opts.launch:
        extras = {"mode": "send"} | opts.extra_launch
        try:
            adb.launch_app(extras)
            log("launched com.hscast on the device (accept the capture prompt)")
        except Exception as exc:
            log(f"could not auto-launch the app ({exc}); start it by hand")
    return "127.0.0.1", opts.video_port, opts.control_port


def run_mirror(opts: MirrorOptions) -> int:
    with Tunnels() as tunnels:
        host, video_port, control_port = _setup_transport(opts, tunnels)

        video = connect(host, video_port, P.CH_VIDEO, P.ROLE_RECEIVER,
                        timeout=opts.connect_timeout)
        control = None
        if opts.control:
            try:
                control = connect(host, control_port, P.CH_CONTROL, P.ROLE_SENDER,
                                  timeout=15.0)
            except Exception as exc:
                log(f"control channel unavailable ({exc}); display-only")

        try:
            return _session(opts, video, control)
        finally:
            video.close()
            if control:
                control.close()


def _session(opts: MirrorOptions, video: P.Conn, control: P.Conn | None) -> int:
    packet = video.read_packet()
    while packet.type != P.P_STREAM_INFO:
        packet = video.read_packet()
    info = P.StreamInfo.unpack(packet.payload)
    log(f"stream: {info.codec_name} {info.width}x{info.height} @ {info.fps} fps, "
        f"{info.bitrate / 1e6:.1f} Mb/s")

    mailbox = Mailbox()
    reader = _Reader(video, mailbox, opts.hwaccel, control, opts.record, info)
    pong = _PongReader(control) if control else None

    with Renderer("HSCast - Android", info.width, info.height, opts.vsync) as renderer:
        sender = ControlSender(control, renderer, info.bitrate)
        if control:
            log("control enabled:\n" + HOTKEY_HELP)
        reader.start()
        if pong:
            pong.start()

        meter = Meter("display", interval=opts.stats_interval)
        last_ping = 0
        deadline = now_us() + int(opts.exit_after * 1e6) if opts.exit_after else 0
        try:
            while not renderer.closed and not sender.quit_requested and reader.running:
                if deadline and now_us() > deadline:
                    log(f"--exit-after {opts.exit_after:g}s reached")
                    break
                sender.handle_all(renderer.poll_events())

                frame = mailbox.take(0.002)
                if frame is not None:
                    renderer.draw(frame)
                    meter.frame()

                now = now_us()
                if control and now - last_ping > 1_000_000:
                    last_ping = now
                    try:
                        control.send_ping(now)
                    except OSError:
                        pass
                    # Retitling is an X/Win32 round trip, so do it on the ping
                    # tick rather than on every pass of a ~500 Hz loop.
                    if pong and pong.rtt_ms is not None:
                        renderer.set_title(
                            f"HSCast - Android  {info.width}x{info.height}  "
                            f"rtt {pong.rtt_ms:.1f} ms"
                        )
                meter.maybe_report()
                reader.meter.maybe_report()
        except KeyboardInterrupt:
            log("interrupted")
        finally:
            reader.running = False
            if pong:
                pong.running = False

    if reader.error and not isinstance(reader.error, (ConnectionError, OSError)):
        log(f"reader failed: {type(reader.error).__name__}: {reader.error}")
        return 1
    if mailbox.overwritten:
        log(f"{mailbox.overwritten} frames were superseded by a newer one "
            "before they could be drawn")
    return 0
