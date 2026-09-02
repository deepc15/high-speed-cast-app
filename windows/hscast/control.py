"""Turn SDL mouse/keyboard events into HSCast control packets."""

from __future__ import annotations

import ctypes

import sdl2

from . import protocol as P
from .util import log

# --- Android KeyEvent constants we forward ---------------------------------

AKEY_HOME = 3
AKEY_BACK = 4
AKEY_DPAD_UP = 19
AKEY_DPAD_DOWN = 20
AKEY_DPAD_LEFT = 21
AKEY_DPAD_RIGHT = 22
AKEY_VOLUME_UP = 24
AKEY_VOLUME_DOWN = 25
AKEY_POWER = 26
AKEY_TAB = 61
AKEY_ENTER = 66
AKEY_DEL = 67
AKEY_APP_SWITCH = 187
AKEY_FORWARD_DEL = 112
AKEY_ESCAPE = 111
AKEY_MOVE_HOME = 122
AKEY_MOVE_END = 123
AKEY_PAGE_UP = 92
AKEY_PAGE_DOWN = 93

AMETA_SHIFT_ON = 0x00000001
AMETA_ALT_ON = 0x00000002
AMETA_CTRL_ON = 0x00001000

# Keys with no text of their own, so they must travel as key events.
_KEY_MAP = {
    sdl2.SDLK_RETURN: AKEY_ENTER,
    sdl2.SDLK_KP_ENTER: AKEY_ENTER,
    sdl2.SDLK_BACKSPACE: AKEY_DEL,
    sdl2.SDLK_DELETE: AKEY_FORWARD_DEL,
    sdl2.SDLK_TAB: AKEY_TAB,
    sdl2.SDLK_ESCAPE: AKEY_ESCAPE,
    sdl2.SDLK_UP: AKEY_DPAD_UP,
    sdl2.SDLK_DOWN: AKEY_DPAD_DOWN,
    sdl2.SDLK_LEFT: AKEY_DPAD_LEFT,
    sdl2.SDLK_RIGHT: AKEY_DPAD_RIGHT,
    sdl2.SDLK_HOME: AKEY_MOVE_HOME,
    sdl2.SDLK_END: AKEY_MOVE_END,
    sdl2.SDLK_PAGEUP: AKEY_PAGE_UP,
    sdl2.SDLK_PAGEDOWN: AKEY_PAGE_DOWN,
}

HOTKEY_HELP = """\
Ctrl+F fullscreen   Ctrl+B back      Ctrl+H home     Ctrl+S recents
Ctrl+N notifs       Ctrl+P power     Ctrl+W wake     Ctrl+K force keyframe
Ctrl+Up/Down bitrate +/-             Ctrl+Q quit
Right click = back, middle click = home, wheel = scroll"""

_SCROLL_UNIT = 256  # protocol sends scroll in 1/256 of a notch


