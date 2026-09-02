package com.hscast

import android.util.Log
import java.nio.charset.StandardCharsets

/** Decodes control-channel packets and routes them to input injection or the encoder. */
class ControlHandler(private val sink: Sink) {

    interface Sink {
        fun requestKeyframe()
        fun setBitrate(bps: Int)
        fun wakeScreen()
    }

    fun handle(packet: Packet, conn: PacketConn) {
        when (packet.type) {
            Protocol.P_TOUCH -> {
                if (packet.payload.size < 8) return
                RemoteInputService.get()?.touch(
                    packet.u8(0),
                    packet.u16(2),
                    packet.u16(4),
                ) ?: warnNoInput()
            }

            Protocol.P_KEY -> {
                if (packet.payload.size < 9) return
                RemoteInputService.get()?.key(packet.u8(0), packet.u32(1).toInt())
                    ?: warnNoInput()
            }

            Protocol.P_TEXT -> {
                val text = String(packet.payload, StandardCharsets.UTF_8)
                if (text.isNotEmpty()) {
                    RemoteInputService.get()?.text(text) ?: warnNoInput()
                }
            }

            Protocol.P_SCROLL -> {
                if (packet.payload.size < 8) return
                RemoteInputService.get()?.scroll(
                    packet.u16(0),
                    packet.u16(2),
                    packet.i16(4),
                    packet.i16(6),
                ) ?: warnNoInput()
            }

            Protocol.P_ACTION -> {
                if (packet.payload.isEmpty()) return
                val id = packet.u8(0)
                if (id == Protocol.ACTION_WAKE) {
                    sink.wakeScreen()
                } else {
                    RemoteInputService.get()?.action(id) ?: warnNoInput()
                }
            }

            Protocol.P_REQUEST_KEYFRAME -> sink.requestKeyframe()

            Protocol.P_SET_BITRATE -> {
                if (packet.payload.size < 4) return
                sink.setBitrate(packet.u32(0).toInt())
            }

            Protocol.P_PING -> {
                if (packet.payload.size < 8) return
                runCatching { conn.sendPong(packet.i64(0)) }
            }

            else -> Log.d(TAG, "ignoring control packet type 0x%02x".format(packet.type))
        }
    }

    private fun warnNoInput() {
        if (!warned) {
            warned = true
            Log.w(TAG, "input received but the HSCast accessibility service is not enabled")
        }
    }

    private var warned = false

    companion object {
        private const val TAG = "HSCast/Control"
    }
}
