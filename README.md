# HSCast

Low-latency, two-way screen casting between Android and Windows, with mouse and
keyboard control of the phone from the PC.

* **`mirror`** — the phone's screen on the PC (like scrcpy), plus input control
* **`desktop`** — the PC's desktop on the phone

Both directions run over USB (through an ADB tunnel) or plain Wi-Fi TCP.
Nothing is transcoded in software that a GPU can do instead: the phone's
compositor writes straight into a hardware H.264/HEVC encoder, and the PC
uploads decoded YUV planes to a GPU texture without ever touching RGB.

## Measured on the development machine

Intel iGPU (no NVIDIA), 12 cores, Windows 11, loopback TCP:

| Path | Result |
|------|--------|
| PC desktop → phone, 1920×1080 captured, encoded 1280×720 @ 60 | **60.0 fps sustained**, encode p50 6.8 ms / p95 10.4 ms, 1.4 Mb/s idle desktop |
| Phone → PC, 720×1280 @ 60 | **60 fps end to end**, hardware D3D11VA decode |

Capture is DXGI Desktop Duplication, encode picked `h264_qsv` automatically
after `h264_nvenc` failed to open. On a machine with NVENC expect a further
few milliseconds off the encode figure.

> **Status:** the whole Windows side and the wire protocol are tested and
> working, verified end to end in both directions against the included
> `tools/fake_android.py` harness. The Android app is complete but has **not**
> been compiled or run on a device here — there was no Android SDK or attached
> phone on this machine. Expect to fix small build issues on first `gradle`
> run.

## Quick Start (Windows Application)

For a user-friendly graphical interface, simply double-click:
```cmd
Launch-HSCast.bat
```
or from PowerShell inside `windows/`:
```powershell
.\run.ps1
```
This automatically sets up the environment, verifies hardware acceleration, and opens the **HSCast Screen Casting Studio** GUI.

### Windows Application Features
- **Visual Mode Switcher**: 1-click toggling between *Phone to PC (Mirroring)* and *PC to Phone (Desktop)*.
- **Smart Connection**: Auto-scans USB ADB devices and provides local Wi-Fi IP helper.
- **Tuning & Presets**: *Gaming (60+ FPS)*, *Balanced (1080p 60FPS)*, and *Ultra Quality (HEVC)* with live bitrate slider.
- **Built-in System Doctor**: Instant visual diagnostics for Python, PyAV, GPU Encoders (NVENC/QSV/AMF), PySDL2, DXGI capture, and ADB path configuration.
- **Virtual Demo Simulator**: Test the entire streaming and control pipeline instantly without needing a physical phone attached.
- **Remote Action Bar**: Android navigation hotkeys (Back, Home, Recents, Lock, Wake) right from the dashboard.

---

## Setup

### Windows CLI / Advanced

From `windows`:

```bash
.\run.ps1 doctor
```

`run.ps1` creates `.venv`, installs dependencies and runs the app.
Everything after that is `.\run.ps1 <command>`, `.\run.ps1 gui`, or activate the venv and use `python -m hscast`.

Dependencies: `av` (FFmpeg), `PySDL2` + `pysdl2-dll`, `numpy`, `dxcam`, `mss`.
Python 3.12–3.14 all work; 3.14 was used here.

For USB you also need `adb` on `PATH` (Android platform-tools). Wi-Fi mode
needs no ADB at all.

### Android

There is no `gradlew` jar checked in, so either open `hscast/android/` in
Android Studio, or generate the wrapper once from `hscast/android`:

```bash
gradle wrapper
```

then build and install:

```bash
./gradlew installDebug
```

Then, if you want the PC to control the phone, enable
**Settings → Accessibility → HSCast remote input** (the app's last button opens
that screen directly).

## Phone screen on the PC

```bash
python -m hscast mirror
```

USB by default: it finds the device, sets up `adb forward` for both channels,
launches the app, and opens a window once you accept the capture prompt on the
phone. Over Wi-Fi, start casting from the app and then:

```bash
python -m hscast mirror --wifi 192.168.1.50
```

Window hotkeys:

