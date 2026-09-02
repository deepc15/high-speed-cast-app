"""Stand-in for the Android app, so the Windows side can be tested without a phone.

    python tools\\fake_android.py sender      # pretend to be a casting phone
    python tools\\fake_android.py receiver    # pretend to be a phone showing the desktop

``sender`` listens on the mirror ports, encodes a synthetic animation and prints
every control event it receives, so you can drive it with:

    python -m hscast mirror --wifi 127.0.0.1

``receiver`` dials the desktop sender and reports decode statistics, so you can
check the other direction with:

    python -m hscast desktop --wifi
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hscast import protocol as P  # noqa: E402
from hscast.decoder import Decoder  # noqa: E402
from hscast.encoder import Encoder  # noqa: E402
from hscast.transport import connect, listen_one  # noqa: E402
from hscast.util import Meter, log, now_us  # noqa: E402

WIDTH, HEIGHT, FPS = 720, 1280, 60  # portrait, like a phone


def _pattern(width: int, height: int, tick: int) -> np.ndarray:
    """A moving block plus a gradient: compressible, but never static."""
    frame = np.empty((height, width, 4), dtype=np.uint8)
    frame[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    frame[:, :, 1] = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
    frame[:, :, 2] = (tick * 3) % 256
    frame[:, :, 3] = 255
    size = 120
    x = int((width - size) * (0.5 + 0.5 * np.sin(tick / 40.0)))
    y = int((height - size) * (0.5 + 0.5 * np.cos(tick / 57.0)))
    frame[y:y + size, x:x + size, :3] = 255
    return frame


def _control_server(port: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn = listen_one(port, P.CH_CONTROL, P.ROLE_RECEIVER, timeout=5.0)
        except Exception:
            continue
        log("control channel connected")
        try:
            while not stop.is_set():
                packet = conn.read_packet()
                if packet.type == P.P_TOUCH:
                    action, pointer, x, y, pressure = P._TOUCH.unpack(packet.payload)
                    names = {0: "down", 1: "up", 2: "move", 3: "cancel"}
                    log(f"touch {names.get(action, action)} at "
                        f"({x * 100 // 65535}%, {y * 100 // 65535}%)")
                elif packet.type == P.P_KEY:
                    action, keycode, meta = P._KEY.unpack(packet.payload)
                    log(f"key {'down' if action == 0 else 'up'} keycode={keycode} meta={meta}")
                elif packet.type == P.P_TEXT:
                    log(f"text {packet.payload.decode('utf-8', 'replace')!r}")
                elif packet.type == P.P_SCROLL:
                    x, y, hscroll, vscroll = P._SCROLL.unpack(packet.payload)
                    log(f"scroll h={hscroll / 256:+.2f} v={vscroll / 256:+.2f}")
                elif packet.type == P.P_ACTION:
                    log(f"global action {packet.payload[0]}")
                elif packet.type == P.P_REQUEST_KEYFRAME:
                    log("keyframe requested")
                elif packet.type == P.P_SET_BITRATE:
                    log(f"bitrate requested: {P.parse_u32(packet.payload) / 1e6:.1f} Mb/s")
                elif packet.type == P.P_PING:
                    conn.send_pong(P.parse_u64(packet.payload))
        except Exception as exc:
            log(f"control channel closed: {exc}")
        finally:
            conn.close()


def run_sender(args) -> int:
    stop = threading.Event()
    control = threading.Thread(
        target=_control_server, args=(args.control_port, stop), daemon=True
    )
    control.start()

    conn = listen_one(args.video_port, P.CH_VIDEO, P.ROLE_SENDER, timeout=args.timeout)
    encoder = Encoder(WIDTH, HEIGHT, FPS, args.bitrate, "h264", args.encoder)
    conn.send_stream_info(P.StreamInfo(
        P.CODEC_H264, encoder.width, encoder.height, FPS, encoder.bitrate,
        encoder.extradata,
    ))

    meter = Meter("fake-phone")
    interval = 1.0 / FPS
    start = time.perf_counter()
    tick = 0
    try:
        while True:
            target = start + tick * interval
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            frame = _pattern(WIDTH, HEIGHT, tick)
            for pts, data, keyframe in encoder.encode(frame, now_us(), tick == 0):
                conn.send_video_frame(pts, data, keyframe)
                meter.frame(len(data))
            meter.maybe_report()
            tick += 1
            if args.frames and tick >= args.frames:
                log(f"sent {tick} frames, done")
                return 0
    except (KeyboardInterrupt, ConnectionError, OSError) as exc:
        log(f"sender stopped: {exc}")
        return 0
    finally:
        stop.set()
        encoder.close()
        conn.close()


def run_receiver(args) -> int:
    conn = connect(args.host, args.port, P.CH_VIDEO, P.ROLE_RECEIVER, timeout=args.timeout)
    decoder: Decoder | None = None
    meter = Meter("fake-phone-recv")
    decoded = 0
    try:
        while True:
            packet = conn.read_packet()
            if packet.type == P.P_STREAM_INFO:
                info = P.StreamInfo.unpack(packet.payload)
                log(f"stream {info.codec_name} {info.width}x{info.height} @ {info.fps} fps, "
                    f"{info.bitrate / 1e6:.1f} Mb/s, csd {len(info.extra)} B")
                decoder = Decoder(info.codec_name, info.extra, hwaccel=not args.no_hwaccel)
            elif packet.type == P.P_VIDEO_CONFIG and decoder is not None:
                log(f"codec config, {len(packet.payload)} B")
            elif packet.type == P.P_VIDEO_FRAME and decoder is not None:
                _pts, keyframe, unit = P.parse_video_frame(packet.payload)
                frames = decoder.decode(bytes(unit))
                decoded += len(frames)
                meter.frame(len(packet.payload))
                if keyframe and decoded <= 1:
                    log(f"first keyframe decoded: {frames[0].width}x{frames[0].height} "
                        f"{frames[0].format.name}" if frames else "keyframe produced no frame")
                meter.maybe_report()
                if args.frames and decoded >= args.frames:
                    log(f"decoded {decoded} frames, done")
                    return 0
    except (KeyboardInterrupt, ConnectionError, OSError) as exc:
        log(f"receiver stopped after {decoded} decoded frames: {exc}")
        return 0 if decoded else 1
    finally:
        if decoder:
            decoder.close()
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    sender = sub.add_parser("sender", help="act as a casting phone")
    sender.add_argument("--video-port", type=int, default=8765)
    sender.add_argument("--control-port", type=int, default=8766)
    sender.add_argument("--bitrate", type=int, default=6_000_000)
    sender.add_argument("--encoder", default=None)
    sender.add_argument("--frames", type=int, default=0, help="stop after N frames")
    sender.add_argument("--timeout", type=float, default=120.0)

    receiver = sub.add_parser("receiver", help="act as a phone showing the desktop")
    receiver.add_argument("--host", default="127.0.0.1")
    receiver.add_argument("--port", type=int, default=8767)
    receiver.add_argument("--frames", type=int, default=0, help="stop after N frames")
    receiver.add_argument("--no-hwaccel", action="store_true")
    receiver.add_argument("--timeout", type=float, default=60.0)

    args = parser.parse_args()
    return run_sender(args) if args.mode == "sender" else run_receiver(args)


if __name__ == "__main__":
    sys.exit(main())
