package com.hscast

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.hardware.display.DisplayManager
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.DisplayMetrics
import android.util.Log
import android.view.Display
import android.view.WindowManager
import java.io.IOException
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.nio.ByteBuffer

/**
 * Phone -> PC sender: foreground service owning the MediaProjection, the
 * hardware encoder and the two listening sockets.
 *
 * The PC dials in (directly over Wi-Fi, or through `adb forward` over USB),
 * which means no discovery protocol and no inbound firewall rules on Windows.
 */
class CastService : Service(), ScreenEncoder.Listener, ControlHandler.Sink {

    private data class Settings(
        val fps: Int,
        val bitrateBps: Int,
        val maxSize: Int,
        val codec: Int,
    )

    private lateinit var worker: HandlerThread
    private lateinit var workerHandler: Handler

    private var projection: MediaProjection? = null
    private var encoder: ScreenEncoder? = null
    private var settings = Settings(60, 8_000_000, 1600, Protocol.CODEC_H264)

    private var videoServer: ServerSocket? = null
    private var controlServer: ServerSocket? = null

    @Volatile
    private var videoConn: PacketConn? = null

    @Volatile
    private var frames: FrameQueue? = null

    private var wakeLock: PowerManager.WakeLock? = null
    private var lastRotation = -1
    private var lastWidth = -1
    private var lastHeight = -1

    private val restartRunnable = Runnable {
        restartEncoder()
    }

    // -- lifecycle -----------------------------------------------------------

    override fun onCreate() {
        super.onCreate()
        worker = HandlerThread("hscast-service").apply { start() }
        workerHandler = Handler(worker.looper)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }

        // Android 14+ requires the media-projection foreground service to be
        // running *before* getMediaProjection() is called.
        startForegroundNotification()

        if (projection != null) return START_STICKY

