"""Low-latency H.264/HEVC decoding via PyAV (libavcodec)."""

from __future__ import annotations

import av
import av.error

from .util import ffmpeg_log_silenced, log

# AV_CODEC_FLAG_LOW_DELAY, used when PyAV's Flags enum is not importable.
_AV_CODEC_FLAG_LOW_DELAY = 1 << 19

# Hardware device types worth trying on Windows, best first.
_HW_DEVICE_TYPES = ("d3d11va", "dxva2", "cuda", "qsv")


def _enable_low_delay(ctx) -> None:
    """Stop the decoder from holding frames back for reordering.

    Without this libavcodec may buffer output waiting for future pictures,
    which is exactly the delay we cannot afford. The flag name moved around
    between PyAV releases, hence the fallback to the raw bit.
    """
    try:
        from av.codec.context import Flags

        ctx.flags |= Flags.LOW_DELAY
        return
    except Exception:  # pragma: no cover - depends on PyAV version
        pass
    try:
        ctx.flags |= _AV_CODEC_FLAG_LOW_DELAY
    except Exception:  # pragma: no cover
        log("warning: could not set the low-delay decoder flag")


def _create_context(codec_name: str, hwaccel: bool):
    if hwaccel:
        try:
            from av.codec.hwaccel import HWAccel
        except Exception:
            log("hwaccel: this PyAV build has no HWAccel support, using software decode")
        else:
            for device in _HW_DEVICE_TYPES:
                try:
                    # Trying devices that do not exist here is the normal path,
                    # so libavcodec's own complaints are not worth showing.
                    with ffmpeg_log_silenced():
                        accel = HWAccel(device_type=device, allow_software_fallback=True)
                        ctx = av.CodecContext.create(codec_name, "r", hwaccel=accel)
                    log(f"hwaccel: decoding {codec_name} on {device}")
                    return ctx
                except Exception as exc:
                    log(f"hwaccel: {device} unavailable ({type(exc).__name__})")
    return av.CodecContext.create(codec_name, "r")


class Decoder:
    """One decoder instance per stream geometry.

    Recreated whenever a new STREAM_INFO arrives (rotation, resolution change).
    """

    def __init__(self, codec_name: str, extradata: bytes = b"", hwaccel: bool = True):
        self.codec_name = codec_name
        self.ctx = _create_context(codec_name, hwaccel)
        # Slice threading parallelises within a picture, so it does not add
        # latency. Frame threading would hold back thread_count frames.
        try:
            self.ctx.thread_type = "SLICE"
            self.ctx.thread_count = 0
        except Exception:
            pass
        _enable_low_delay(self.ctx)
        if extradata:
            try:
                self.ctx.extradata = bytes(extradata)
            except Exception as exc:
                log(f"warning: decoder rejected codec config ({exc})")
        self.corrupt_frames = 0
        self._warned = False

    def decode(self, access_unit: bytes) -> list:
        """Decode one complete access unit. Returns 0..n VideoFrames."""
        try:
            packet = av.Packet(access_unit)
            return self.ctx.decode(packet)
        except (av.error.FFmpegError, ValueError) as exc:
            self.corrupt_frames += 1
            if not self._warned:
                log(f"decode error ({exc}); requesting a keyframe")
                self._warned = True
            return []
        except av.error.EOFError:
            return []
        except Exception as exc:
            self.corrupt_frames += 1
            if not self._warned:
                log(f"unexpected decode error ({type(exc).__name__}: {exc}); requesting a keyframe")
                self._warned = True
            return []

    def flush(self) -> list:
        try:
            return self.ctx.decode(None)
        except Exception:
            return []

    def close(self) -> None:
        # PyAV 13+ dropped CodecContext.close(); dropping the reference frees it.
        closer = getattr(self.ctx, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
        self.ctx = None
