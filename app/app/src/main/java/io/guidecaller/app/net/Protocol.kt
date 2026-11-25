package io.guidecaller.app.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * WS protocol v1 — see plan/schema/messages.schema.json and drafts/Протокол.md §4.
 *
 * Strictness rules (mirroring the JSON Schema):
 *  - every message has `v` = 1 and `t` = message type (`t` is the polymorphic discriminator);
 *  - unknown fields are rejected (`additionalProperties: false`);
 *  - required fields have no defaults, so a missing one fails decoding;
 *  - ranges/patterns from the schema are checked in `init` blocks;
 *  - enum wire names are the exact schema strings.
 */
object Protocol {

    const val VERSION = 1

    /** Transport limit from drafts/Протокол.md §1: a frame must not exceed 4 KiB. */
    const val MAX_MESSAGE_BYTES = 4096

    /** drafts/Протокол.md §4.1 + plan/app.md: no `hello_ok` within this window → reconnect. */
    const val HELLO_TIMEOUT_MS = 10_000L

    /** drafts/Протокол.md §4.6: `timer` waits for `ack(stage=delivered)` no longer than this. */
    const val ACK_TIMEOUT_MS = 10_000L

    /** drafts/Протокол.md §4.4. */
    const val MIN_TIMER_DURATION_S = 60
    const val MAX_TIMER_DURATION_S = 86_400

    /** drafts/Протокол.md §5 — WebSocket close codes. */
    const val CLOSE_NORMAL = 1000
    const val CLOSE_PROTOCOL_ERROR = 4400
    const val CLOSE_AUTH_FAILED = 4401
    const val CLOSE_INTERNAL = 1011

    private val HEX_ID = Regex("^[0-9a-f]{8}$")

    /**
     * `t` is the discriminator, defaults are always written, and nulls of optional
     * fields are omitted (their absence is the wire representation of "not set").
     */
    val json: Json = Json {
        classDiscriminator = "t"
        ignoreUnknownKeys = false
        encodeDefaults = true
        explicitNulls = false
    }

    fun encode(message: Message): String =
        json.encodeToString(Message.serializer(), message)

    fun parse(text: String): Message =
        json.decodeFromString(Message.serializer(), text)

    fun parseOrNull(text: String): Message? =
        runCatching { parse(text) }.getOrNull()

    fun isHexId(value: String): Boolean = HEX_ID.matches(value)

    // --- builders for messages the app originates -------------------------------

    /** App side of `hello` (§4.1): the first frame on every connection. */
    fun helloApp(appId: String, token: String): Hello =
        Hello(v = VERSION, role = Role.APP, token = token, id = appId)

    /** `pong` echoes the server `ping` (§4.8). */
    fun pong(tEpoch: Long): Pong = Pong(v = VERSION, tEpoch = tEpoch)

    /** `ack(seq, ok=true, stage=delivered)` (§4.6). */
    fun ackDelivered(seq: Long): Ack =
        Ack(v = VERSION, seq = seq, ok = true, stage = AckStage.DELIVERED)

    fun timer(seq: Long, durationS: Int, tEpoch: Long, deadlineT: Long? = null): TimerCommand =
        TimerCommand(
            v = VERSION,
            seq = seq,
            durationS = durationS,
            tEpoch = tEpoch,
            deadlineT = deadlineT,
        )

    fun cancel(seq: Long, tEpoch: Long): CancelMessage =
        CancelMessage(v = VERSION, seq = seq, tEpoch = tEpoch)
}

// --- enums ----------------------------------------------------------------------

@Serializable
enum class Role {
    @SerialName("device")
    DEVICE,

    @SerialName("app")
    APP,
}

/** Button state machine; also used for `peer.state` / `status.device.state`. */
@Serializable
enum class ButtonState {
    BOOT,
    OFFLINE,
    ARMED,
    SENT,
    WAIT_ETA,
    COUNTDOWN,
    ARRIVED,
    NO_ACK,
}

@Serializable
enum class AckStage {
    @SerialName("server")
    SERVER,

    @SerialName("delivered")
    DELIVERED,
}

@Serializable
enum class ErrorCode {
    @SerialName("auth_failed")
    AUTH_FAILED,

    @SerialName("bad_request")
    BAD_REQUEST,

    @SerialName("bad_duration")
    BAD_DURATION,

    @SerialName("no_peer")
    NO_PEER,

    @SerialName("duplicate")
    DUPLICATE,

    @SerialName("internal")
    INTERNAL,
}

// --- shared validation helpers ---------------------------------------------------

private fun requireVersion(v: Int) {
    require(v == Protocol.VERSION) { "v must be ${Protocol.VERSION}, got $v" }
}

private fun requireHexId(id: String) {
    require(Protocol.isHexId(id)) { "id must be 8 lowercase hex chars: $id" }
}

private fun requireSeq(seq: Long) {
    require(seq >= 1) { "seq must be >= 1, got $seq" }
}

private fun requireEpoch(tEpoch: Long) {
    require(tEpoch >= 0) { "t_epoch must be >= 0, got $tEpoch" }
}

// --- messages -------------------------------------------------------------------

@Serializable
sealed interface Message {
    val v: Int
}

