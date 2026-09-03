package com.hscast

import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import java.nio.ByteBuffer

/**
 * MediaProjection -> VirtualDisplay -> hardware encoder input surface.
 *
 * Nothing touches pixels in Java: the compositor writes the mirrored screen
 * directly into the encoder's input surface, so the only data crossing into
 * our process is the compressed bitstream.
 */
class ScreenEncoder(
    private val projection: MediaProjection,
    val config: Config,
    private val listener: Listener,
) {

    data class Config(
        val width: Int,
        val height: Int,
        val dpi: Int,
        val fps: Int,
        val bitrateBps: Int,
        val codec: Int,
        val iFrameIntervalSec: Float = 1.0f,
    )

    interface Listener {
        fun onCodecConfig(csd: ByteArray)

        /**
         * Called on the encoder's callback thread. [source] is only valid for
         * the duration of the call, so copy anything you need to keep.
         */
        fun onFrame(ptsUs: Long, source: ByteBuffer, size: Int, keyframe: Boolean)

        fun onEncoderError(error: Throwable)
    }

    private var codec: MediaCodec? = null
    private var surface: Surface? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var thread: HandlerThread? = null

    @Volatile
    private var running = false

    private val callback = object : MediaCodec.Callback() {
        override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {
            // Surface input: the encoder pulls frames itself, never called.
        }

        override fun onOutputBufferAvailable(
            codec: MediaCodec,
            index: Int,
            info: MediaCodec.BufferInfo,
        ) {
            try {
                val buffer = codec.getOutputBuffer(index)
                if (buffer != null && info.size > 0) {
                    buffer.position(info.offset)
                    buffer.limit(info.offset + info.size)
                    if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                        val csd = ByteArray(info.size)
                        buffer.get(csd)
                        listener.onCodecConfig(csd)
                    } else {
                        listener.onFrame(
                            info.presentationTimeUs,
                            buffer,
                            info.size,
                            info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0,
                        )
                    }
                }
            } catch (t: Throwable) {
                listener.onEncoderError(t)
            } finally {
                runCatching { codec.releaseOutputBuffer(index, false) }
            }
        }

        override fun onError(codec: MediaCodec, error: MediaCodec.CodecException) {
            listener.onEncoderError(error)
        }

        override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {
            Log.i(TAG, "encoder output format: $format")
        }
    }

    fun start() {
        check(!running) { "already started" }
        val mime = Protocol.mimeFor(config.codec)
        val format = MediaFormat.createVideoFormat(mime, config.width, config.height).apply {
            setInteger(
                MediaFormat.KEY_COLOR_FORMAT,
                MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface,
            )
            setInteger(MediaFormat.KEY_BIT_RATE, config.bitrateBps)
            setInteger(MediaFormat.KEY_FRAME_RATE, config.fps)
            setFloat(MediaFormat.KEY_I_FRAME_INTERVAL, config.iFrameIntervalSec)
            // Repeat previous frame every 100ms (10fps min) if the phone screen is static
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 100_000L)
            // Constant bitrate keeps the encoder from saving up bits and then
            // emitting a burst that a thin link cannot drain in one frame time.
            setInteger(
                MediaFormat.KEY_BITRATE_MODE,
                MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR,
            )
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0)
                // Put SPS/PPS in front of every IDR so a receiver that joins
                // late, or recovers from loss, can decode without a re-announce.
                setInteger(MediaFormat.KEY_PREPEND_HEADER_TO_SYNC_FRAMES, 1)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                // Tells the encoder not to hold frames back for lookahead.
                setInteger(MediaFormat.KEY_LATENCY, 1)
            }
        }

        val encoder = MediaCodec.createEncoderByType(mime)
        val handlerThread = HandlerThread("hscast-encoder").apply { start() }
        encoder.setCallback(callback, Handler(handlerThread.looper))
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        val inputSurface = encoder.createInputSurface()
        encoder.start()

        val flags = DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR or
            DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC

        val display = projection.createVirtualDisplay(
            "hscast",
            config.width,
            config.height,
            config.dpi,
            flags,
            inputSurface,
            null,
            null,
        ) ?: run {
            encoder.stop()
            encoder.release()
            inputSurface.release()
            handlerThread.quitSafely()
            throw IllegalStateException("createVirtualDisplay returned null")
        }

        codec = encoder
        surface = inputSurface
        virtualDisplay = display
        thread = handlerThread
        running = true
        Log.i(
            TAG,
            "encoding ${config.width}x${config.height} @ ${config.fps} fps, " +
                "${config.bitrateBps / 1000} kb/s, $mime",
        )
    }

    fun requestKeyFrame() {
        if (!running) return
        runCatching {
            codec?.setParameters(
                Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) },
            )
        }
    }

    /** Retunes rate control in place -- no encoder restart, no keyframe needed. */
    fun setBitrate(bps: Int) {
        if (!running) return
        runCatching {
            codec?.setParameters(
                Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_VIDEO_BITRATE, bps) },
            )
        }
    }

    fun stop() {
        running = false
        virtualDisplay?.let { runCatching { it.release() } }
        virtualDisplay = null
        codec?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        codec = null
        surface?.let { runCatching { it.release() } }
        surface = null
        thread?.quitSafely()
        thread = null
    }

    companion object {
        private const val TAG = "HSCast/Encoder"

        /**
         * Scale the screen so its longest edge fits [maxSize], keeping the
         * aspect ratio and snapping to a multiple of 8 -- hardware encoders
         * reject or silently pad odd geometry.
         */
        fun scaledSize(width: Int, height: Int, maxSize: Int): Pair<Int, Int> {
            val longest = maxOf(width, height)
            val scale = if (maxSize <= 0 || longest <= maxSize) 1.0 else maxSize.toDouble() / longest
            val w = (width * scale).toInt() and 0x7FFFFFF8
            val h = (height * scale).toInt() and 0x7FFFFFF8
            return maxOf(w, 8) to maxOf(h, 8)
        }
    }
}
