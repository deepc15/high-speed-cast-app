"""SDL2 window that uploads decoded YUV planes straight to a GPU texture.

The decoder hands us yuv420p (or nv12 when hardware decoding). Feeding those
planes to SDL as a YUV texture lets the GPU do the colour conversion in the
sampler, so no frame is ever converted to RGB on the CPU.
"""

from __future__ import annotations

import ctypes

import sdl2

from .util import log


class RendererError(Exception):
    pass


def _sdl_error() -> str:
    return sdl2.SDL_GetError().decode("utf-8", "replace")


_UINT8_P = ctypes.POINTER(ctypes.c_ubyte)


def _plane_ptr(plane):
    """A decoded plane's base address as the ``Uint8*`` PySDL2 expects."""
    return ctypes.cast(ctypes.c_void_p(plane.buffer_ptr), _UINT8_P)


_PIXEL_FORMATS = {
    "yuv420p": sdl2.SDL_PIXELFORMAT_IYUV,
    "yuvj420p": sdl2.SDL_PIXELFORMAT_IYUV,
    "nv12": sdl2.SDL_PIXELFORMAT_NV12,
}


class Renderer:
    def __init__(self, title: str, src_w: int, src_h: int, vsync: bool = False,
                 max_fraction: float = 0.8):
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RendererError(f"SDL_Init failed: {_sdl_error()}")

        sdl2.SDL_SetHint(sdl2.SDL_HINT_RENDER_SCALE_QUALITY, b"1")
        # Without this SDL installs a signal handler that swallows Ctrl+C.
        sdl2.SDL_SetHint(b"SDL_NO_SIGNAL_HANDLERS", b"1")

        win_w, win_h = self._initial_size(src_w, src_h, max_fraction)
        self.window = sdl2.SDL_CreateWindow(
            title.encode("utf-8"),
            sdl2.SDL_WINDOWPOS_CENTERED,
            sdl2.SDL_WINDOWPOS_CENTERED,
            win_w,
            win_h,
            sdl2.SDL_WINDOW_RESIZABLE | sdl2.SDL_WINDOW_ALLOW_HIGHDPI,
        )
        if not self.window:
            raise RendererError(f"SDL_CreateWindow failed: {_sdl_error()}")

        flags = sdl2.SDL_RENDERER_ACCELERATED
        if vsync:
            flags |= sdl2.SDL_RENDERER_PRESENTVSYNC
        self.renderer = sdl2.SDL_CreateRenderer(self.window, -1, flags)
        if not self.renderer:
            raise RendererError(f"SDL_CreateRenderer failed: {_sdl_error()}")

        info = sdl2.SDL_RendererInfo()
        if sdl2.SDL_GetRendererInfo(self.renderer, ctypes.byref(info)) == 0:
            log(f"renderer backend: {info.name.decode()}, vsync={'on' if vsync else 'off'}")

        self.texture = None
        self.src_w = 0
        self.src_h = 0
        self._tex_format = None
        self.closed = False
        self.fullscreen = False
        self._dst = sdl2.SDL_Rect(0, 0, win_w, win_h)
        self.resize_source(src_w, src_h, "yuv420p")

    @staticmethod
    def _initial_size(src_w: int, src_h: int, fraction: float) -> tuple[int, int]:
        mode = sdl2.SDL_DisplayMode()
        if sdl2.SDL_GetCurrentDisplayMode(0, ctypes.byref(mode)) != 0:
            return src_w, src_h
        scale = min(
            1.0,
            mode.w * fraction / max(src_w, 1),
            mode.h * fraction / max(src_h, 1),
        )
        return max(320, int(src_w * scale)), max(240, int(src_h * scale))

    # -- texture -------------------------------------------------------------

    def resize_source(self, src_w: int, src_h: int, pix_fmt: str) -> None:
        fmt = _PIXEL_FORMATS.get(pix_fmt)
        if fmt is None:
            raise RendererError(f"unsupported decoder pixel format {pix_fmt!r}")
        if src_w == self.src_w and src_h == self.src_h and fmt == self._tex_format:
            return
        if self.texture:
            sdl2.SDL_DestroyTexture(self.texture)
        self.texture = sdl2.SDL_CreateTexture(
            self.renderer, fmt, sdl2.SDL_TEXTUREACCESS_STREAMING, src_w, src_h
        )
        if not self.texture:
            raise RendererError(f"SDL_CreateTexture failed: {_sdl_error()}")
        self.src_w, self.src_h, self._tex_format = src_w, src_h, fmt
        log(f"video surface: {src_w}x{src_h} {pix_fmt}")
        self._recompute_dst()

    # -- geometry ------------------------------------------------------------

    def output_size(self) -> tuple[int, int]:
        w, h = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetRendererOutputSize(self.renderer, ctypes.byref(w), ctypes.byref(h))
        return w.value, h.value

    def window_size(self) -> tuple[int, int]:
        w, h = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetWindowSize(self.window, ctypes.byref(w), ctypes.byref(h))
        return w.value, h.value

    def _recompute_dst(self) -> None:
        out_w, out_h = self.output_size()
        if not self.src_w or not self.src_h:
            self._dst = sdl2.SDL_Rect(0, 0, out_w, out_h)
            return
        scale = min(out_w / self.src_w, out_h / self.src_h)
        w = int(self.src_w * scale)
        h = int(self.src_h * scale)
        self._dst = sdl2.SDL_Rect((out_w - w) // 2, (out_h - h) // 2, w, h)

    def to_normalised(self, win_x: int, win_y: int) -> tuple[int, int] | None:
        """Map a window-space mouse position to 0..65535 surface coordinates.

        Returns ``None`` for clicks in the letterbox bars, which are not part
        of the remote screen and must not be forwarded as touches.
        """
        win_w, win_h = self.window_size()
        out_w, out_h = self.output_size()
        if not win_w or not win_h:
            return None
        # High-DPI: mouse events are in window points, the draw rect is in pixels.
        x = win_x * out_w / win_w
        y = win_y * out_h / win_h
        rel_x = (x - self._dst.x) / self._dst.w if self._dst.w else -1.0
        rel_y = (y - self._dst.y) / self._dst.h if self._dst.h else -1.0
        if not (0.0 <= rel_x <= 1.0 and 0.0 <= rel_y <= 1.0):
            return None
        return int(rel_x * 65535), int(rel_y * 65535)

    # -- drawing -------------------------------------------------------------

    def draw(self, frame) -> None:
        pix_fmt = frame.format.name
        self.resize_source(frame.width, frame.height, pix_fmt)
        planes = frame.planes

        if self._tex_format == sdl2.SDL_PIXELFORMAT_IYUV:
            rc = sdl2.SDL_UpdateYUVTexture(
                self.texture, None,
                _plane_ptr(planes[0]), planes[0].line_size,
                _plane_ptr(planes[1]), planes[1].line_size,
                _plane_ptr(planes[2]), planes[2].line_size,
            )
        elif hasattr(sdl2, "SDL_UpdateNVTexture"):
            rc = sdl2.SDL_UpdateNVTexture(
                self.texture, None,
                _plane_ptr(planes[0]), planes[0].line_size,
                _plane_ptr(planes[1]), planes[1].line_size,
            )
        else:
            raise RendererError(
                "this SDL2 build cannot upload NV12 textures (needs SDL >= 2.0.16); "
                "run with --no-hwaccel"
            )
        if rc != 0:
            raise RendererError(f"texture upload failed: {_sdl_error()}")

        sdl2.SDL_RenderClear(self.renderer)
        sdl2.SDL_RenderCopy(self.renderer, self.texture, None, ctypes.byref(self._dst))
        sdl2.SDL_RenderPresent(self.renderer)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        sdl2.SDL_SetWindowFullscreen(
            self.window,
            sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP if self.fullscreen else 0,
        )
        self._recompute_dst()

    def set_title(self, title: str) -> None:
        sdl2.SDL_SetWindowTitle(self.window, title.encode("utf-8"))

    # -- events --------------------------------------------------------------

    def poll_events(self) -> list:
        """Drain the SDL queue, handling window events, returning the rest."""
        out = []
        event = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
            if event.type == sdl2.SDL_QUIT:
                self.closed = True
                continue
            if event.type == sdl2.SDL_WINDOWEVENT:
                if event.window.event in (
                    sdl2.SDL_WINDOWEVENT_SIZE_CHANGED,
                    sdl2.SDL_WINDOWEVENT_RESIZED,
                    sdl2.SDL_WINDOWEVENT_EXPOSED,
                ):
                    self._recompute_dst()
                elif event.window.event == sdl2.SDL_WINDOWEVENT_CLOSE:
                    self.closed = True
                continue
            out.append(sdl2.SDL_Event.from_buffer_copy(event))
        return out

    def close(self) -> None:
        if self.texture:
            sdl2.SDL_DestroyTexture(self.texture)
            self.texture = None
        if self.renderer:
            sdl2.SDL_DestroyRenderer(self.renderer)
            self.renderer = None
        if self.window:
            sdl2.SDL_DestroyWindow(self.window)
            self.window = None
        sdl2.SDL_Quit()

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
