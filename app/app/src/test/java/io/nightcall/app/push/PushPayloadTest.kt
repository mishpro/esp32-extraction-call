package io.nightcall.app.push

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/** FCM data messages are all strings (§4.10); the parser must not crash on trash. */
class PushPayloadTest {

    private val valid = mapOf(
        "v" to "1",
        "t" to "press",
        "seq" to "7",
        "t_epoch" to "1793001234",
        "device_id" to "a1b2c3d4",
    )

    @Test
    fun `parses a press data message`() {
        val press = PushPayload.toPress(valid)
        assertNotNull(press)
        assertEquals(7L, press!!.seq)
        assertEquals(1_793_001_234L, press.tEpoch)
        assertEquals("a1b2c3d4", press.from)
    }

    @Test
    fun `ignores other message types`() {
        assertNull(PushPayload.toPress(valid + ("t" to "status")))
    }

    @Test
    fun `drops malformed numbers and missing fields`() {
        assertNull(PushPayload.toPress(valid + ("seq" to "x")))
        assertNull(PushPayload.toPress(valid + ("seq" to "0")))
        assertNull(PushPayload.toPress(valid - "t_epoch"))
        assertNull(PushPayload.toPress(valid + ("v" to "2")))
    }

    @Test
    fun `keeps the call when from is malformed`() {
        val press = PushPayload.toPress(valid + ("device_id" to "zz"))
        assertNotNull(press)
        assertNull(press!!.from)
    }
}
