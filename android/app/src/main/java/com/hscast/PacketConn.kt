package com.hscast

import java.io.BufferedInputStream
import java.io.Closeable
import java.io.DataInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.Socket
import java.nio.ByteBuffer

class Packet(val type: Int, val flags: Int, val payload: ByteArray) {
    /** Big-endian readers for the fixed-layout payloads. */
    fun u8(offset: Int): Int = payload[offset].toInt() and 0xFF
    fun u16(offset: Int): Int = (u8(offset) shl 8) or u8(offset + 1)
    fun i16(offset: Int): Int = u16(offset).toShort().toInt()
    fun u32(offset: Int): Long =
        (u8(offset).toLong() shl 24) or (u8(offset + 1).toLong() shl 16) or
            (u8(offset + 2).toLong() shl 8) or u8(offset + 3).toLong()

    fun i64(offset: Int): Long {
        var value = 0L
        for (i in 0 until 8) value = (value shl 8) or u8(offset + i).toLong()
        return value
    }
}

/**
 * Framed packet transport over one TCP socket.
 *
 * Writes assemble the header and the payload into a single reusable buffer and
 * hand it to the socket in one call, so a video frame costs one copy and one
 * write rather than a header write that Nagle-interacts with the payload.
 */
class ModeMismatchException(message: String) : IOException(message)

