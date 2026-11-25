package io.guidecaller.app.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.guidecaller.app.BuildConfig
import io.guidecaller.app.GuidecallerApp
import io.guidecaller.app.data.SecureStore
import io.guidecaller.app.net.ConnectionState
import io.guidecaller.app.net.WsClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel

/**
 * Minimal S3 screen (steps 3.1/3.4): server address, WS status, connect/disconnect.
 * No press list and no timer input — that is S3.7/S4.
 *
 * The socket is owned by the activity scope only until `SignalService` exists (S3.5);
 * then the UI will subscribe to the service's StateFlow instead.
 */
class MainActivity : ComponentActivity() {

    private val clientScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val wsClient: WsClient by lazy { WsClient(scope = clientScope) }

    private lateinit var store: SecureStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = (application as GuidecallerApp).secureStore
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ConnectScreen(store = store, client = wsClient)
                }
            }
        }
    }

    override fun onDestroy() {
        wsClient.disconnect()
        clientScope.cancel()
        super.onDestroy()
    }
}

@Composable
private fun ConnectScreen(store: SecureStore, client: WsClient) {
    var url by rememberSaveable { mutableStateOf(BuildConfig.DEFAULT_SERVER_URL) }
    val token = remember {
        store.token ?: BuildConfig.DEFAULT_TOKEN.takeIf { it.isNotBlank() }?.also {
            store.token = it
        }
    }
    val appId = remember { store.appId }
    val connection by client.state.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(text = "Guidecaller", style = MaterialTheme.typography.headlineSmall)

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text(text = "Server address (ws://host:8000/ws)") },
            singleLine = true,
        )

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { token?.let { client.connect(url.trim(), it, appId) } },
                enabled = url.isNotBlank() && token != null,
            ) {
                Text(text = "Connect")
            }
            OutlinedButton(onClick = { client.disconnect() }) {
                Text(text = "Disconnect")
            }
        }

        Text(
            text = connectionText(connection),
            style = MaterialTheme.typography.titleMedium,
        )

        HorizontalDivider()

        Text(text = "app_id: $appId", style = MaterialTheme.typography.bodyMedium)
        Text(
            text = if (token == null) "token: not provisioned" else "token: stored",
            style = MaterialTheme.typography.bodySmall,
        )
        if (token == null) {
            Text(
                text = "Build with -Pguidecaller.token=… (never committed) to connect.",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

private fun connectionText(state: ConnectionState): String = when (state) {
    ConnectionState.Idle -> "WS: idle"
    is ConnectionState.Connecting -> "WS: connecting (attempt ${state.attempt + 1})"
    is ConnectionState.Connected -> "WS: connected (session ${state.session})"
    is ConnectionState.Reconnecting ->
        "WS: reconnecting in ${(state.delayMs + 999) / 1000} s (attempt ${state.attempt})"
    is ConnectionState.Failed ->
        if (state.reason == "auth_failed") "AUTH FAIL" else "WS: failed - ${state.reason}"
    ConnectionState.Disconnected -> "WS: disconnected"
}
