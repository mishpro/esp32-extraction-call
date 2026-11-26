package io.nightcall.app.net

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Contract tests against plan/schema/messages.schema.json and drafts/Протокол.md §4.
 * All 13 JSON examples from §4 are parsed, re-encoded and compared structurally.
 */
class ProtocolTest {

    private val examples: List<Pair<String, String>> = listOf(
        "4.1 hello (device)" to
            """{ "v": 1, "t": "hello", "role": "device", "token": "9f2c…", "id": "a1b2c3d4", "fw": "1.0.0" }""",
        "4.1 error auth_failed" to
            """{ "v": 1, "t": "error", "code": "auth_failed", "msg": "bad token" }""",
        "4.2 hello_ok" to
            """{
              "v": 1,
              "t": "hello_ok",
              "session": "s-7f3a91",
              "t_epoch": 1793001234,
              "peer": { "id": "a1b2c3d4", "online": true, "state": "ARMED", "remaining_s": 10800 }
            }""",
        "4.3 press (button)" to
            """{ "v": 1, "t": "press", "seq": 7, "t_epoch": 1793001234 }""",
        "4.3 press (forwarded)" to
            """{ "v": 1, "t": "press", "seq": 7, "t_epoch": 1793001234, "from": "a1b2c3d4" }""",
        "4.4 timer (app)" to
            """{ "v": 1, "t": "timer", "seq": 3, "duration_s": 10800, "t_epoch": 1793001300 }""",
        "4.4 timer (forwarded)" to
            """{ "v": 1, "t": "timer", "seq": 3, "duration_s": 10800, "deadline_t": 1793012100, "t_epoch": 1793001300 }""",
        "4.5 cancel" to
            """{ "v": 1, "t": "cancel", "seq": 4, "t_epoch": 1793001400 }""",
        "4.6 ack" to
            """{ "v": 1, "t": "ack", "seq": 7, "ok": true, "stage": "server" }""",
        "4.7 status" to
            """{
              "v": 1,
              "t": "status",
              "t_epoch": 1793001500,
              "device": { "online": true, "state": "COUNTDOWN", "remaining_s": 10412, "fw": "1.0.0" },
              "app":    { "online": true }
            }""",
        "4.8 ping" to
            """{ "v": 1, "t": "ping", "t_epoch": 1793001600 }""",
        "4.8 pong" to
            """{ "v": 1, "t": "pong", "t_epoch": 1793001600 }""",
        "4.9 error bad_duration" to
            """{ "v": 1, "t": "error", "code": "bad_duration", "msg": "duration_s out of range", "seq": 3 }""",
    )

    @Test
    fun `all 13 protocol examples round trip byte-for-byte in JSON structure`() {
        assertEquals(13, examples.size)
        for ((name, raw) in examples) {
            val parsed = Protocol.parse(raw)
            val reencoded = Protocol.encode(parsed)
            val reparsed = Protocol.parse(reencoded)
            assertEquals("$name: model equality after round trip", parsed, reparsed)
            assertEquals(
                "$name: JSON structure after round trip",
                Json.parseToJsonElement(raw),
                Json.parseToJsonElement(reencoded),
            )
        }
    }

    @Test
    fun `examples map to the expected message subclasses`() {
        assertTrue(Protocol.parse(examples[0].second) is Hello)
        assertTrue(Protocol.parse(examples[1].second) is ErrorMessage)
        assertTrue(Protocol.parse(examples[2].second) is HelloOk)
        assertTrue(Protocol.parse(examples[3].second) is Press)
        assertTrue(Protocol.parse(examples[4].second) is Press)
        assertTrue(Protocol.parse(examples[5].second) is TimerCommand)
        assertTrue(Protocol.parse(examples[6].second) is TimerCommand)
        assertTrue(Protocol.parse(examples[7].second) is CancelMessage)
        assertTrue(Protocol.parse(examples[8].second) is Ack)
        assertTrue(Protocol.parse(examples[9].second) is StatusMessage)
        assertTrue(Protocol.parse(examples[10].second) is Ping)
        assertTrue(Protocol.parse(examples[11].second) is Pong)
        assertTrue(Protocol.parse(examples[12].second) is ErrorMessage)
    }