class PacketConn(
    private val socket: Socket,
    private val channel: Int,
    private val role: Int,
) : Closeable {

    init {
        socket.tcpNoDelay = true
        socket.sendBufferSize = 1 shl 19
    }

    private val input = DataInputStream(BufferedInputStream(socket.getInputStream(), 1 shl 16))
    private val output: OutputStream = socket.getOutputStream()
    private val sendLock = Any()
    private var sendBuf = ByteArray(1 shl 18)

    val peer: String get() = socket.inetAddress?.hostAddress ?: "?"

    fun handshake(localMode: Int = Protocol.FLAG_MODE_UNSPECIFIED): Int {
        val ours = ByteArray(8)
        Protocol.MAGIC.copyInto(ours, 0)
        ours[4] = Protocol.VERSION.toByte()
        ours[5] = channel.toByte()
        ours[6] = role.toByte()
        ours[7] = localMode.toByte()
        synchronized(sendLock) {
            output.write(ours)
            output.flush()
        }
        val theirs = ByteArray(8)
        input.readFully(theirs)
        for (i in 0 until 4) {
            if (theirs[i] != Protocol.MAGIC[i]) throw IOException("peer is not HSCast")
        }
        val version = theirs[4].toInt() and 0xFF
        if (version != Protocol.VERSION) {
            throw IOException("peer speaks protocol v$version, we speak v${Protocol.VERSION}")
        }
        val peerChannel = theirs[5].toInt() and 0xFF
        if (peerChannel != channel) throw IOException("peer opened channel $peerChannel")
        if ((theirs[6].toInt() and 0xFF) == role) throw IOException("role collision")

        val peerFlags = theirs[7].toInt() and 0xFF
        val peerMode = peerFlags and 0x03
        if (localMode != Protocol.FLAG_MODE_UNSPECIFIED && peerMode != Protocol.FLAG_MODE_UNSPECIFIED) {
            if (localMode == Protocol.FLAG_MODE_WIFI && peerMode == Protocol.FLAG_MODE_USB) {
                sendHandshakeError(Protocol.FLAG_ERROR_PLEASE_SELECT_USB)
                throw ModeMismatchException("Please select USB option in Android to proceed")
            }
            if (localMode == Protocol.FLAG_MODE_USB && peerMode == Protocol.FLAG_MODE_WIFI) {
                sendHandshakeError(Protocol.FLAG_ERROR_PLEASE_SELECT_WIFI)
                throw ModeMismatchException("Please select Wi-Fi option in Android to proceed")
            }
        }
        return peerFlags
    }

    fun sendHandshakeError(errorFlag: Int) {
        val err = ByteArray(8)
        Protocol.MAGIC.copyInto(err, 0)
        err[4] = Protocol.VERSION.toByte()
        err[5] = channel.toByte()
        err[6] = role.toByte()
        err[7] = errorFlag.toByte()
        synchronized(sendLock) {
            runCatching {
                output.write(err)
                output.flush()
            }
        }
    }

    // -- reading -------------------------------------------------------------

    fun readPacket(): Packet {
        val type = input.readUnsignedByte()
        val flags = input.readUnsignedByte()
        val length = input.readInt()
        if (length < 0 || length > Protocol.MAX_PAYLOAD) {
            throw IOException("payload of $length bytes is out of range")
        }
        val payload = if (length == 0) EMPTY else ByteArray(length).also(input::readFully)
        return Packet(type, flags, payload)
    }

    // -- writing -------------------------------------------------------------

    private fun ensure(size: Int) {
        if (sendBuf.size < size) sendBuf = ByteArray(Integer.highestOneBit(size) shl 1)
    }

    private fun putInt(buf: ByteArray, offset: Int, value: Int) {
        buf[offset] = (value ushr 24).toByte()
        buf[offset + 1] = (value ushr 16).toByte()
        buf[offset + 2] = (value ushr 8).toByte()
        buf[offset + 3] = value.toByte()
    }

    private fun putLong(buf: ByteArray, offset: Int, value: Long) {
        for (i in 0 until 8) buf[offset + i] = (value ushr (56 - 8 * i)).toByte()
    }

    fun send(type: Int, payload: ByteArray = EMPTY, flags: Int = 0) {
        synchronized(sendLock) {
            val total = HEADER + payload.size
            ensure(total)
            sendBuf[0] = type.toByte()
            sendBuf[1] = flags.toByte()
            putInt(sendBuf, 2, payload.size)
            payload.copyInto(sendBuf, HEADER)
            output.write(sendBuf, 0, total)
            output.flush()
        }
    }

    fun sendStreamInfo(
        codec: Int,
        width: Int,
        height: Int,
        fps: Int,
        bitrate: Int,
        extra: ByteArray,
    ) {
        val payload = ByteArray(13 + extra.size)
        payload[0] = codec.toByte()
        payload[1] = (width ushr 8).toByte(); payload[2] = width.toByte()
        payload[3] = (height ushr 8).toByte(); payload[4] = height.toByte()
        payload[5] = (fps ushr 8).toByte(); payload[6] = fps.toByte()
        putInt(payload, 7, bitrate)
        payload[11] = (extra.size ushr 8).toByte(); payload[12] = extra.size.toByte()
        extra.copyInto(payload, 13)
        send(Protocol.P_STREAM_INFO, payload)
    }

    fun sendVideoConfig(extra: ByteArray) =
        send(Protocol.P_VIDEO_CONFIG, extra, Protocol.FLAG_CONFIG)

    /** Copies [size] bytes out of [source] straight into the send buffer. */
    fun sendVideoFrame(ptsUs: Long, source: ByteBuffer, size: Int, keyframe: Boolean) {
        synchronized(sendLock) {
            val total = HEADER + FRAME_HEADER + size
            ensure(total)
            sendBuf[0] = Protocol.P_VIDEO_FRAME.toByte()
            sendBuf[1] = (if (keyframe) Protocol.FLAG_KEYFRAME else 0).toByte()
            putInt(sendBuf, 2, FRAME_HEADER + size)
            putLong(sendBuf, HEADER, ptsUs)
            sendBuf[HEADER + 8] = (if (keyframe) 1 else 0).toByte()
            source.get(sendBuf, HEADER + FRAME_HEADER, size)
            output.write(sendBuf, 0, total)
            output.flush()
        }
    }

    fun sendVideoFrame(ptsUs: Long, data: ByteArray, size: Int, keyframe: Boolean) =
        sendVideoFrame(ptsUs, ByteBuffer.wrap(data, 0, size), size, keyframe)

    fun sendRequestKeyframe() = send(Protocol.P_REQUEST_KEYFRAME)

    fun sendPong(clockUs: Long) {
        val payload = ByteArray(8)
        putLong(payload, 0, clockUs)
        send(Protocol.P_PONG, payload)
    }

    override fun close() {
        runCatching { socket.close() }
    }

    companion object {
        private const val HEADER = 6
        private const val FRAME_HEADER = 9
        private val EMPTY = ByteArray(0)
    }
}
