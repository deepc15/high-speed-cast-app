package com.hscast

import android.Manifest
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.view.View
import android.widget.ArrayAdapter
import android.widget.AutoCompleteTextView
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.util.Collections

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var modeRadioGroup: RadioGroup
    private lateinit var modeWifi: RadioButton
    private lateinit var modeUsb: RadioButton
    private lateinit var fpsField: AutoCompleteTextView
    private lateinit var bitrateField: AutoCompleteTextView
    private lateinit var maxSizeField: AutoCompleteTextView
    private lateinit var hostField: EditText
    private lateinit var portField: EditText
    private lateinit var hevcBox: CheckBox
    private lateinit var inputState: TextView
    private lateinit var deviceIp: TextView
    private lateinit var devicePorts: TextView
    private lateinit var senderHint: TextView
    private lateinit var toggleCastBtn: Button
    private lateinit var statusPill: TextView
    private lateinit var homeContainer: LinearLayout
    private lateinit var configContainer: LinearLayout

    private lateinit var consent: ActivityResultLauncher<Intent>

    private fun isWifiMode(): Boolean = modeWifi.isChecked

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        prefs = getSharedPreferences("hscast", MODE_PRIVATE)

        homeContainer = findViewById(R.id.homeContainer)
        configContainer = findViewById(R.id.configContainer)

        modeRadioGroup = findViewById(R.id.modeRadioGroup)
        modeWifi = findViewById(R.id.modeWifi)
        modeUsb = findViewById(R.id.modeUsb)

        val savedMode = prefs.getString(KEY_MODE, "wifi") ?: "wifi"
        if (savedMode == "usb") {
            modeUsb.isChecked = true
        } else {
            modeWifi.isChecked = true
        }
        updateModeSelectorUI()

        modeRadioGroup.setOnCheckedChangeListener { _, checkedId ->
            save()
            updateModeSelectorUI()
            updateDeviceInfo()
            if (checkedId == R.id.modeWifi) {
                startWiFiListener()
            } else {
                WiFiCastListener.stop()
            }
        }

        fpsField = findViewById(R.id.fps)
        bitrateField = findViewById(R.id.bitrate)
        maxSizeField = findViewById(R.id.maxSize)
        hostField = findViewById(R.id.host)
        portField = findViewById(R.id.port)
        hevcBox = findViewById(R.id.hevc)
        inputState = findViewById(R.id.inputState)
        deviceIp = findViewById(R.id.deviceIp)
        devicePorts = findViewById(R.id.devicePorts)
        senderHint = findViewById(R.id.senderHint)
        toggleCastBtn = findViewById(R.id.toggleCast)
        statusPill = findViewById(R.id.statusPill)

        setupDropdown(fpsField, arrayOf("30", "60", "90", "120"))
        setupDropdown(bitrateField, arrayOf("4", "8", "12", "16", "24", "32"))
        setupDropdown(maxSizeField, arrayOf("720", "1080", "1280", "1600", "1920", "2560"))

        fpsField.setText(prefs.getInt(KEY_FPS, 60).toString(), false)
        bitrateField.setText(prefs.getString(KEY_BITRATE, "8"), false)
        maxSizeField.setText(prefs.getInt(KEY_MAX_SIZE, 1600).toString(), false)
        hostField.setText(prefs.getString(KEY_HOST, "127.0.0.1"))
        portField.setText(prefs.getInt(KEY_PORT, 8767).toString())
        hevcBox.isChecked = prefs.getBoolean(KEY_HEVC, false)

        // Listen for CastService status changes in real-time
        CastService.runningListener = { isRunning ->
            runOnUiThread {
                updateCastButtonState(isRunning)
                if (isRunning) {
                    WiFiCastListener.stop()
                } else {
                    startWiFiListener()
                }
            }
        }

        consent = registerForActivityResult(
            ActivityResultContracts.StartActivityForResult(),
        ) { result ->
            val data = result.data
            if (result.resultCode == RESULT_OK && data != null) {
                startCastService(result.resultCode, data)
                updateCastButtonState(true)
            } else {
                Log.w("HSCast", "SCREEN_CAPTURE_CANCELLED: Screen capture was denied by user")
                toast("Screen capture was denied")
                notifyCaptureCancelled()
                startWiFiListener()
            }
        }

        val howToConnectBtn = findViewById<Button>(R.id.howToConnectBtn)
        howToConnectBtn.setOnClickListener {
            senderHint.visibility = if (senderHint.visibility == View.GONE) View.VISIBLE else View.GONE
        }

        val howToConnectReceiverBtn = findViewById<Button>(R.id.howToConnectReceiverBtn)
        val receiverHint = findViewById<TextView>(R.id.receiverHint)
        howToConnectReceiverBtn.setOnClickListener {
            receiverHint.visibility = if (receiverHint.visibility == View.GONE) View.VISIBLE else View.GONE
        }

        toggleCastBtn.setOnClickListener {
            if (CastService.running) {
                CastService.stop(this)
                updateCastButtonState(false)
                toast("Casting stopped")
            } else {
                requestCast()
            }
        }

        findViewById<Button>(R.id.openConfigBtn).setOnClickListener {
            homeContainer.visibility = View.GONE
            configContainer.visibility = View.VISIBLE
        }

        findViewById<Button>(R.id.backBtn).setOnClickListener {
            closeConfiguration()
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (configContainer.visibility == View.VISIBLE) {
                    closeConfiguration()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        findViewById<Button>(R.id.receive).setOnClickListener { openReceiver() }
        findViewById<Button>(R.id.enableInput).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            toast("Enable \"HSCast remote input\" in this list")
        }

        requestNotificationPermission()
        handleLaunchIntent(intent)
        startWiFiListener()
    }

    override fun onDestroy() {
        WiFiCastListener.stop()
        CastService.runningListener = null
        super.onDestroy()
    }

    private fun updateModeSelectorUI() {
        if (isWifiMode()) {
            modeWifi.setBackgroundResource(R.drawable.bg_segment_active_green)
            modeWifi.setTextColor(Color.WHITE)
            modeUsb.setBackgroundColor(Color.TRANSPARENT)
            modeUsb.setTextColor(Color.parseColor("#475569"))
        } else {
            modeUsb.setBackgroundResource(R.drawable.bg_segment_active_green)
            modeUsb.setTextColor(Color.WHITE)
            modeWifi.setBackgroundColor(Color.TRANSPARENT)
            modeWifi.setTextColor(Color.parseColor("#475569"))
        }
    }

    private fun startWiFiListener() {
        if (isWifiMode() && !CastService.running) {
            WiFiCastListener.start {
                runOnUiThread {
                    if (isWifiMode() && !CastService.running) {
                        toast("Incoming Wi-Fi mirroring request from PC...")
                        requestCast()
                    }
                }
            }
        } else {
            WiFiCastListener.stop()
        }
    }

    private fun closeConfiguration() {
        save()
        configContainer.visibility = View.GONE
        homeContainer.visibility = View.VISIBLE
    }

    private fun setupDropdown(field: AutoCompleteTextView, options: Array<String>) {
        val adapter = ArrayAdapter(this, android.R.layout.simple_dropdown_item_1line, options)
        field.setAdapter(adapter)
        field.setOnClickListener { field.showDropDown() }
        field.setOnFocusChangeListener { _, hasFocus ->
            if (hasFocus) field.showDropDown()
        }
    }

    private fun updateCastButtonState(isRunning: Boolean) {
        if (isRunning) {
            statusPill.text = "● Active"
            statusPill.setBackgroundResource(R.drawable.bg_pill_status_running)
            statusPill.setTextColor(Color.parseColor("#059669"))
            toggleCastBtn.text = "Stop Session"
            toggleCastBtn.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#EF4444"))
            toggleCastBtn.setTextColor(Color.WHITE)
        } else {
            statusPill.text = "● Idle"
            statusPill.setBackgroundResource(R.drawable.bg_pill_status_idle)
            statusPill.setTextColor(Color.parseColor("#475569"))
            toggleCastBtn.text = "Start Mirroring"
            toggleCastBtn.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#10B981"))
            toggleCastBtn.setTextColor(Color.WHITE)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleLaunchIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        updateDeviceInfo()
        updateModeSelectorUI()
        updateCastButtonState(CastService.running)
        if (isWifiMode() && !CastService.running) {
            startWiFiListener()
        } else {
            WiFiCastListener.stop()
        }
        inputState.text = if (RemoteInputService.isEnabled()) {
            "Remote input: enabled"
        } else {
            "Remote input: off -- taps from the PC will be ignored"
        }
    }

    private fun updateDeviceInfo() {
        val ip = getDeviceIpAddress()
        if (isWifiMode()) {
            deviceIp.text = "Device IP: $ip"
            devicePorts.text = "Ports: ${Protocol.VIDEO_PORT} (Video), ${Protocol.CONTROL_PORT} (Control)"
            senderHint.text = if (ip != "Unavailable") {
                "On the PC run (over Wi-Fi):\npython -m hscast mirror --ip $ip"
            } else {
                "Not connected to Wi-Fi. Connect to Wi-Fi or switch to USB Mode."
            }
        } else {
            deviceIp.text = "Mode: USB Mode (ADB Forward)"
            devicePorts.text = "Ports: ${Protocol.VIDEO_PORT} (Video), ${Protocol.CONTROL_PORT} (Control)"
            senderHint.text = "On the PC run (over USB):\npython -m hscast mirror"
        }
    }

    private fun getDeviceIpAddress(): String {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return "Unavailable"
            for (intf in Collections.list(interfaces)) {
                if (!intf.isUp || intf.isLoopback) continue
                for (addr in Collections.list(intf.inetAddresses)) {
                    if (!addr.isLoopbackAddress && addr is Inet4Address) {
                        val ip = addr.hostAddress
                        if (!ip.isNullOrEmpty() && ip != "127.0.0.1") {
                            return ip
                        }
                    }
                }
            }
        } catch (e: Exception) {
            // ignore
        }
        return "Unavailable"
    }

    /** Lets `adb shell am start --es mode send|recv` drive the app from the PC. */
    private fun handleLaunchIntent(intent: Intent?) {
        when (intent?.getStringExtra("mode")) {
            "send" -> {
                if (!CastService.running) {
                    requestCast()
                } else {
                    toast("Casting is already active")
                }
            }
            "recv" -> {
                intent.getStringExtra("host")?.let { hostField.setText(it) }
                intent.getStringExtra("port")?.let { portField.setText(it) }
                openReceiver()
            }
        }
    }

    private fun requestCast() {
        if (CastService.running) {
            toast("Casting is already active")
            return
        }
        WiFiCastListener.stop()
        save()
        val manager = getSystemService(MediaProjectionManager::class.java)
        consent.launch(manager.createScreenCaptureIntent())
    }

    private fun startCastService(resultCode: Int, data: Intent) {
        WiFiCastListener.stop()
        val intent = Intent(this, CastService::class.java).apply {
            putExtra(CastService.EXTRA_RESULT_CODE, resultCode)
            putExtra(CastService.EXTRA_RESULT_DATA, data)
            putExtra(CastService.EXTRA_FPS, intField(fpsField, 60).coerceIn(10, 120))
            putExtra(
                CastService.EXTRA_BITRATE,
                (doubleField(bitrateField, 8.0) * 1_000_000).toInt().coerceIn(500_000, 60_000_000),
            )
            putExtra(CastService.EXTRA_MAX_SIZE, intField(maxSizeField, 1600).coerceIn(320, 3840))
            putExtra(
                CastService.EXTRA_CODEC,
                if (hevcBox.isChecked) Protocol.CODEC_HEVC else Protocol.CODEC_H264,
            )
        }
        startForegroundService(intent)
        toast("Casting on ports ${Protocol.VIDEO_PORT}/${Protocol.CONTROL_PORT}")
        if (!RemoteInputService.isEnabled()) {
            toast("Enable remote input if you want to control the phone from the PC")
        }
    }

    private fun openReceiver() {
        save()
        startActivity(
            Intent(this, ReceiveActivity::class.java)
                .putExtra(ReceiveActivity.EXTRA_HOST, hostField.text.toString().trim())
                .putExtra(ReceiveActivity.EXTRA_PORT, intField(portField, 8767)),
        )
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
    }

    private fun save() {
        prefs.edit()
            .putString(KEY_MODE, if (isWifiMode()) "wifi" else "usb")
            .putInt(KEY_FPS, intField(fpsField, 60))
            .putString(KEY_BITRATE, bitrateField.text.toString())
            .putInt(KEY_MAX_SIZE, intField(maxSizeField, 1600))
            .putString(KEY_HOST, hostField.text.toString().trim())
            .putInt(KEY_PORT, intField(portField, 8767))
            .putBoolean(KEY_HEVC, hevcBox.isChecked)
            .apply()
    }

    private fun intField(field: EditText, fallback: Int): Int =
        field.text.toString().trim().toIntOrNull() ?: fallback

    private fun doubleField(field: EditText, fallback: Double): Double =
        field.text.toString().trim().toDoubleOrNull() ?: fallback

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private fun notifyCaptureCancelled() {
        Thread({
            try {
                val server = ServerSocket()
                server.reuseAddress = true
                server.bind(InetSocketAddress(Protocol.VIDEO_PORT))
                server.soTimeout = 2000
                val client = server.accept()
                val out = client.getOutputStream()
                out.write(byteArrayOf('H'.code.toByte(), 'S'.code.toByte(), 'C'.code.toByte(), '1'.code.toByte(), 1, 1, 1, 0x80.toByte()))
                out.flush()
                client.close()
                server.close()
            } catch (_: Exception) {
                // Ignore timeout or occupied port
            }
        }, "hscast-cancel-notify").start()
    }

    private object WiFiCastListener {
        private var serverSocket: ServerSocket? = null
        @Volatile
        private var listening = false

        fun start(onHit: () -> Unit) {
            if (listening || CastService.running) return
            listening = true
            Thread({
                while (listening && !CastService.running) {
                    var server: ServerSocket? = null
                    try {
                        server = ServerSocket()
                        server.reuseAddress = true
                        server.bind(InetSocketAddress(Protocol.VIDEO_PORT))
                        serverSocket = server
                        Log.i("HSCast/WiFiListener", "Listening on port ${Protocol.VIDEO_PORT} for Wi-Fi auto-start...")

                        while (listening && !CastService.running) {
                            val socket = try {
                                server.accept()
                            } catch (e: Exception) {
                                break
                            }
                            Log.i("HSCast/WiFiListener", "Inbound Wi-Fi mirror request from ${socket.remoteSocketAddress}")
                            runCatching { socket.close() }
                            stop()
                            onHit()
                            break
                        }
                    } catch (e: Exception) {
                        Log.d("HSCast/WiFiListener", "Port ${Protocol.VIDEO_PORT} busy or waiting: ${e.message}")
                        runCatching { Thread.sleep(1000) }
                    } finally {
                        runCatching { server?.close() }
                        if (serverSocket === server) serverSocket = null
                    }
                }
                listening = false
            }, "hscast-wifi-listener").start()
        }

        fun stop() {
            listening = false
            runCatching { serverSocket?.close() }
            serverSocket = null
        }
    }

    companion object {
        private const val KEY_MODE = "mode_type"
        private const val KEY_FPS = "fps"
        private const val KEY_BITRATE = "bitrate"
        private const val KEY_MAX_SIZE = "maxSize"
        private const val KEY_HOST = "host"
        private const val KEY_PORT = "port"
        private const val KEY_HEVC = "hevc"
    }
}