    @Test
    fun `hello_ok carries peer state and server time`() {
        val helloOk = Protocol.parse(examples[2].second) as HelloOk
        assertEquals("s-7f3a91", helloOk.session)
        assertEquals(1_793_001_234L, helloOk.tEpoch)
        assertEquals("a1b2c3d4", helloOk.peer.id)
        assertTrue(helloOk.peer.online)
        assertEquals(ButtonState.ARMED, helloOk.peer.state)
        assertEquals(10_800L, helloOk.peer.remainingSeconds)
    }

    @Test
    fun `status carries device and app liveness`() {
        val status = Protocol.parse(examples[9].second) as StatusMessage
        assertTrue(status.device.online)
        assertEquals(ButtonState.COUNTDOWN, status.device.state)
        assertEquals(10_412L, status.device.remainingSeconds)
        assertEquals("1.0.0", status.device.fw)
        assertTrue(status.app.online)
        assertEquals(1_793_001_500L, status.tEpoch)
    }

    @Test
    fun `helloApp sends role app and omits fw`() {
        val encoded = Protocol.encode(Protocol.helloApp(appId = "a1b2c3d4", token = "secret"))
        val json = Json.parseToJsonElement(encoded).jsonObject
        assertEquals(1, json["v"]?.jsonPrimitive?.content?.toInt())
        assertEquals("hello", json["t"]?.jsonPrimitive?.content)
        assertEquals("app", json["role"]?.jsonPrimitive?.content)
        assertEquals("a1b2c3d4", json["id"]?.jsonPrimitive?.content)
        assertEquals("secret", json["token"]?.jsonPrimitive?.content)
        assertFalse("fw must not be present for an app hello", json.containsKey("fw"))
    }

    @Test
    fun `ackDelivered and pong builders match the schema`() {
        val ack = Json.parseToJsonElement(Protocol.encode(Protocol.ackDelivered(seq = 7))).jsonObject
        assertEquals("ack", ack["t"]?.jsonPrimitive?.content)
        assertEquals("true", ack["ok"]?.jsonPrimitive?.content)
        assertEquals("delivered", ack["stage"]?.jsonPrimitive?.content)

        val pong = Json.parseToJsonElement(Protocol.encode(Protocol.pong(tEpoch = 1_793_001_600))).jsonObject
        assertEquals("pong", pong["t"]?.jsonPrimitive?.content)
        assertEquals(1_793_001_600L, pong["t_epoch"]?.jsonPrimitive?.content?.toLong())
    }

    @Test
    fun `optional fields stay absent when they are not set`() {
        val press = Protocol.parse("""{ "v": 1, "t": "press", "seq": 1, "t_epoch": 10 }""") as Press
        assertFalse(Protocol.encode(press).contains("\"from\""))

        val error = Protocol.parse("""{ "v": 1, "t": "error", "code": "no_peer", "msg": "no button" }""") as ErrorMessage
        assertFalse(Protocol.encode(error).contains("\"seq\""))
    }

    @Test
    fun `ping can be answered with the same t_epoch`() {
        val ping = Protocol.parse(examples[10].second) as Ping
        val pong = Protocol.parse(Protocol.encode(Protocol.pong(ping.tEpoch))) as Pong
        assertEquals(ping.tEpoch, pong.tEpoch)
    }

