"""Command line entry point for the Windows side of HSCast."""

from __future__ import annotations

import argparse
import sys

from .util import (
    boost_priority,
    drop_timer_resolution,
    log,
    quiet_ffmpeg,
    raise_timer_resolution,
    set_verbose,
)


def _bitrate(text: str) -> int:
    value = text.strip().lower()
    multiplier = 1
    if value.endswith("k"):
        multiplier, value = 1_000, value[:-1]
    elif value.endswith("m"):
        multiplier, value = 1_000_000, value[:-1]
    try:
        return int(float(value) * multiplier)
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad bitrate {text!r} (try 8M, 12000k, 8000000)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hscast",
        description="Low-latency screen casting between Android and Windows.",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress logging")
    parser.add_argument("--no-boost", action="store_true",
                        help="do not raise process priority / timer resolution")
    parser.add_argument("--ffmpeg-log", action="store_true",
                        help="show libavcodec's own log output (muted by default)")
    sub = parser.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help="launch the modern graphical user interface")
    gui.add_argument("--browser", action="store_true",
                     help="open in default web browser instead of native window")
    gui.add_argument("--port", type=int, default=None, help="HTTP server port")

    mirror = sub.add_parser("mirror", help="show the Android screen on this PC (+ control)")
    mirror.add_argument("--wifi", metavar="HOST", default=None,
                        help="connect to the phone over Wi-Fi at HOST instead of USB")
    mirror.add_argument("--serial", default=None, help="ADB serial, when several devices are attached")
    mirror.add_argument("--video-port", type=int, default=8765)
    mirror.add_argument("--control-port", type=int, default=8766)
    mirror.add_argument("--no-control", action="store_true", help="view only, do not send input")
    mirror.add_argument("--no-hwaccel", action="store_true", help="force software decoding")
    mirror.add_argument("--vsync", action="store_true",
                        help="present on vblank (smoother, adds up to one frame of latency)")
    mirror.add_argument("--no-launch", action="store_true", help="do not auto-start the Android app")
    mirror.add_argument("--record", metavar="FILE", default=None,
                        help="also write the raw elementary stream to FILE (.h264/.hevc)")
    mirror.add_argument("--timeout", type=float, default=120.0, help="connect timeout, seconds")
    mirror.add_argument("--exit-after", type=float, default=0.0, metavar="SECONDS",
                        help="close the window automatically after this long (for testing)")

    desktop = sub.add_parser("desktop", help="send this PC's desktop to the Android app")
    desktop.add_argument("--wifi", action="store_true",
                         help="wait for a Wi-Fi connection instead of setting up an ADB tunnel")
    desktop.add_argument("--serial", default=None, help="ADB serial, when several devices are attached")
    desktop.add_argument("--port", type=int, default=8767)
    desktop.add_argument("--monitor", type=int, default=0, help="monitor index, 0 = primary")
    desktop.add_argument("--fps", type=int, default=60)
    desktop.add_argument("--bitrate", type=_bitrate, default=12_000_000, help="e.g. 8M, 20M")
    desktop.add_argument("--codec", choices=("h264", "hevc"), default="h264")
    desktop.add_argument("--encoder", default=None,
                         help="force a libavcodec encoder, e.g. h264_nvenc, libx264")
    desktop.add_argument("--max-size", type=int, default=1920,
                         help="scale so the longest edge is at most this many pixels (0 = native)")
    desktop.add_argument("--scale-filter", default="AREA",
                         choices=("AREA", "BILINEAR", "FAST_BILINEAR", "BICUBIC", "POINT"),
                         help="downscale filter; AREA keeps small text most readable")
    desktop.add_argument("--capture", choices=("auto", "dxcam", "mss"), default="auto")
    desktop.add_argument("--no-cursor", action="store_true", help="do not composite the mouse pointer")
    desktop.add_argument("--no-launch", action="store_true", help="do not auto-start the Android app")
    desktop.add_argument("--queue", type=int, default=3, help="frames the socket may buffer")
    desktop.add_argument("--timeout", type=float, default=120.0,
                         help="how long to wait for the phone to connect")

    sub.add_parser("doctor", help="check dependencies, codecs and attached devices")
    return parser


def _run_mirror(args) -> int:
    from .mirror_app import MirrorOptions, run_mirror

    return run_mirror(MirrorOptions(
        usb=args.wifi is None,
        host=args.wifi or "127.0.0.1",
        serial=args.serial,
        video_port=args.video_port,
        control_port=args.control_port,
        control=not args.no_control,
        hwaccel=not args.no_hwaccel,
        vsync=args.vsync,
        launch=not args.no_launch,
        connect_timeout=args.timeout,
        record=args.record,
        exit_after=args.exit_after,
    ))


def _run_desktop(args) -> int:
    from .desktop_app import DesktopOptions, run_desktop

    return run_desktop(DesktopOptions(
        usb=not args.wifi,
        serial=args.serial,
        port=args.port,
        monitor=args.monitor,
        fps=args.fps,
        bitrate=args.bitrate,
        codec=args.codec,
        encoder=args.encoder,
        max_size=args.max_size,
        scale_filter=args.scale_filter,
        capture_backend=args.capture,
        cursor=not args.no_cursor,
        launch=not args.no_launch,
        accept_timeout=args.timeout,
        queue_depth=args.queue,
    ))


def _run_doctor(_args) -> int:
    from .doctor import doctor

    return doctor()


def _run_gui(args) -> int:
    from .gui import launch_gui

    return launch_gui(browser_mode=getattr(args, "browser", False),
                      port=getattr(args, "port", None))


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["gui"]

    args = build_parser().parse_args(argv)
    set_verbose(not args.quiet)
    if not args.ffmpeg_log:
        quiet_ffmpeg()

    if not args.no_boost:
        raise_timer_resolution()
        boost_priority()
    try:
        if args.command == "mirror":
            return _run_mirror(args)
        if args.command == "desktop":
            return _run_desktop(args)
        if args.command == "doctor":
            return _run_doctor(args)
        return _run_gui(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log(f"error: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if not args.no_boost:
            drop_timer_resolution()


if __name__ == "__main__":
    sys.exit(main())
