package io.nightcall.app.service

import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class AckQueueTest {

    // Process-wide object: start every test from an empty queue.
    @Before
    fun clear() {
        AckQueue.drain()
    }

    @Test
    fun `drains in order and empties`() {
        AckQueue.offer(1)
        AckQueue.offer(2)
        assertEquals(listOf(1L, 2L), AckQueue.drain())
        assertEquals(emptyList<Long>(), AckQueue.drain())
    }

    @Test
    fun `remove drops a seq already answered over the WebSocket`() {
        AckQueue.offer(3)
        AckQueue.offer(4)
        AckQueue.remove(3)
        assertEquals(listOf(4L), AckQueue.drain())
    }
}
