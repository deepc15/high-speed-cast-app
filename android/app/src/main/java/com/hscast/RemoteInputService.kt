package com.hscast

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.res.Configuration
import android.graphics.Path
import android.graphics.Point
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.KeyEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Injects the PC's mouse and keyboard into this device.
 *
 * An accessibility service is the only way to synthesise touches from a normal
 * app process -- everything else needs root or a shell-uid helper. The cost is
 * that gestures are dispatched one at a time, so a drag is delivered as a chain
 * of short continued strokes: while one is in flight the newest position is
 * kept and sent the moment the previous one completes. Intermediate points get
 * coalesced rather than queued, which keeps the finger tracking the cursor
 * instead of trailing further behind the longer you drag.
 */
class RemoteInputService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())

    private var stroke: GestureDescription.StrokeDescription? = null
    private var dispatching = false
    private var lastX = 0f
    private var lastY = 0f
    private var pendingX = 0f
    private var pendingY = 0f
    private var hasPending = false
    private var pendingUp = false
    private var cachedSize: Point? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "remote input connected")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() {
        handler.post { resetGesture() }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        cachedSize = null // rotation changed the coordinate space
    }

    // -- public entry points, safe to call from any thread --------------------

    fun touch(action: Int, nx: Int, ny: Int) {
        handler.post { handleTouch(action, nx, ny) }
    }

    fun scroll(nx: Int, ny: Int, hscroll: Int, vscroll: Int) {
        handler.post { handleScroll(nx, ny, hscroll, vscroll) }
    }

    fun key(action: Int, keycode: Int) {
        if (action != Protocol.KEY_DOWN) return
        handler.post { handleKey(keycode) }
    }

    fun text(value: String) {
        handler.post { handleText(value) }
    }

    fun action(id: Int) {
        handler.post { handleAction(id) }
    }

    // -- geometry ------------------------------------------------------------

    private fun screenSize(): Point {
        cachedSize?.let { return it }
        val manager = getSystemService(WindowManager::class.java)
        val size = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = manager.currentWindowMetrics.bounds
            Point(bounds.width(), bounds.height())
        } else {
            @Suppress("DEPRECATION")
            Point().also { manager.defaultDisplay.getRealSize(it) }
        }
        cachedSize = size
        return size
    }

    private fun toPixels(nx: Int, ny: Int): Pair<Float, Float> {
        val size = screenSize()
        val x = nx.toFloat() / Protocol.COORD_MAX * (size.x - 1)
        val y = ny.toFloat() / Protocol.COORD_MAX * (size.y - 1)
        return x.coerceIn(0f, (size.x - 1).toFloat()) to y.coerceIn(0f, (size.y - 1).toFloat())
    }

    // -- pointer -------------------------------------------------------------

    private fun handleTouch(action: Int, nx: Int, ny: Int) {
        val (x, y) = toPixels(nx, ny)
        when (action) {
            Protocol.TOUCH_DOWN -> {
                resetGesture()
                lastX = x
                lastY = y
                val path = Path().apply { moveTo(x, y) }
                val first = GestureDescription.StrokeDescription(path, 0, STEP_MS, true)
                stroke = first
                dispatch(first)
            }

            Protocol.TOUCH_MOVE -> {
                if (stroke == null) return
                if (dispatching) {
                    pendingX = x
                    pendingY = y
                    hasPending = true
                } else {
                    continueTo(x, y, willContinue = true)
                }
            }

            Protocol.TOUCH_UP, Protocol.TOUCH_CANCEL -> {
                if (stroke == null) return
                if (dispatching) {
                    pendingX = x
                    pendingY = y
                    hasPending = true
                    pendingUp = true
                } else {
                    continueTo(x, y, willContinue = false)
                }
            }
        }
    }

    private fun continueTo(x: Float, y: Float, willContinue: Boolean) {
        val current = stroke ?: return
        val path = Path().apply {
            moveTo(lastX, lastY)
            // A zero-length path is rejected, so nudge a stationary point.
            if (x == lastX && y == lastY) lineTo(x + 0.5f, y) else lineTo(x, y)
        }
        val next = try {
            current.continueStroke(path, 0, STEP_MS, willContinue)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "could not continue stroke: ${e.message}")
            resetGesture()
            return
        }
        lastX = x
        lastY = y
        stroke = if (willContinue) next else null
        dispatch(next)
    }

    private fun dispatch(description: GestureDescription.StrokeDescription) {
        val gesture = GestureDescription.Builder().addStroke(description).build()
        dispatching = true
        val accepted = dispatchGesture(gesture, gestureCallback, handler)
        if (!accepted) {
            Log.w(TAG, "dispatchGesture refused (is another gesture running?)")
            resetGesture()
        }
    }

    private val gestureCallback = object : GestureResultCallback() {
        override fun onCompleted(gestureDescription: GestureDescription?) {
            dispatching = false
            if (!hasPending) return
            hasPending = false
            val up = pendingUp
            pendingUp = false
            continueTo(pendingX, pendingY, willContinue = !up)
        }

        override fun onCancelled(gestureDescription: GestureDescription?) {
            resetGesture()
        }
    }

    private fun resetGesture() {
        dispatching = false
        stroke = null
        hasPending = false
        pendingUp = false
    }

    private fun handleScroll(nx: Int, ny: Int, hscroll: Int, vscroll: Int) {
        if (stroke != null) return // do not fight an in-progress drag
        val (x, y) = toPixels(nx, ny)
        val dx = hscroll / 256f * SCROLL_PIXELS_PER_NOTCH
        // A wheel notch up scrolls the content up, which means dragging down.
        val dy = vscroll / 256f * SCROLL_PIXELS_PER_NOTCH
        if (dx == 0f && dy == 0f) return
        val size = screenSize()
        val path = Path().apply {
            moveTo(x, y)
            lineTo(
                (x + dx).coerceIn(0f, (size.x - 1).toFloat()),
                (y + dy).coerceIn(0f, (size.y - 1).toFloat()),
            )
        }
        val swipe = GestureDescription.StrokeDescription(path, 0, SCROLL_MS, false)
        dispatchGesture(GestureDescription.Builder().addStroke(swipe).build(), null, handler)
    }

    // -- keys and text -------------------------------------------------------

    private fun handleKey(keycode: Int) {
        when (keycode) {
            KeyEvent.KEYCODE_BACK -> performGlobalAction(GLOBAL_ACTION_BACK)
            KeyEvent.KEYCODE_HOME -> performGlobalAction(GLOBAL_ACTION_HOME)
            KeyEvent.KEYCODE_APP_SWITCH -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            KeyEvent.KEYCODE_DEL -> editFocused { existing ->
                if (existing.isEmpty()) existing else existing.dropLast(1)
            }

            KeyEvent.KEYCODE_ENTER -> imeEnter()
            else -> Log.d(TAG, "keycode $keycode has no accessibility equivalent")
        }
    }

    private fun handleText(value: String) {
        editFocused { existing -> existing + value }
    }

    private fun imeEnter() {
        val node = findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                node.performAction(
                    AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id,
                )
            } else {
                node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            }
        } finally {
            @Suppress("DEPRECATION")
            node.recycle()
        }
    }

    /**
     * Accessibility can only replace a field's whole contents, so typing means
     * read-modify-write of the focused editable node.
     */
    private fun editFocused(transform: (String) -> String) {
        val node = findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (node == null) {
            Log.d(TAG, "no focused input to type into")
            return
        }
        try {
            if (!node.isEditable) {
                Log.d(TAG, "focused node is not editable")
                return
            }
            val updated = transform(node.text?.toString() ?: "")
            val arguments = Bundle().apply {
                putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                    updated,
                )
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
            node.performAction(
                AccessibilityNodeInfo.ACTION_SET_SELECTION,
                Bundle().apply {
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, updated.length)
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, updated.length)
                },
            )
        } finally {
            @Suppress("DEPRECATION")
            node.recycle()
        }
    }

    private fun handleAction(id: Int) {
        when (id) {
            Protocol.ACTION_BACK -> performGlobalAction(GLOBAL_ACTION_BACK)
            Protocol.ACTION_HOME -> performGlobalAction(GLOBAL_ACTION_HOME)
            Protocol.ACTION_RECENTS -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            Protocol.ACTION_NOTIFICATIONS -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            Protocol.ACTION_POWER -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN)
                }
            }
            // ACTION_WAKE is handled by CastService, which holds the wake lock.
        }
    }

    companion object {
        private const val TAG = "HSCast/Input"
        private const val STEP_MS = 10L
        private const val SCROLL_MS = 60L
        private const val SCROLL_PIXELS_PER_NOTCH = 220f

        @Volatile
        private var instance: RemoteInputService? = null

        fun get(): RemoteInputService? = instance

        fun isEnabled(): Boolean = instance != null
    }
}
