package com.hscast

import android.content.res.Configuration
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.math.min

/**
 * PC -> phone receiver. Connects out to the Windows sender, decodes into a
 * SurfaceView, and shows the desktop letterboxed at its real aspect ratio.
 */
class ReceiveActivity : AppCompatActivity() {

    private lateinit var root: FrameLayout
    private lateinit var surfaceView: SurfaceView
    private lateinit var status: TextView

    private var host: String = "127.0.0.1"
    private var port: Int = 8767

    private var network: Thread? = null

    @Volatile
    private var running = false

    @Volatile
    private var decoder: VideoDecoder? = null

    @Volatile
    private var streamWidth = 0

    @Volatile
    private var streamHeight = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_receive)
        root = findViewById(R.id.root)
        surfaceView = findViewById(R.id.surface)
        status = findViewById(R.id.status)

        host = intent.getStringExtra(EXTRA_HOST) ?: host
        port = intent.getIntExtra(EXTRA_PORT, port)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemBars()

        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) = startNetwork(holder)

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) = Unit

            override fun surfaceDestroyed(holder: SurfaceHolder) = stopNetwork()
        })
    }

    override fun onDestroy() {
        stopNetwork()
        super.onDestroy()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyAspect()
    }

    private fun hideSystemBars() {
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            )
    }

    /** Letterbox the surface so the desktop is not stretched to the phone's aspect. */
    private fun applyAspect() {
        val width = streamWidth
        val height = streamHeight
        if (width <= 0 || height <= 0) return
        root.post {
            val available = min(
                root.width.toFloat() / width,
                root.height.toFloat() / height,
            )
            if (available <= 0f) return@post
            surfaceView.layoutParams = FrameLayout.LayoutParams(
                (width * available).toInt(),
                (height * available).toInt(),
                Gravity.CENTER,
            )
        }
    }

    private fun setStatus(text: String?) {
        runOnUiThread {
            if (text == null) {
                status.visibility = View.GONE
            } else {
                status.visibility = View.VISIBLE
                status.text = text
            }
        }
    }

    // -- networking ----------------------------------------------------------

    private fun startNetwork(holder: SurfaceHolder) {
        if (running) return
        running = true
        network = Thread({ networkLoop(holder) }, "hscast-receive").also { it.start() }
    }

    private fun stopNetwork() {
        running = false
        network?.interrupt()
        network = null
        decoder?.release()
        decoder = null
    }

    private fun networkLoop(holder: SurfaceHolder) {
        while (running) {
            setStatus("Connecting to $host:$port ...")
            var conn: PacketConn? = null
            try {
                val socket = Socket()
                socket.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                conn = PacketConn(socket, Protocol.CH_VIDEO, Protocol.ROLE_RECEIVER)
                conn.handshake()
                setStatus("Waiting for video ...")
                readStream(conn, holder)
            } catch (e: Exception) {
                if (running) Log.i(TAG, "receive session ended: ${e.message}")
            } finally {
                conn?.close()
                decoder?.release()
                decoder = null
            }
            if (!running) break
            setStatus("Reconnecting ...")
            try {
                Thread.sleep(RETRY_DELAY_MS)
            } catch (e: InterruptedException) {
                break
            }
        }
    }

    private fun readStream(conn: PacketConn, holder: SurfaceHolder) {
        while (running) {
            val packet = conn.readPacket()
            when (packet.type) {
                Protocol.P_STREAM_INFO -> onStreamInfo(packet, holder)

                Protocol.P_VIDEO_CONFIG -> {
                    // Sender restarted its encoder: rebuild with the new SPS/PPS.
                    rebuildDecoder(holder, packet.payload)
                }

                Protocol.P_VIDEO_FRAME -> {
                    if (packet.payload.size <= 9) continue
                    val current = decoder ?: continue
                    val pts = packet.i64(0)
                    val size = packet.payload.size - 9
                    val body = ByteArray(size)
                    System.arraycopy(packet.payload, 9, body, 0, size)
                    if (!current.submit(pts, body, size)) {
                        // Decoder is saturated; skip ahead instead of queueing.
                        runCatching { conn.sendRequestKeyframe() }
                    } else {
                        setStatus(null)
                    }
                }

                Protocol.P_PING -> runCatching { conn.sendPong(packet.i64(0)) }
            }
        }
    }

    private fun onStreamInfo(packet: Packet, holder: SurfaceHolder) {
        if (packet.payload.size < 13) return
        codec = packet.u8(0)
        streamWidth = packet.u16(1)
        streamHeight = packet.u16(3)
        val extraLen = packet.u16(11)
        val extra = if (extraLen > 0 && packet.payload.size >= 13 + extraLen) {
            packet.payload.copyOfRange(13, 13 + extraLen)
        } else {
            ByteArray(0)
        }
        Log.i(TAG, "stream ${streamWidth}x$streamHeight codec=$codec extra=${extra.size}B")
        applyAspect()
        rebuildDecoder(holder, extra)
    }

    private var codec = Protocol.CODEC_H264

    private fun rebuildDecoder(holder: SurfaceHolder, extra: ByteArray) {
        if (streamWidth <= 0 || streamHeight <= 0) return
        decoder?.release()
        decoder = try {
            VideoDecoder(codec, streamWidth, streamHeight, extra, holder.surface) { error ->
                Log.e(TAG, "decoder failed", error)
                decoder?.release()
                decoder = null
            }
        } catch (e: Exception) {
            Log.e(TAG, "could not create the decoder", e)
            setStatus("Decoder error: ${e.message}")
            null
        }
    }

    companion object {
        private const val TAG = "HSCast/Receive"
        private const val CONNECT_TIMEOUT_MS = 4000
        private const val RETRY_DELAY_MS = 700L

        const val EXTRA_HOST = "host"
        const val EXTRA_PORT = "port"
    }
}
