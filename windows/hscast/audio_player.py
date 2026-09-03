"""Low-latency PCM audio playback on Windows via PySDL2 Audio."""

from __future__ import annotations

import ctypes
import sdl2
from .util import log


class AudioPlayer:
    """Queues incoming 48000Hz 16-bit stereo PCM audio blocks to the PC soundcard/headphones."""

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.dev = 0
        try:
            if sdl2.SDL_InitSubSystem(sdl2.SDL_INIT_AUDIO) != 0:
                log("warning: SDL_InitSubSystem(AUDIO) failed")
                return

            desired = sdl2.SDL_AudioSpec(sample_rate, sdl2.AUDIO_S16SYS, channels, 1024)
            obtained = sdl2.SDL_AudioSpec(0, 0, 0, 0)

            self.dev = sdl2.SDL_OpenAudioDevice(
                None, 0, ctypes.byref(desired), ctypes.byref(obtained), 0
            )
            if self.dev > 0:
                sdl2.SDL_PauseAudioDevice(self.dev, 0)
                log(f"audio playback ready on PC: {obtained.freq}Hz {obtained.channels}ch")
            else:
                log(f"warning: could not open PC audio output device: {sdl2.SDL_GetError().decode('utf-8', 'replace')}")
        except Exception as exc:
            log(f"warning: audio player initialization skipped ({exc})")
            self.dev = 0

    def play(self, pcm_data: bytes) -> None:
        if self.dev > 0 and pcm_data:
            # Keep audio buffer low (<100ms) to ensure real-time sync with video
            queued = sdl2.SDL_GetQueuedAudioSize(self.dev)
            if queued < 38400: # ~100ms max
                sdl2.SDL_QueueAudio(self.dev, pcm_data, len(pcm_data))

    def close(self) -> None:
        if self.dev > 0:
            sdl2.SDL_ClearAudio(self.dev)
            sdl2.SDL_CloseAudioDevice(self.dev)
            self.dev = 0