| | |
|---|---|
| `Ctrl+F` | fullscreen |
| `Ctrl+B` / right click | back |
| `Ctrl+H` / middle click | home |
| `Ctrl+S` | recents |
| `Ctrl+N` | notification shade |
| `Ctrl+P` | lock screen |
| `Ctrl+W` | wake screen |
| `Ctrl+K` | force a keyframe |
| `Ctrl+↑` / `Ctrl+↓` | bitrate ±25 % |
| `Ctrl+Q` | quit |

Left click and drag becomes a touch gesture, the wheel scrolls, and typing goes
to whatever text field has focus. The window title shows the live control
round-trip time.

Useful flags: `--no-control` (view only), `--no-hwaccel` (software decode),
`--vsync` (smoother, one frame more latency), `--record out.h264` (also dump the
elementary stream), `--serial` (pick a device).

## PC desktop on the phone

```bash
python -m hscast desktop
```

The PC listens and the phone dials in. Over USB this sets up `adb reverse` and
launches the app in receive mode. Over Wi-Fi:

```bash
python -m hscast desktop --wifi
```

which prints the address to type into the app.

Useful flags: `--fps 60`, `--bitrate 20M`, `--codec hevc`, `--max-size 1280`
(scale so the longest edge fits; `0` for native), `--monitor 1`, `--no-cursor`,
`--encoder h264_nvenc`, `--scale-filter BILINEAR`.

Desktop Duplication does not include the mouse pointer, so HSCast composites a
small arrow into each frame itself.

## Testing without a phone

`tools/fake_android.py` stands in for the app on both sides. In one terminal:

```bash
python tools\fake_android.py sender
```

and in another:

```bash
python -m hscast mirror --wifi 127.0.0.1
```

The fake phone encodes a synthetic animation and prints every control event it
receives, so you can confirm clicks, drags, scrolls, text and hotkeys land with
the right coordinates. `mirror --exit-after 8` closes the window on its own,
which makes this scriptable.

For the other direction, start the sender:

```bash
python -m hscast desktop --wifi
```

then the stand-in receiver, which reports decode statistics:

```bash
python tools\fake_android.py receiver --frames 240
```

## How it is put together

```
Android                                        Windows
-------                                        -------
MediaProjection                                dxcam (DXGI Desktop Duplication)
  -> VirtualDisplay                              -> BGRA in a numpy buffer
  -> MediaCodec input Surface                     -> one shared swscale context
  -> hardware H.264/HEVC                          -> h264_nvenc / qsv / amf / x264
  -> FrameQueue (bounded, sheds)                  -> FrameWriter (bounded, sheds)
  -> TCP                                          -> TCP
                    <-- video channel (8765 / 8767) -->
                    <-- control channel (8766) -->
MediaCodec decoder -> SurfaceView              PyAV decode -> SDL2 YUV texture
AccessibilityService <- control                SDL input -> control
```

`PROTOCOL.md` has the byte layout. Four design decisions carry most of the
latency win:

**Two TCP connections, not one.** A 200 KB keyframe in flight cannot delay a
touch event, because they are on different sockets. `TCP_NODELAY` is set on
both, and each packet goes out as a single `sendall` of header plus payload so
the two never end up in separate segments.

**Drop frames, never buffer them.** Every queue in the pipeline is bounded at
three frames. On overflow the sender sheds non-keyframes first, then the newest
frame, and asks its encoder for a fresh IDR so the receiver resynchronises
cleanly. Buffering a late frame would trade one dropped frame for permanently
higher delay; on a congested link that difference compounds into seconds.

**Nothing holds a frame back.** No B-frames, no lookahead, `low_delay` on the
decoder, `KEY_LATENCY` on the Android encoder, `tune=ull`/`zerolatency` on the
PC encoder, and decoded output released for display the moment it exists rather
than scheduled against its timestamp. For a live stream, "now" is always the
right presentation time.

**Input runs on its own loop.** The viewer polls SDL every ~2 ms instead of once
per video frame, so a click is on the wire in single-digit milliseconds even
when the video is running at 30 fps.