    @Test
    fun `unknown fields are rejected`() {
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "ping", "t_epoch": 1, "extra": true }"""))
        assertNull(
            Protocol.parseOrNull(
                """{ "v": 1, "t": "hello_ok", "session": "s", "t_epoch": 1,
                   "peer": { "id": "a1b2c3d4", "online": true, "state": "ARMED", "x": 1 } }""",
            ),
        )
        assertNull(
            Protocol.parseOrNull(
                """{ "v": 1, "t": "status", "t_epoch": 1,
                   "device": { "online": true, "state": "ARMED" }, "app": { "online": true, "x": 1 } }""",
            ),
        )
    }

    @Test
    fun `unknown message type is rejected`() {
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "nope", "t_epoch": 1 }"""))
    }

    @Test
    fun `wrong protocol version is rejected`() {
        assertNull(Protocol.parseOrNull("""{ "v": 2, "t": "ping", "t_epoch": 1 }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 0, "t": "pong", "t_epoch": 1 }"""))
    }

    @Test
    fun `missing required fields are rejected`() {
        // no token
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello", "role": "app", "id": "a1b2c3d4" }"""))
        // no peer
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello_ok", "session": "s", "t_epoch": 1 }"""))
        // no t_epoch
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "press", "seq": 1 }"""))
        // no ack stage
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "ack", "seq": 1, "ok": true }"""))
    }

    @Test
    fun `wrong field types are rejected`() {
        // Note: kotlinx-serialization accepts a number encoded as a JSON string ("7" → 7);
        // full schema validation stays on the server (plan/schema/README.md), so this is
        // a documented leniency, not a protocol guarantee.
        assertEquals(7L, (Protocol.parse("""{ "v": 1, "t": "press", "seq": "7", "t_epoch": 1 }""") as Press).seq)
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "ack", "seq": 1, "ok": 1, "stage": "server" }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "ping", "t_epoch": 1.5 }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "ping", "t_epoch": null }"""))
    }

    @Test
    fun `schema constraints on ids and seqs are enforced`() {
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello", "role": "app", "token": "t", "id": "A1B2C3D4" }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello", "role": "app", "token": "t", "id": "a1b2c3d" }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello", "role": "app", "token": "t", "id": "a1b2c3d4x" }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello", "role": "app", "token": "", "id": "a1b2c3d4" }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "press", "seq": 0, "t_epoch": 1 }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "press", "seq": 1, "t_epoch": -1 }"""))
        assertNull(Protocol.parseOrNull("""{ "v": 1, "t": "hello", "role": "bot", "token": "t", "id": "a1b2c3d4" }"""))
    }

    @Test
    fun `timer duration is limited to 60 through 86400 seconds`() {
        fun timer(durationS: Int) =
            """{ "v": 1, "t": "timer", "seq": 1, "duration_s": $durationS, "t_epoch": 1 }"""

        assertTrue(Protocol.parseOrNull(timer(60)) is TimerCommand)
        assertTrue(Protocol.parseOrNull(timer(86_400)) is TimerCommand)
        assertNull(Protocol.parseOrNull(timer(59)))
        assertNull(Protocol.parseOrNull(timer(86_401)))
        assertNull(Protocol.parseOrNull(timer(0)))
    }

    @Test
    fun `all button states serialize to schema names`() {
        val expected = listOf(
            "BOOT", "OFFLINE", "ARMED", "SENT", "WAIT_ETA", "COUNTDOWN", "ARRIVED", "NO_ACK",
        )
        assertEquals(
            expected,
            ButtonState.entries.map { wireName(Protocol.json.encodeToString(ButtonState.serializer(), it)) },
        )
        assertNull(
            runCatching {
                Protocol.json.decodeFromString(ButtonState.serializer(), "\"NO_APP\"")
            }.getOrNull(),
        )
    }

    @Test
    fun `all error codes serialize to schema names`() {
        val expected = listOf(
            "auth_failed", "bad_request", "bad_duration", "no_peer", "duplicate", "internal",
        )
        assertEquals(
            expected,
            ErrorCode.entries.map { wireName(Protocol.json.encodeToString(ErrorCode.serializer(), it)) },
        )
        assertNull(
            runCatching {
                Protocol.json.decodeFromString(ErrorCode.serializer(), "\"unknown\"")
            }.getOrNull(),
        )
    }

    @Test
    fun `ack stages serialize to schema names`() {
        assertEquals(
            listOf("server", "delivered"),
            AckStage.entries.map { wireName(Protocol.json.encodeToString(AckStage.serializer(), it)) },
        )
    }

    private fun wireName(jsonString: String): String =
        Json.parseToJsonElement(jsonString).jsonPrimitive.content
}
