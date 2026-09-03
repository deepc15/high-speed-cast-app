package com.hscast

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.os.Build
import android.util.Log

/**
 * Captures internal device audio (media/game audio) on Android 10+ (API 29+)
 * via AudioPlaybackCaptureConfiguration and streams PCM blocks to the PC.
 */
class AudioCapturer(
    private val projection: MediaProjection,
    private val listener: Listener,
) {
    interface Listener {
        fun onAudioFrame(ptsUs: Long, pcmData: ByteArray, size: Int)
    }

    private var audioRecord: AudioRecord? = null
    @Volatile
    private var running = false
    private var thread: Thread? = null

    @SuppressLint("MissingPermission")
    fun start() {
        if (running) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            Log.w(TAG, "Internal audio capture requires Android 10 (API 29) or higher")
            return
        }

        try {
            val captureConfig = AudioPlaybackCaptureConfiguration.Builder(projection)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
                .build()

            val sampleRate = 48000
            val channelConfig = AudioFormat.CHANNEL_IN_STEREO
            val audioEncoding = AudioFormat.ENCODING_PCM_16BIT

            val minBufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioEncoding)
            val bufferSize = maxOf(minBufferSize, 16384)

            val record = AudioRecord.Builder()
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(audioEncoding)
                        .setSampleRate(sampleRate)
                        .setChannelMask(channelConfig)
                        .build(),
                )
                .setBufferSizeInBytes(bufferSize)
                .setAudioPlaybackCaptureConfig(captureConfig)
                .build()

            if (record.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord failed to initialize")
                record.release()
                return
            }

            audioRecord = record
            running = true
            record.startRecording()

            thread = Thread({ runCaptureLoop(record) }, "hscast-audio-capturer").also { it.start() }
            Log.i(TAG, "Internal audio capture started successfully (${sampleRate}Hz 16-bit stereo PCM)")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start internal audio capture", e)
        }
    }

    private fun runCaptureLoop(record: AudioRecord) {
        val buffer = ByteArray(3840) // ~20ms chunks of 48000Hz stereo 16-bit PCM
        val startNano = System.nanoTime()

        while (running) {
            val read = record.read(buffer, 0, buffer.size)
            if (read > 0) {
                val ptsUs = (System.nanoTime() - startNano) / 1000L
                listener.onAudioFrame(ptsUs, buffer, read)
            } else if (read < 0) {
                Log.w(TAG, "AudioRecord read error code: $read")
                break
            }
        }
    }

    fun stop() {
        running = false
        thread?.interrupt()
        thread = null
        audioRecord?.runCatching {
            stop()
            release()
        }
        audioRecord = null
    }

    companion object {
        private const val TAG = "HSCast/Audio"
    }
}
