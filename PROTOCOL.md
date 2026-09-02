# HSCast wire protocol v1

Two independent TCP connections per session. Keeping control off the video
connection means a large video frame in flight can never delay a touch event
(no head-of-line blocking between the two).

* **Video channel** — one direction, sender → receiver, H.264 or HEVC Annex-B.
* **Control channel** — one direction, viewer → mirrored device, input events.

`TCP_NODELAY` is set on both. All integers are **big-endian**.

## Handshake

Immediately after connect, both peers write 8 bytes and read the peer's 8 bytes:

```
offset size  field
0      4     magic "HSC1"
4      1     version (1)
5      1     channel (1 = video, 2 = control)
6      1     role    (1 = sender, 2 = receiver)
7      1     flags   (reserved, 0)
```

A mismatched magic, version or channel is a hard failure — close the socket.

## Framing

Every message after the handshake:

```
offset size  field
0      1     type
1      1     flags
2      4     payload length (bytes, may be 0)
6      N     payload
```

Frame flags: `0x01` keyframe, `0x02` codec config (SPS/PPS/VPS).

## Video channel messages

### `0x01` STREAM_INFO

Sent once before the first frame, and again whenever the geometry changes
(device rotation, resolution change). The receiver must (re)create its decoder
when it sees this.

```
1  codec       1 = h264, 2 = hevc
2  width       pixels
2  height      pixels
2  fps         nominal frame rate
4  bitrate     target bits/sec
2  extra_len
N  extra       Annex-B codec config (csd-0 + csd-1), may be empty
```

### `0x02` VIDEO_CONFIG

Standalone Annex-B codec config. Emitted when the encoder produces
`BUFFER_FLAG_CODEC_CONFIG` after the stream has already started.

### `0x03` VIDEO_FRAME

```
8  pts_us      presentation timestamp, microseconds, monotonic
1  keyframe    1 = IDR
N  data        one complete access unit, Annex-B start codes
```

One packet is always exactly one access unit, so the receiver never needs a
bitstream parser — it can hand the payload straight to the decoder.

## Control channel messages

Pointer coordinates are **normalised to `0..65535`** across the mirrored
surface. That keeps the channel independent of the sender's resolution, so
rotation or a resolution change mid-session needs no renegotiation.

| Type | Name | Payload |
|------|------|---------|
| `0x10` | TOUCH | `1` action (0 down, 1 up, 2 move, 3 cancel), `1` pointer id, `2` x, `2` y, `2` pressure (0..65535) |
| `0x11` | KEY | `1` action (0 down, 1 up), `4` Android keycode, `4` meta state |
| `0x12` | TEXT | UTF-8 bytes, no terminator |
| `0x13` | SCROLL | `2` x, `2` y, `2` h scroll (signed, 1/256 units), `2` v scroll (signed) |
| `0x14` | ACTION | `1` id (1 back, 2 home, 3 recents, 4 notifications, 5 power, 6 wake) |
| `0x20` | REQUEST_KEYFRAME | empty |
| `0x21` | SET_BITRATE | `4` bits/sec |
| `0x30` | PING | `8` sender clock, microseconds |
| `0x31` | PONG | `8` echoed PING clock |

`REQUEST_KEYFRAME`, `SET_BITRATE`, `PING` and `PONG` are addressed to whichever
peer is *capturing*, so they are accepted on **either** channel. In the
phone → PC direction they ride the control channel; in the PC → phone direction
the phone writes them back up the video socket, which saves opening a second
connection for three message types.

## Backpressure policy

The sender never blocks the encoder on the socket. Encoded frames go into a
bounded queue (default 3 frames). On overflow the sender:

1. drops queued non-keyframes, oldest first;
2. if that is not enough, drops the incoming frame;
3. asks its encoder for a fresh keyframe so the receiver recovers cleanly
   instead of showing corruption.

Dropping late frames is what keeps latency flat under a congested link —
buffering them would trade a frame drop for permanently growing delay.