class ControlSender:
    """Owns the viewer-side input state machine.

    Kept deliberately stateless apart from the pointer-down flag: every event
    is translated and written straight to the socket so a click costs one
    small ``sendall`` and nothing else.
    """

    def __init__(self, conn: P.Conn | None, renderer, bitrate: int = 8_000_000):
        self.conn = conn
        self.renderer = renderer
        self.bitrate = bitrate
        self._pointer_down = False
        self._last_valid = (0, 0)
        self.quit_requested = False
        if conn is not None:
            sdl2.SDL_StartTextInput()

    # -- helpers -------------------------------------------------------------

    def _send(self, fn, *args) -> None:
        if self.conn is None:
            return
        try:
            fn(*args)
        except OSError as exc:
            log(f"control channel lost: {exc}")
            self.conn = None

    def _norm(self, x: int, y: int):
        return self.renderer.to_normalised(x, y)

    # -- event dispatch ------------------------------------------------------

    def handle(self, event) -> None:
        etype = event.type
        if etype == sdl2.SDL_MOUSEBUTTONDOWN:
            self._mouse_button(event.button, True)
        elif etype == sdl2.SDL_MOUSEBUTTONUP:
            self._mouse_button(event.button, False)
        elif etype == sdl2.SDL_MOUSEMOTION:
            self._mouse_motion(event.motion)
        elif etype == sdl2.SDL_MOUSEWHEEL:
            self._mouse_wheel(event.wheel)
        elif etype == sdl2.SDL_KEYDOWN:
            self._key(event.key, True)
        elif etype == sdl2.SDL_KEYUP:
            self._key(event.key, False)
        elif etype == sdl2.SDL_TEXTINPUT:
            text = bytes(event.text.text).split(b"\x00", 1)[0].decode("utf-8", "ignore")
            if text:
                self._send(self.conn.send_text, text)

    def handle_all(self, events) -> None:
        for event in events:
            self.handle(event)

    # -- mouse ---------------------------------------------------------------

    def _mouse_button(self, button, pressed: bool) -> None:
        if button.button == sdl2.SDL_BUTTON_RIGHT:
            if pressed:
                self._send(self.conn.send_action, P.ACTION_BACK)
            return
        if button.button == sdl2.SDL_BUTTON_MIDDLE:
            if pressed:
                self._send(self.conn.send_action, P.ACTION_HOME)
            return
        if button.button != sdl2.SDL_BUTTON_LEFT:
            return
        point = self._norm(button.x, button.y)
        if pressed:
            if point is None:
                return  # click landed in a letterbox bar
            self._pointer_down = True
            self._send(self.conn.send_touch, P.TOUCH_DOWN, 0, point[0], point[1])
        elif self._pointer_down:
            self._pointer_down = False
            # Release outside the surface still needs an UP, or the remote
            # gesture recogniser is left holding a phantom finger down.
            x, y = point if point else self._last_valid
            self._send(self.conn.send_touch, P.TOUCH_UP, 0, x, y)

    def _mouse_motion(self, motion) -> None:
        if not self._pointer_down:
            return
        point = self._norm(motion.x, motion.y)
        if point is None:
            return
        self._last_valid = point
        self._send(self.conn.send_touch, P.TOUCH_MOVE, 0, point[0], point[1])

    def _mouse_wheel(self, wheel) -> None:
        # SDL_MouseWheelEvent carries no position, so query the current one.
        point = self._norm(*_mouse_position())
        if point is None:
            return
        self._send(
            self.conn.send_scroll,
            point[0], point[1],
            int(wheel.x * _SCROLL_UNIT), int(wheel.y * _SCROLL_UNIT),
        )

    # -- keyboard ------------------------------------------------------------

    def _key(self, key, pressed: bool) -> None:
        sym = key.keysym.sym
        mod = key.keysym.mod
        ctrl = bool(mod & sdl2.KMOD_CTRL)

        if ctrl:
            if pressed:
                self._hotkey(sym)
            return  # never forward the hotkey modifier combo to the device

        mapped = _KEY_MAP.get(sym)
        if mapped is not None:
            meta = 0
            if mod & sdl2.KMOD_SHIFT:
                meta |= AMETA_SHIFT_ON
            if mod & sdl2.KMOD_ALT:
                meta |= AMETA_ALT_ON
            self._send(
                self.conn.send_key,
                P.KEY_DOWN if pressed else P.KEY_UP, mapped, meta,
            )
            return
        # Printable keys arrive again as SDL_TEXTINPUT; forwarding the keydown
        # too would type every character twice.

    def _hotkey(self, sym) -> None:
        if sym == sdl2.SDLK_f:
            self.renderer.toggle_fullscreen()
        elif sym == sdl2.SDLK_q:
            self.quit_requested = True
        elif sym == sdl2.SDLK_b:
            self._send(self.conn.send_action, P.ACTION_BACK)
        elif sym == sdl2.SDLK_h:
            self._send(self.conn.send_action, P.ACTION_HOME)
        elif sym == sdl2.SDLK_s:
            self._send(self.conn.send_action, P.ACTION_RECENTS)
        elif sym == sdl2.SDLK_n:
            self._send(self.conn.send_action, P.ACTION_NOTIFICATIONS)
        elif sym == sdl2.SDLK_p:
            self._send(self.conn.send_action, P.ACTION_POWER)
        elif sym == sdl2.SDLK_w:
            self._send(self.conn.send_action, P.ACTION_WAKE)
        elif sym == sdl2.SDLK_k:
            self._send(self.conn.send_request_keyframe)
        elif sym in (sdl2.SDLK_UP, sdl2.SDLK_EQUALS, sdl2.SDLK_PLUS):
            self._adjust_bitrate(1.25)
        elif sym in (sdl2.SDLK_DOWN, sdl2.SDLK_MINUS):
            self._adjust_bitrate(0.8)

    def _adjust_bitrate(self, factor: float) -> None:
        self.bitrate = int(max(500_000, min(60_000_000, self.bitrate * factor)))
        log(f"requesting bitrate {self.bitrate / 1e6:.1f} Mb/s")
        self._send(self.conn.send_set_bitrate, self.bitrate)


def _mouse_position() -> tuple[int, int]:
    x, y = ctypes.c_int(), ctypes.c_int()
    sdl2.SDL_GetMouseState(ctypes.byref(x), ctypes.byref(y))
    return x.value, y.value
