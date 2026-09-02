package com.hscast

import android.Manifest
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: SharedPreferences
    private lateinit var fpsField: EditText
    private lateinit var bitrateField: EditText
    private lateinit var maxSizeField: EditText
    private lateinit var hostField: EditText
    private lateinit var portField: EditText
    private lateinit var hevcBox: CheckBox
    private lateinit var inputState: TextView

    private lateinit var consent: ActivityResultLauncher<Intent>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = getSharedPreferences("hscast", MODE_PRIVATE)

        fpsField = findViewById(R.id.fps)
        bitrateField = findViewById(R.id.bitrate)
        maxSizeField = findViewById(R.id.maxSize)
        hostField = findViewById(R.id.host)
        portField = findViewById(R.id.port)
        hevcBox = findViewById(R.id.hevc)
        inputState = findViewById(R.id.inputState)

        fpsField.setText(prefs.getInt(KEY_FPS, 60).toString())
        bitrateField.setText(prefs.getString(KEY_BITRATE, "8"))
        maxSizeField.setText(prefs.getInt(KEY_MAX_SIZE, 1600).toString())
        hostField.setText(prefs.getString(KEY_HOST, "127.0.0.1"))
        portField.setText(prefs.getInt(KEY_PORT, 8767).toString())
        hevcBox.isChecked = prefs.getBoolean(KEY_HEVC, false)

        consent = registerForActivityResult(
            ActivityResultContracts.StartActivityForResult(),
        ) { result ->
            val data = result.data
            if (result.resultCode == RESULT_OK && data != null) {
                startCastService(result.resultCode, data)
            } else {
                toast("Screen capture was denied")
            }
        }

        findViewById<Button>(R.id.startCast).setOnClickListener { requestCast() }
        findViewById<Button>(R.id.stopCast).setOnClickListener {
            CastService.stop(this)
            toast("Casting stopped")
        }
        findViewById<Button>(R.id.receive).setOnClickListener { openReceiver() }
        findViewById<Button>(R.id.enableInput).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            toast("Enable \"HSCast remote input\" in this list")
        }

        requestNotificationPermission()
        handleLaunchIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleLaunchIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        inputState.text = if (RemoteInputService.isEnabled()) {
            "Remote input: enabled"
        } else {
            "Remote input: off -- taps from the PC will be ignored"
        }
    }

    /** Lets `adb shell am start --es mode send|recv` drive the app from the PC. */
    private fun handleLaunchIntent(intent: Intent?) {
        when (intent?.getStringExtra("mode")) {
            "send" -> requestCast()
            "recv" -> {
                intent.getStringExtra("host")?.let { hostField.setText(it) }
                intent.getStringExtra("port")?.let { portField.setText(it) }
                openReceiver()
            }
        }
    }

    private fun requestCast() {
        save()
        val manager = getSystemService(MediaProjectionManager::class.java)
        consent.launch(manager.createScreenCaptureIntent())
    }

    private fun startCastService(resultCode: Int, data: Intent) {
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

    companion object {
        private const val KEY_FPS = "fps"
        private const val KEY_BITRATE = "bitrate"
        private const val KEY_MAX_SIZE = "maxSize"
        private const val KEY_HOST = "host"
        private const val KEY_PORT = "port"
        private const val KEY_HEVC = "hevc"
    }
}
