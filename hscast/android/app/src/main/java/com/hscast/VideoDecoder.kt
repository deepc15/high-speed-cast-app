package com.hscast

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.nio.ByteBuffer
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Hardware decoder rendering straight into a [Surface].
 *
 * Output buffers are released with render=true the instant they appear, with no
 * presentation timestamp: the stream is live, so "as soon as possible" is
 * always the right time to show a frame. Scheduling by pts would only add the
 * buffering we are trying to avoid.
 */
class VideoDecoder(
    codec: Int,
    width: Int,
    height: Int,
    csd: ByteArray,
    surface: Surface,
    private val onFatalError: (Throwable) -> Unit,
) {

    private val availableInputs = LinkedBlockingQueue<Int>()
    private val thread = HandlerThread("hscast-decoder").apply { start() }
    private val codecInstance: MediaCodec

    @Volatile
    private var released = false

    var framesRendered: Long = 0
        private set

    private val callback = object : MediaCodec.Callback() {
        override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {
            availableInputs.offer(index)
        }

        override fun onOutputBufferAvailable(
            codec: MediaCodec,
            index: Int,
            info: MediaCodec.BufferInfo,
        ) {
            if (released) return
            runCatching { codec.releaseOutputBuffer(index, true) }
            framesRendered++
        }

        override fun onError(codec: MediaCodec, error: MediaCodec.CodecException) {
            if (!released) onFatalError(error)
        }

        override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {
            Log.i(TAG, "decoder output format: $format")
        }
    }

    init {
        val mime = Protocol.mimeFor(codec)
        val format = MediaFormat.createVideoFormat(mime, width, height).apply {
            if (csd.isNotEmpty()) setByteBuffer("csd-0", ByteBuffer.wrap(csd))
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            }
        }
        codecInstance = MediaCodec.createDecoderByType(mime)
        codecInstance.setCallback(callback, Handler(thread.looper))
        codecInstance.configure(format, surface, null, 0)
        codecInstance.start()
        Log.i(TAG, "decoding $mime ${width}x$height")
    }

    /**
     * Queue one access unit. Returns false when no input buffer freed up in
     * time, which means the caller should drop the frame and ask for a keyframe
     * rather than let the backlog grow.
     */
    fun submit(ptsUs: Long, data: ByteArray, size: Int): Boolean {
        if (released) return false
        val index = availableInputs.poll(INPUT_WAIT_MS, TimeUnit.MILLISECONDS) ?: return false
        return try {
            val buffer = codecInstance.getInputBuffer(index) ?: return false
            buffer.clear()
            buffer.put(data, 0, size)
            codecInstance.queueInputBuffer(index, 0, size, ptsUs, 0)
            true
        } catch (e: IllegalStateException) {
            Log.w(TAG, "queueInputBuffer failed: ${e.message}")
            false
        }
    }

    fun release() {
        if (released) return
        released = true
        runCatching { codecInstance.stop() }
        runCatching { codecInstance.release() }
        thread.quitSafely()
        availableInputs.clear()
    }

    companion object {
        private const val TAG = "HSCast/Decoder"
        private const val INPUT_WAIT_MS = 20L
    }
}
