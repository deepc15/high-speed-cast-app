"""Hardware-first H.264/HEVC encoding of captured desktop frames.

Encoder options here all aim at one thing: never let the encoder buffer a
frame. No B-frames, no lookahead, no rate-control queue. That trades a little
compression efficiency for a pipeline where a frame that goes in comes out.
"""

from __future__ import annotations

from fractions import Fraction

import av
import numpy as np

from .util import ffmpeg_log_silenced, log

try:
    from av.video.reformatter import VideoReformatter
except ImportError:  # pragma: no cover - very old PyAV
    VideoReformatter = None

# name -> (pixel format the encoder wants, low-latency options)
_ENCODERS = {
    "h264_nvenc": ("nv12", {
        "preset": "p1",            # fastest NVENC preset
        "tune": "ull",             # ultra low latency
        "zerolatency": "1",
        "rc": "cbr",
        "delay": "0",
        "bf": "0",
        "forced-idr": "1",
    }),
    "hevc_nvenc": ("nv12", {
        "preset": "p1", "tune": "ull", "zerolatency": "1",
        "rc": "cbr", "delay": "0", "bf": "0", "forced-idr": "1",
    }),
    "h264_qsv": ("nv12", {
        "preset": "veryfast",
        "async_depth": "1",        # no frames in flight inside QSV
        "look_ahead": "0",
        "low_delay_brc": "1",
        "bf": "0",
    }),
    "hevc_qsv": ("nv12", {
        "preset": "veryfast", "async_depth": "1", "low_delay_brc": "1", "bf": "0",
    }),
    "h264_amf": ("nv12", {
        "usage": "ultralowlatency",
        "quality": "speed",
        "rc": "cbr",
        "bf": "0",
    }),
    "hevc_amf": ("nv12", {
        "usage": "ultralowlatency", "quality": "speed", "rc": "cbr",
    }),
    "libx264": ("yuv420p", {
        "preset": "ultrafast",
        "tune": "zerolatency",
        "x264-params": "sliced-threads=1:sync-lookahead=0:rc-lookahead=0:bframes=0",
    }),
    "libx265": ("yuv420p", {
        "preset": "ultrafast",
        "tune": "zerolatency",
        # log-level=none: x265 otherwise writes a 20-line banner to stderr
        # every time a context is opened, including during encoder probing.
        "x265-params": "bframes=0:rc-lookahead=0:frame-threads=1:log-level=none",
    }),
}

_H264_ORDER = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
_HEVC_ORDER = ("hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265")


def _picture_type_i():
    """The value PyAV wants for "encode this as an IDR".

    Older releases accepted the string ``"I"``; PyAV 13+ wants the enum, which
    is why this is resolved once at import instead of guessed per frame.
    """
    try:
        from av.video.frame import PictureType

        return PictureType.I
    except Exception:  # pragma: no cover - depends on PyAV version
        return 1  # AV_PICTURE_TYPE_I


_PICTURE_TYPE_I = _picture_type_i()


class EncoderError(Exception):
    pass


def _available(name: str) -> bool:
    try:
        av.codec.Codec(name, "w")
        return True
    except Exception:
        return False


def encoder_candidates(codec: str = "h264", prefer: str | None = None) -> list[str]:
    """Encoders to try, best first.

    Being registered in FFmpeg is not the same as being usable: h264_nvenc is
    present in every full build but fails to open without an NVIDIA GPU, so the
    real selection happens by attempting an open (see ``Encoder._open``).
    """
    if prefer:
        if not _available(prefer):
            raise EncoderError(f"encoder {prefer!r} is not in this FFmpeg build")
        return [prefer]
    order = _HEVC_ORDER if codec == "hevc" else _H264_ORDER
    candidates = [name for name in order if _available(name)]
    if not candidates:
        raise EncoderError(f"no {codec} encoder available in this FFmpeg build")
    return candidates


def _configure(ctx, name: str, width: int, height: int, fps: int, bitrate: int,
               gop_size: int) -> None:
    """Apply every setting an encoder needs, in one place.

    Probing and real encoding must configure identically. They used to differ,
    and h264_qsv refuses to open unless ``framerate`` is set ("Current frame
    rate is unsupported"), so the probe reported no GPU encoder on a machine
    where encoding worked fine.
    """
    pix_fmt, options = _ENCODERS[name]
    ctx.width = width
    ctx.height = height
    ctx.pix_fmt = pix_fmt
    ctx.bit_rate = bitrate
    # Microsecond time base: our pts come straight from a monotonic clock, so
    # the receiver's latency maths needs no rescaling.
    ctx.time_base = Fraction(1, 1_000_000)
    ctx.framerate = Fraction(fps, 1)
    ctx.gop_size = gop_size
    ctx.max_b_frames = 0
    ctx.options = dict(options)