One non-obvious thing worth knowing if you touch `encoder.py`: PyAV's
`VideoFrame.reformat()` builds a fresh `SwsContext` on every call, and
initialising the scaler costs far more than running it. Sharing one
`VideoReformatter` for the session took BGRA→NV12 with a 1080p→720p downscale
from **20.6 ms to 1.5 ms per frame** — the single largest speedup in the
project.

## Input control: what works and what does not

Injection goes through an `AccessibilityService`, which is the only way a
normal app can synthesise touches without root. That gives you taps, drags,
scrolls, back/home/recents, the notification shade, lock screen and typing into
focused text fields.

The limits are the API's, not the code's:

* Gestures dispatch one at a time. A drag is delivered as a chain of continued
  strokes, and while one is in flight the newest cursor position is *coalesced*
  rather than queued — the finger tracks the cursor instead of falling further
  behind the longer you drag, but it is not scrcpy-smooth.
* Typing is a read-modify-write of the focused field's whole contents, so it
  needs a focused editable view and will not drive a game's key handler.
* Arrow keys and most keycodes have no accessibility equivalent and are ignored.
* Multi-touch and pinch-zoom are not implemented.

scrcpy avoids all of this by running a helper process as the shell UID via ADB
and calling hidden `InputManager` APIs. That is a much larger piece of work and
only functions over USB/ADB, which is why HSCast takes the accessibility route:
it works identically over Wi-Fi and needs no debugging bridge.

## Tuning

* **Latency too high over Wi-Fi.** Drop `--max-size` first (fewer pixels beats a
  lower bitrate), then check the 5 GHz band. `Ctrl+↓` in the viewer lowers the
  phone's bitrate live.
* **Stuttering.** Look at the `dropped` counter in the stats line. Frames being
  shed means the link cannot carry the bitrate.
* **High CPU on the PC.** `doctor` will say if no GPU encoder is usable; a
  software x264 fallback costs noticeably more.
* **Blurry text when casting the desktop.** Raise `--max-size` toward native, or
  keep `--scale-filter AREA` (the default, best for small text).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `adb not found` | Install platform-tools, or use `--wifi` |
| `No authorised device` | Accept the USB debugging prompt on the phone |
| `no usable encoder` | FFmpeg build has no encoder that opens here; try `--encoder libx264` |
| `could not upload NV12 textures` | SDL older than 2.0.16; run `mirror --no-hwaccel` |
| Black window, no frames | The capture consent prompt on the phone was dismissed |
| Taps ignored | The HSCast accessibility service is not enabled |
| Port already in use | A previous run is still holding it, or `adb forward` is stale |

## Layout

```
hscast/
  PROTOCOL.md              wire format
  windows/
    run.ps1                venv bootstrap + CLI wrapper
    hscast/
      protocol.py          framing, packet types, StreamInfo
      transport.py         ADB tunnels, connect/listen, LAN address
      capture.py           dxcam / mss backends, cursor compositing
      encoder.py           encoder probing, shared swscale context
      decoder.py           low-delay decode, hwaccel probing
      renderer.py          SDL2 window, YUV/NV12 texture upload, letterboxing
      control.py           SDL events -> control packets, hotkeys
      pipeline.py          Mailbox (newest wins), FrameWriter (bounded, sheds)
      mirror_app.py        phone -> PC session
      desktop_app.py       PC -> phone session
      doctor.py            environment check
      cli.py               argument parsing
    tools/fake_android.py  phone stand-in for testing
  android/
    app/src/main/java/com/hscast/
      Protocol.kt          constants, mirrors protocol.py
      PacketConn.kt        framed transport, single-write frames
      ScreenEncoder.kt     MediaProjection -> VirtualDisplay -> MediaCodec
      FrameQueue.kt        bounded pooled handoff to the socket writer
      CastService.kt       foreground service, both listening sockets
      ControlHandler.kt    control packets -> input / encoder
      RemoteInputService.kt accessibility gesture + text injection
      VideoDecoder.kt      MediaCodec -> Surface
      ReceiveActivity.kt   PC desktop viewer
      MainActivity.kt      settings and launch UI
```
