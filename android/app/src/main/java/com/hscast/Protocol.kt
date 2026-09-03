package com.hscast

/** HSCast wire protocol v1 constants. Mirrors windows/hscast/protocol.py. */
object Protocol {
    val MAGIC = byteArrayOf(0x48, 0x53, 0x43, 0x31) // "HSC1"
    const val VERSION = 1

    const val CH_VIDEO = 1
    const val CH_CONTROL = 2

    const val ROLE_SENDER = 1
    const val ROLE_RECEIVER = 2

    const val CODEC_H264 = 1
    const val CODEC_HEVC = 2

    const val P_STREAM_INFO = 0x01
    const val P_VIDEO_CONFIG = 0x02
    const val P_VIDEO_FRAME = 0x03

    const val P_TOUCH = 0x10
    const val P_KEY = 0x11
    const val P_TEXT = 0x12
    const val P_SCROLL = 0x13
    const val P_ACTION = 0x14

    const val P_REQUEST_KEYFRAME = 0x20
    const val P_SET_BITRATE = 0x21
    const val P_PING = 0x30
    const val P_PONG = 0x31

    const val FLAG_KEYFRAME = 0x01
    const val FLAG_CONFIG = 0x02
    const val FLAG_MODE_UNSPECIFIED = 0x00
    const val FLAG_MODE_WIFI = 0x01
    const val FLAG_MODE_USB = 0x02
    const val FLAG_ERROR_PLEASE_SELECT_USB = 0x41
    const val FLAG_ERROR_PLEASE_SELECT_WIFI = 0x42
    const val FLAG_CANCELLED = 0x80

    const val TOUCH_DOWN = 0
    const val TOUCH_UP = 1
    const val TOUCH_MOVE = 2
    const val TOUCH_CANCEL = 3

    const val KEY_DOWN = 0
    const val KEY_UP = 1

    const val ACTION_BACK = 1
    const val ACTION_HOME = 2
    const val ACTION_RECENTS = 3
    const val ACTION_NOTIFICATIONS = 4
    const val ACTION_POWER = 5
    const val ACTION_WAKE = 6

    /** Pointer coordinates are resolution independent: 0..65535 across the screen. */
    const val COORD_MAX = 65535

    const val MAX_PAYLOAD = 32 shl 20

    const val VIDEO_PORT = 8765
    const val CONTROL_PORT = 8766

    fun mimeFor(codec: Int): String =
        if (codec == CODEC_HEVC) "video/hevc" else "video/avc"
}