def _release(ctx) -> None:
    """PyAV 13+ dropped CodecContext.close(); dropping the reference frees it."""
    if ctx is None:
        return
    closer = getattr(ctx, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def probe_encoder(name: str, width: int = 1280, height: int = 720,
                  fps: int = 60) -> bool:
    """True when ``name`` can actually be opened on this machine."""
    if name not in _ENCODERS or not _available(name):
        return False
    ctx = None
    with ffmpeg_log_silenced():
        try:
            ctx = av.CodecContext.create(name, "w")
            _configure(ctx, name, width, height, fps, 8_000_000, fps * 4)
            ctx.open()
        except Exception:
            return False
        finally:
            _release(ctx)
    return True


class Encoder:
    """Wraps one libavcodec encoder plus the BGRA -> NV12/YUV420P conversion.

    Rate control cannot be retuned in place for most encoders, so a bitrate
    change rebuilds the encoder and re-announces the stream.
    """

    def __init__(self, width: int, height: int, fps: int, bitrate: int,
                 codec: str = "h264", encoder_name: str | None = None,
                 gop_seconds: float = 4.0, scale_filter: str = "AREA"):
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.fps = fps
        self.bitrate = bitrate
        self.scale_filter = scale_filter
        # One reformatter for the whole session. VideoFrame.reformat() builds a
        # fresh SwsContext per call, and initialising the scaler costs an order
        # of magnitude more than running it: 20 ms a frame versus 1.5 ms.
        self._reformatter = VideoReformatter() if VideoReformatter else None
        self.codec = codec
        self.gop_seconds = gop_seconds
        self._candidates = encoder_candidates(codec, encoder_name)
        self.name = self._candidates[0]
        self.pix_fmt = _ENCODERS[self.name][0]
        self.ctx = None
        self._open()

    def _open(self) -> None:
        failures = []
        gop_size = max(1, int(self.fps * self.gop_seconds))
        for name in self._candidates:
            pix_fmt, options = _ENCODERS[name]
            ctx = av.CodecContext.create(name, "w")
            _configure(ctx, name, self.width, self.height, self.fps,
                       self.bitrate, gop_size)
            try:
                # Silenced: falling through the candidate list is the normal
                # path, and each unusable encoder logs its own complaint.
                with ffmpeg_log_silenced():
                    ctx.open()
            except Exception as exc:
                # Registered but unusable, e.g. h264_nvenc with no NVIDIA GPU.
                failures.append(f"{name}: {exc}")
                log(f"encoder {name} unavailable, trying the next one")
                continue
            self.name, self.pix_fmt, self._options, self.ctx = name, pix_fmt, options, ctx
            # Stick with what worked, so a bitrate change does not re-probe.
            self._candidates = [name]
            log(
                f"encoder: {name} {self.width}x{self.height} @ {self.fps} fps, "
                f"{self.bitrate / 1e6:.1f} Mb/s, {pix_fmt}"
            )
            return
        raise EncoderError("no usable encoder -- " + "; ".join(failures))

    @property
    def extradata(self) -> bytes:
        raw = getattr(self.ctx, "extradata", None)
        return bytes(raw) if raw else b""

    def set_bitrate(self, bitrate: int) -> None:
        if bitrate == self.bitrate:
            return
        self.bitrate = bitrate
        self.close()
        self._open()

    def _to_frame(self, bgra: np.ndarray):
        try:
            # Wraps the capture buffer in place -- no copy of the BGRA frame.
            src = av.VideoFrame.from_numpy_buffer(bgra, format="bgra")
        except Exception:
            src = av.VideoFrame.from_ndarray(bgra, format="bgra")
        if (src.width, src.height, src.format.name) == (
            self.width, self.height, self.pix_fmt
        ):
            return src
        if self._reformatter is None:
            return src.reformat(width=self.width, height=self.height, format=self.pix_fmt)
        return self._reformatter.reformat(
            src,
            width=self.width,
            height=self.height,
            format=self.pix_fmt,
            interpolation=self.scale_filter,
        )

    def encode(self, bgra: np.ndarray, pts_us: int,
               force_keyframe: bool = False) -> list[tuple[int, bytes, bool]]:
        """Encode one BGRA frame. Returns ``(pts_us, annexb, keyframe)`` tuples."""
        frame = self._to_frame(bgra)
        frame.pts = pts_us
        frame.time_base = Fraction(1, 1_000_000)
        if force_keyframe:
            try:
                frame.pict_type = _PICTURE_TYPE_I
            except (TypeError, ValueError):
                frame.pict_type = "I"
        out = []
        for packet in self.ctx.encode(frame):
            out.append((
                int(packet.pts if packet.pts is not None else pts_us),
                bytes(packet),
                bool(packet.is_keyframe),
            ))
        return out

    def flush(self) -> list[tuple[int, bytes, bool]]:
        if self.ctx is None:
            return []
        try:
            return [
                (int(p.pts or 0), bytes(p), bool(p.is_keyframe))
                for p in self.ctx.encode(None)
            ]
        except Exception:
            return []

    def close(self) -> None:
        _release(self.ctx)
        self.ctx = None