        val resultCode = intent?.getIntExtra(EXTRA_RESULT_CODE, 0) ?: 0
        val resultData: Intent? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent?.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent?.getParcelableExtra(EXTRA_RESULT_DATA)
        }

        if (resultCode == 0 || resultData == null) {
            Log.e(TAG, "no screen capture consent supplied (resultCode=$resultCode, resultData=$resultData)")
            stopSelf()
            return START_NOT_STICKY
        }

        settings = Settings(
            fps = intent?.getIntExtra(EXTRA_FPS, 60) ?: 60,
            bitrateBps = intent?.getIntExtra(EXTRA_BITRATE, 8_000_000) ?: 8_000_000,
            maxSize = intent?.getIntExtra(EXTRA_MAX_SIZE, 1600) ?: 1600,
            codec = intent?.getIntExtra(EXTRA_CODEC, Protocol.CODEC_H264) ?: Protocol.CODEC_H264,
        )

        val manager = getSystemService(MediaProjectionManager::class.java)
        projection = manager.getMediaProjection(resultCode, resultData).also {
            it.registerCallback(projectionCallback, workerHandler)
        }

        acquireWakeLock()
        registerRotationListener()
        running = true

        Thread(::runVideoServer, "hscast-video-accept").start()
        Thread(::runControlServer, "hscast-control-accept").start()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        running = false
        closeVideoSession()
        runCatching { videoServer?.close() }
        runCatching { controlServer?.close() }
        unregisterRotationListener()
        projection?.let {
            runCatching { it.unregisterCallback(projectionCallback) }
            runCatching { it.stop() }
        }
        projection = null
        releaseWakeLock()
        worker.quitSafely()
        super.onDestroy()
    }

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            Log.i(TAG, "screen capture revoked")
            stopSelf()
        }
    }

    // -- video channel -------------------------------------------------------

    private fun runVideoServer() {
        var server: ServerSocket? = null
        var bound = false
        for (attempt in 1..10) {
            try {
                server = ServerSocket()
                server.reuseAddress = true
                server.bind(InetSocketAddress(Protocol.VIDEO_PORT))
                bound = true
                break
            } catch (e: IOException) {
                Log.w(TAG, "cannot bind video port ${Protocol.VIDEO_PORT} attempt $attempt: ${e.message}")
                runCatching { server?.close() }
                try { Thread.sleep(300) } catch (_: InterruptedException) { break }
            }
        }
        if (!bound || server == null) {
            Log.e(TAG, "cannot bind video port ${Protocol.VIDEO_PORT} after retries")
            stopSelf()
            return
        }
        videoServer = server
        Log.i(TAG, "video channel listening on ${Protocol.VIDEO_PORT}")

        while (running) {
            val socket = try {
                server.accept()
            } catch (e: IOException) {
                break
            }
            // A new viewer replaces the old one rather than queueing behind it.
            closeVideoSession()
            val conn = PacketConn(socket, Protocol.CH_VIDEO, Protocol.ROLE_SENDER)
            Thread({ serveVideo(conn) }, "hscast-video").start()
        }
        runCatching { server.close() }
    }

    private fun serveVideo(conn: PacketConn) {
        val queue = FrameQueue(QUEUE_DEPTH)
        try {
            conn.handshake()
            Log.i(TAG, "viewer connected from ${conn.peer}")
            synchronized(encoderLock) {
                videoConn = conn
                frames = queue
                startEncoder(conn)
            }
            pumpFrames(conn, queue)
        } catch (e: Exception) {
            Log.i(TAG, "video session ended: ${e.message}")
        } finally {
            queue.close()
            var shouldStop = false
            synchronized(encoderLock) {
                if (videoConn === conn) {
                    videoConn = null
                    frames = null
                    stopEncoder()
                    shouldStop = true
                }
            }
            conn.close()
            if (shouldStop && running) {
                Log.i(TAG, "PC viewer closed casting window; stopping CastService automatically")
                stopSelf()
            }
        }
    }

    private fun pumpFrames(conn: PacketConn, queue: FrameQueue) {
        while (running && frames === queue) {
            val frame = queue.take() ?: return
            try {
                conn.sendVideoFrame(frame.ptsUs, frame.data, frame.size, frame.keyframe)
            } catch (e: IOException) {
                return
            } finally {
                queue.recycle(frame)
            }
            if (queue.takeKeyframeRequest()) {
                encoder?.requestKeyFrame()
            }
        }
    }

    private fun closeVideoSession() {
        frames?.close()
        videoConn?.let { runCatching { it.close() } }
    }

    // -- control channel -----------------------------------------------------

    private fun runControlServer() {
        var server: ServerSocket? = null
        var bound = false
        for (attempt in 1..10) {
            try {
                server = ServerSocket()
                server.reuseAddress = true
                server.bind(InetSocketAddress(Protocol.CONTROL_PORT))
                bound = true
                break
            } catch (e: IOException) {
                Log.w(TAG, "cannot bind control port ${Protocol.CONTROL_PORT} attempt $attempt: ${e.message}")
                runCatching { server?.close() }
                try { Thread.sleep(300) } catch (_: InterruptedException) { break }
            }
        }
        if (!bound || server == null) {
            Log.e(TAG, "cannot bind control port ${Protocol.CONTROL_PORT} after retries")
            return
        }
        controlServer = server
        Log.i(TAG, "control channel listening on ${Protocol.CONTROL_PORT}")

        while (running) {
            val socket = try {
                server.accept()
            } catch (e: IOException) {
                break
            }
            Thread({
                val conn = PacketConn(socket, Protocol.CH_CONTROL, Protocol.ROLE_RECEIVER)
                val handler = ControlHandler(this)
                try {
                    conn.handshake()
                    Log.i(TAG, "control connected from ${conn.peer}")
                    while (running) {
                        handler.handle(conn.readPacket(), conn)
                    }
                } catch (e: Exception) {
                    Log.i(TAG, "control session ended: ${e.message}")
                } finally {
                    conn.close()
                }
            }, "hscast-control").start()
        }
        runCatching { server.close() }
    }

    override fun requestKeyframe() {
        encoder?.requestKeyFrame()
    }

    override fun setBitrate(bps: Int) {
        val clamped = bps.coerceIn(300_000, 60_000_000)
        settings = settings.copy(bitrateBps = clamped)
        encoder?.setBitrate(clamped)
        Log.i(TAG, "bitrate set to ${clamped / 1000} kb/s")
    }

    override fun wakeScreen() {
        val power = getSystemService(PowerManager::class.java)
        @Suppress("DEPRECATION")
        val lock = power.newWakeLock(
            PowerManager.FULL_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
            "hscast:wake",
        )
        lock.acquire(3000)
        workerHandler.postDelayed({ runCatching { lock.release() } }, 2000)
    }

    // -- encoder -------------------------------------------------------------

    /**
     * Guards [encoder] against the video thread starting one while the
     * rotation listener is restarting it on the worker thread.
     */
    private val encoderLock = Any()

    private fun startEncoder(conn: PacketConn) {
        synchronized(encoderLock) {
            val metrics = realDisplayMetrics()
            lastRotation = currentRotation()
            lastWidth = metrics.widthPixels
            lastHeight = metrics.heightPixels
            val (width, height) = ScreenEncoder.scaledSize(
                metrics.widthPixels, metrics.heightPixels, settings.maxSize,
            )
            val config = ScreenEncoder.Config(
                width = width,
                height = height,
                dpi = metrics.densityDpi,
                fps = settings.fps,
                bitrateBps = settings.bitrateBps,
                codec = settings.codec,
                iFrameIntervalSec = 1.0f,
            )
            frames?.clear()
            conn.sendStreamInfo(
                settings.codec, width, height, settings.fps, settings.bitrateBps, ByteArray(0),
            )

            val currentEncoder = encoder
            if (currentEncoder != null && currentEncoder.isRunning) {
                currentEncoder.updateConfig(config)
            } else {
                stopEncoder()
                val projection = this.projection ?: throw IllegalStateException("no projection")
                encoder = ScreenEncoder(projection, config, this).also {
                    it.start()
                    it.requestKeyFrame()
                }
            }
        }
    }

    private fun stopEncoder() {
        synchronized(encoderLock) {
            encoder?.stop()
            encoder = null
        }
    }

    override fun onCodecConfig(csd: ByteArray) {
        runCatching { videoConn?.sendVideoConfig(csd) }
    }

    override fun onFrame(ptsUs: Long, source: ByteBuffer, size: Int, keyframe: Boolean) {
        frames?.submit(ptsUs, source, size, keyframe)
    }

    override fun onEncoderError(error: Throwable) {
        Log.e(TAG, "encoder error", error)
        workerHandler.post { restartEncoder() }
    }

    /**
     * Rebuild the encoder and virtual display.
     *
     * A VirtualDisplay keeps the geometry it was created with, so after a
     * rotation the mirrored content would be letterboxed inside the old frame
     * size. Recreating it and re-announcing the stream keeps the PC window
     * matching the phone.
     */
    private fun restartEncoder() {
        val conn = videoConn ?: return
        synchronized(encoderLock) {
            try {
                startEncoder(conn)
            } catch (e: Exception) {
                Log.e(TAG, "could not restart the encoder", e)
            }
        }
    }

    // -- rotation ------------------------------------------------------------

    private fun checkRotationAndRestart() {
        val rotation = currentRotation()
        val metrics = realDisplayMetrics()
        if (rotation == lastRotation && metrics.widthPixels == lastWidth && metrics.heightPixels == lastHeight) {
            return
        }
        if (encoder == null) return
        Log.i(TAG, "display rotation/geometry change: rot $lastRotation -> $rotation, size ${lastWidth}x${lastHeight} -> ${metrics.widthPixels}x${metrics.heightPixels}")
        lastRotation = rotation
        lastWidth = metrics.widthPixels
        lastHeight = metrics.heightPixels
        workerHandler.removeCallbacks(restartRunnable)
        workerHandler.postDelayed(restartRunnable, 250)
    }

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(displayId: Int) = Unit
        override fun onDisplayRemoved(displayId: Int) = Unit

        override fun onDisplayChanged(displayId: Int) {
            if (displayId != Display.DEFAULT_DISPLAY) return
            workerHandler.post { checkRotationAndRestart() }
        }
    }

    private fun registerRotationListener() {
        getSystemService(DisplayManager::class.java)
            .registerDisplayListener(displayListener, Handler(Looper.getMainLooper()))
    }

    private fun unregisterRotationListener() {
        workerHandler.removeCallbacks(restartRunnable)
        runCatching {
            getSystemService(DisplayManager::class.java)
                .unregisterDisplayListener(displayListener)
        }
    }

    private fun currentRotation(): Int =
        getSystemService(DisplayManager::class.java)
            .getDisplay(Display.DEFAULT_DISPLAY)?.rotation ?: 0

    private fun realDisplayMetrics(): DisplayMetrics {
        val metrics = DisplayMetrics()
        val display = getSystemService(DisplayManager::class.java)?.getDisplay(Display.DEFAULT_DISPLAY)
        if (display != null) {
            display.getRealMetrics(metrics)
        } else {
            val windowManager = getSystemService(WindowManager::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bounds = windowManager.currentWindowMetrics.bounds
                metrics.widthPixels = bounds.width()
                metrics.heightPixels = bounds.height()
                metrics.densityDpi = resources.configuration.densityDpi
            } else {
                @Suppress("DEPRECATION")
                windowManager.defaultDisplay.getRealMetrics(metrics)
            }
        }
        return metrics
    }

    // -- housekeeping --------------------------------------------------------

    private fun acquireWakeLock() {
        val power = getSystemService(PowerManager::class.java)
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hscast:cast").apply {
            setReferenceCounted(false)
            acquire(4 * 60 * 60 * 1000L)
        }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { runCatching { it.release() } }
        wakeLock = null
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        ).apply { setShowBadge(false) }
        manager.createNotificationChannel(channel)
    }

    private fun startForegroundNotification() {
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, CastService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification: Notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText("Casting this screen on ports ${Protocol.VIDEO_PORT}/${Protocol.CONTROL_PORT}")
            .setSmallIcon(android.R.drawable.ic_menu_share)
            .setOngoing(true)
            .addAction(
                Notification.Action.Builder(null as Icon?, "Stop", stopIntent).build(),
            )
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    companion object {
        private const val TAG = "HSCast/Service"
        private const val CHANNEL_ID = "hscast.cast"
        private const val NOTIFICATION_ID = 42
        private const val QUEUE_DEPTH = 3

        const val ACTION_STOP = "com.hscast.action.STOP"
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "resultData"
        const val EXTRA_FPS = "fps"
        const val EXTRA_BITRATE = "bitrate"
        const val EXTRA_MAX_SIZE = "maxSize"
        const val EXTRA_CODEC = "codec"

        @Volatile
        var running = false
            private set(value) {
                field = value
                runningListener?.invoke(value)
            }

        @Volatile
        var runningListener: ((Boolean) -> Unit)? = null

        fun stop(context: Context) {
            context.startService(
                Intent(context, CastService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}
