package com.hscast

import java.nio.ByteBuffer
import java.util.ArrayDeque

/**
 * Bounded handoff between the encoder callback thread and the socket writer.
 *
 * The encoder must never block on the network, so when the queue fills up we
 * shed frames instead of applying backpressure: non-keyframes go first, and
 * once anything has been dropped a fresh keyframe is requested so the receiver
 * resynchronises rather than showing smeared macroblocks.
 *
 * Frame buffers are pooled -- at 60 fps a fresh allocation per frame would hand
 * the GC several megabytes a second for no reason.
 */
class FrameQueue(private val capacity: Int = 3) {

    class Frame {
        var ptsUs: Long = 0
        var data: ByteArray = ByteArray(0)
        var size: Int = 0
        var keyframe: Boolean = false
    }

    private val lock = Object()
    private val queue = ArrayDeque<Frame>()
    private val pool = ArrayDeque<Frame>()
    private var keyframeWanted = false
    private var closed = false

    var dropped: Int = 0
        private set

    fun submit(ptsUs: Long, source: ByteBuffer, size: Int, keyframe: Boolean) {
        synchronized(lock) {
            if (closed) return
            if (queue.size >= capacity) shedLocked()
            if (queue.size >= capacity) {
                dropped++
                keyframeWanted = true
                return
            }
            val frame = pool.pollFirst() ?: Frame()
            if (frame.data.size < size) frame.data = ByteArray(size + (size shr 1))
            source.get(frame.data, 0, size)
            frame.ptsUs = ptsUs
            frame.size = size
            frame.keyframe = keyframe
            queue.addLast(frame)
            lock.notifyAll()
        }
    }

    private fun shedLocked() {
        val iterator = queue.iterator()
        while (iterator.hasNext()) {
            val frame = iterator.next()
            if (!frame.keyframe) {
                iterator.remove()
                pool.addLast(frame)
                dropped++
                keyframeWanted = true
            }
        }
    }

    /** Blocks until a frame is available; returns null once closed and drained. */
    fun take(): Frame? {
        synchronized(lock) {
            while (queue.isEmpty() && !closed) {
                lock.wait()
            }
            return queue.pollFirst()
        }
    }

    fun recycle(frame: Frame) {
        synchronized(lock) {
            if (pool.size < capacity + 2) pool.addLast(frame)
        }
    }

    fun takeKeyframeRequest(): Boolean {
        synchronized(lock) {
            val wanted = keyframeWanted
            keyframeWanted = false
            return wanted
        }
    }

    fun close() {
        synchronized(lock) {
            closed = true
            queue.clear()
            lock.notifyAll()
        }
    }
}
