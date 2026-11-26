package io.nightcall.app.net

import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Network-level checks for [WsClient] without a phone: MockWebServer speaks the
 * server side of protocol v1. Verifies that `hello` is the first frame, that
 * `hello_ok` moves the state to Connected and that `ping` is answered with `pong`.
 */
class WsClientTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.close()
    }

    private fun enqueueSocket(listener: WebSocketListener) {
        server.enqueue(MockResponse.Builder().webSocketUpgrade(listener).build())
    }

    @Test
    fun helloIsFirstFrameAndHelloOkConnects() = runBlocking {
        val received = LinkedBlockingQueue<String>()
        enqueueSocket(
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    received.put(text)
                    if (Protocol.parseOrNull(text) is Hello) {
                        webSocket.send(HELLO_OK)
                    }
                }
            },
        )

        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val client = WsClient(scope)
        try {
            client.connect(server.url("/ws").toString(), "dev-token", "deadbeef")
            withTimeout(5_000) { client.state.first { it is ConnectionState.Connected } }

            val first = received.poll(5, TimeUnit.SECONDS)!!
            val hello = Protocol.parse(first)
            assertTrue("first frame must be hello, got $first", hello is Hello)
            assertEquals(Role.APP, (hello as Hello).role)
            assertEquals("deadbeef", hello.id)
            assertEquals("dev-token", hello.token)
        } finally {
            client.disconnect()
            scope.cancel()
        }
    }

    @Test
    fun serverPingIsAnsweredWithPong() = runBlocking {
        val pongs = LinkedBlockingQueue<String>()
        enqueueSocket(
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    when (Protocol.parseOrNull(text)) {
                        is Hello -> {
                            webSocket.send(HELLO_OK)
                            webSocket.send("""{"v":1,"t":"ping","t_epoch":1700000123}""")
                        }

                        is Pong -> pongs.put(text)
                        else -> Unit
                    }
                }
            },
        )

        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val client = WsClient(scope)
        try {
            client.connect(server.url("/ws").toString(), "dev-token", "deadbeef")
            withTimeout(5_000) { client.state.first { it is ConnectionState.Connected } }

            val pong = pongs.poll(5, TimeUnit.SECONDS)
            assertTrue("no pong received", pong != null)
            val parsed = Protocol.parse(pong!!)
            assertTrue("expected pong, got $pong", parsed is Pong)
            assertEquals(1700000123L, (parsed as Pong).tEpoch)
        } finally {
            client.disconnect()
            scope.cancel()
        }
    }

    private companion object {
        const val HELLO_OK =
            """{"v":1,"t":"hello_ok","session":"s-test","t_epoch":1700000000,""" +
                """"peer":{"id":"a1b2c3d4","online":true,"state":"ARMED"}}"""
    }
}