/** §4.1 — client → server. */
@Serializable
@SerialName("hello")
data class Hello(
    @SerialName("v") override val v: Int,
    @SerialName("role") val role: Role,
    @SerialName("token") val token: String,
    @SerialName("id") val id: String,
    @SerialName("fw") val fw: String? = null,
) : Message {
    init {
        requireVersion(v)
        require(token.isNotEmpty()) { "token must not be empty" }
        requireHexId(id)
    }
}

@Serializable
data class Peer(
    @SerialName("id") val id: String,
    @SerialName("online") val online: Boolean,
    @SerialName("state") val state: ButtonState,
    @SerialName("remaining_s") val remainingSeconds: Long? = null,
) {
    init {
        requireHexId(id)
        remainingSeconds?.let { require(it >= 0) { "remaining_s must be >= 0" } }
    }
}

/** §4.2 — server → client, also proves the token and resets the backoff counter. */
@Serializable
@SerialName("hello_ok")
data class HelloOk(
    @SerialName("v") override val v: Int,
    @SerialName("session") val session: String,
    @SerialName("t_epoch") val tEpoch: Long,
    @SerialName("peer") val peer: Peer,
) : Message {
    init {
        requireVersion(v)
        requireEpoch(tEpoch)
    }
}

/** §4.3 — button → server → app; `from` is added by the server on forward. */
@Serializable
@SerialName("press")
data class Press(
    @SerialName("v") override val v: Int,
    @SerialName("seq") val seq: Long,
    @SerialName("t_epoch") val tEpoch: Long,
    @SerialName("from") val from: String? = null,
) : Message {
    init {
        requireVersion(v)
        requireSeq(seq)
        requireEpoch(tEpoch)
        from?.let(::requireHexId)
    }
}

/** §4.4 — app → server → button; `deadline_t` is added by the server on forward. */
@Serializable
@SerialName("timer")
data class TimerCommand(
    @SerialName("v") override val v: Int,
    @SerialName("seq") val seq: Long,
    @SerialName("duration_s") val durationS: Int,
    @SerialName("t_epoch") val tEpoch: Long,
    @SerialName("deadline_t") val deadlineT: Long? = null,
) : Message {
    init {
        requireVersion(v)
        requireSeq(seq)
        require(
            durationS in Protocol.MIN_TIMER_DURATION_S..Protocol.MAX_TIMER_DURATION_S,
        ) { "duration_s must be ${Protocol.MIN_TIMER_DURATION_S}..${Protocol.MAX_TIMER_DURATION_S}, got $durationS" }
        requireEpoch(tEpoch)
        deadlineT?.let { require(it >= 0) { "deadline_t must be >= 0" } }
    }
}

/** §4.5 — app → server → button. */
@Serializable
@SerialName("cancel")
data class CancelMessage(
    @SerialName("v") override val v: Int,
    @SerialName("seq") val seq: Long,
    @SerialName("t_epoch") val tEpoch: Long,
) : Message {
    init {
        requireVersion(v)
        requireSeq(seq)
        requireEpoch(tEpoch)
    }
}

/** §4.6 — both directions. */
@Serializable
@SerialName("ack")
data class Ack(
    @SerialName("v") override val v: Int,
    @SerialName("seq") val seq: Long,
    @SerialName("ok") val ok: Boolean,
    @SerialName("stage") val stage: AckStage,
) : Message {
    init {
        requireVersion(v)
        requireSeq(seq)
    }
}

@Serializable
data class DeviceStatus(
    @SerialName("online") val online: Boolean,
    @SerialName("state") val state: ButtonState,
    @SerialName("remaining_s") val remainingSeconds: Long? = null,
    @SerialName("fw") val fw: String? = null,
) {
    init {
        remainingSeconds?.let { require(it >= 0) { "remaining_s must be >= 0" } }
    }
}

@Serializable
data class AppStatus(
    @SerialName("online") val online: Boolean,
)

/** §4.7 — server → both. */
@Serializable
@SerialName("status")
data class StatusMessage(
    @SerialName("v") override val v: Int,
    @SerialName("t_epoch") val tEpoch: Long,
    @SerialName("device") val device: DeviceStatus,
    @SerialName("app") val app: AppStatus,
) : Message {
    init {
        requireVersion(v)
        requireEpoch(tEpoch)
    }
}

/** §4.8 — server heartbeat; the client replies with [Pong] within 10 s. */
@Serializable
@SerialName("ping")
data class Ping(
    @SerialName("v") override val v: Int,
    @SerialName("t_epoch") val tEpoch: Long,
) : Message {
    init {
        requireVersion(v)
        requireEpoch(tEpoch)
    }
}

@Serializable
@SerialName("pong")
data class Pong(
    @SerialName("v") override val v: Int,
    @SerialName("t_epoch") val tEpoch: Long,
) : Message {
    init {
        requireVersion(v)
        requireEpoch(tEpoch)
    }
}

/** §4.9 — server → client. */
@Serializable
@SerialName("error")
data class ErrorMessage(
    @SerialName("v") override val v: Int,
    @SerialName("code") val code: ErrorCode,
    @SerialName("msg") val msg: String,
    @SerialName("seq") val seq: Long? = null,
) : Message {
    init {
        requireVersion(v)
        seq?.let(::requireSeq)
    }
}
