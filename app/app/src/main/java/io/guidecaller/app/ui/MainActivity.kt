package io.guidecaller.app.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.guidecaller.app.BuildConfig
import io.guidecaller.app.GuidecallerApp
import io.guidecaller.app.net.ConnectionState
import io.guidecaller.app.service.CommandBus
import io.guidecaller.app.service.CommandPhase
import io.guidecaller.app.service.PressItem
import io.guidecaller.app.service.SignalBus
import io.guidecaller.app.service.SignalReducer
import io.guidecaller.app.service.SignalService
import io.guidecaller.app.service.SignalState
import kotlinx.coroutines.delay

/**
 * S3.7 screen: server address, connect/disconnect, live status and recent presses.
 *
 * The socket lives in [SignalService]; the activity only starts/stops the service
 * and observes [SignalBus.state], so the session survives activity recreation and
 * the UI never owns a [io.guidecaller.app.net.WsClient].
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = (application as GuidecallerApp).secureStore
        // §4.1 requires a non-empty token even when AUTH_REQUIRED=false: fall back
        // to the build-time dev placeholder and remember it for the service.
        val token = store.token
            ?: BuildConfig.DEFAULT_TOKEN.takeIf { it.isNotBlank() }?.also { store.token = it }
        val appId = store.appId
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    StatusScreen(token = token, appId = appId)
                }
            }
        }
    }
}

@Composable
private fun StatusScreen(token: String?, appId: String) {
    val context = LocalContext.current
    var url by rememberSaveable { mutableStateOf(BuildConfig.DEFAULT_SERVER_URL) }
    val state by SignalBus.state.collectAsStateWithLifecycle()

    // Android 13+: ask for POST_NOTIFICATIONS before the service posts a press.
    // Denial does not block the socket — only the notification is lost — so the
    // pending start runs either way. Only the URL is kept: it survives recreation,
    // while token/appId come from the store on every activity instance.
    var pendingUrl by rememberSaveable { mutableStateOf<String?>(null) }
    val notificationPermission = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) {
        pendingUrl?.let { target -> token?.let { SignalService.start(context, target, it, appId) } }
        pendingUrl = null
    }

    val onConnect: () -> Unit = {
        val secret = token
        if (secret != null) {
            val target = url.trim()
            if (needsNotificationPermission(context)) {
                pendingUrl = target
                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                SignalService.start(context, target, secret, appId)
            }
        }
    }
    val onDisconnect: () -> Unit = { SignalService.stop(context) }

    Column(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "Guidecaller",
                    style = MaterialTheme.typography.headlineSmall,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = if (state.isConnected) "WS ONLINE" else "WS OFFLINE",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (state.isConnected) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }

            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text(text = "Server address (ws://host:8000/ws)") },
                singleLine = true,
                enabled = token != null,
            )

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = onConnect,
                    modifier = Modifier.weight(1f),
                    enabled = url.isNotBlank() && token != null,
                ) {
                    Text(text = "Connect")
                }
                OutlinedButton(
                    onClick = onDisconnect,
                    modifier = Modifier.weight(1f),
                    enabled = state.serviceRunning,
                ) {
                    Text(text = "Disconnect")
                }
            }

            Text(
                text = "app_id: $appId · token: " + if (token == null) "not provisioned" else "stored",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (token == null) {
                Text(
                    text = "Build with -Pguidecaller.token=… (never committed) to connect.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        HorizontalDivider()

        StatusSection(state = state)

        HorizontalDivider()

        CountdownSection(state = state)

        HorizontalDivider()

        PressesSection(presses = state.presses, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun StatusSection(state: SignalState) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = connectionText(state.connection),
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            text = "Button: " + if (state.deviceOnline) "online" else "offline",
            style = MaterialTheme.typography.bodyLarge,
        )
        Text(
            text = "Button state: " + buttonStateText(state.deviceState),
            style = MaterialTheme.typography.bodyLarge,
        )
        Text(
            text = "App: " + if (state.appOnline) "online" else "offline",
            style = MaterialTheme.typography.bodyLarge,
        )
        state.serverOffsetSec?.let { offset ->
            Text(
                text = "Server clock offset: " + if (offset >= 0) "+$offset s" else "$offset s",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        if (state.authFailed || (state.connection as? ConnectionState.Failed)?.reason == "auth_failed") {
            Banner(
                text = "AUTH FAIL",
                container = MaterialTheme.colorScheme.errorContainer,
                content = MaterialTheme.colorScheme.onErrorContainer,
            )
        }
        // §4.9 `no_peer` and an offline button are the same user-facing problem.
        if (state.lastError == SignalReducer.CODE_NO_PEER || (state.isConnected && !state.deviceOnline)) {
            Banner(
                text = "NO BUTTON",
                container = MaterialTheme.colorScheme.tertiaryContainer,
                content = MaterialTheme.colorScheme.onTertiaryContainer,
            )
        }
        state.lastError?.let { error ->
            Text(
                text = "Last error: $error",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun Banner(text: String, container: Color, content: Color) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = container,
        contentColor = content,
        shape = MaterialTheme.shapes.small,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
        )
    }
}

/**
 * S4: set a countdown on the button or cancel the running one.
 *
 * §4.4–4.6: the buttons stay in "waiting" until `ack(stage=delivered)`; after
 * 10 s without it the UI says the time was not set and allows a retry (the
 * retry gets a new `seq`, see [SignalService]).
 */
@Composable
private fun CountdownSection(state: SignalState) {
    var minutes by rememberSaveable { mutableStateOf("") }
    val minutesValue = minutes.toIntOrNull()
    val validMinutes = minutesValue != null && minutesValue in 1..MAX_TIMER_MINUTES
    val pending = state.command.phase == CommandPhase.PENDING
    val canCommand = state.isConnected && state.deviceOnline && !pending

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text = "Countdown", style = MaterialTheme.typography.titleMedium)

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for (preset in listOf(15, 30, 60)) {
                OutlinedButton(
                    onClick = { CommandBus.send(CommandBus.Command.SetTimer(preset * 60)) },
                    enabled = canCommand,
                    modifier = Modifier.weight(1f),
                ) {
                    Text(text = "$preset min")
                }
            }
        }

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            OutlinedTextField(
                value = minutes,
                onValueChange = { input -> minutes = input.filter(Char::isDigit).take(4) },
                modifier = Modifier.weight(1f),
                label = { Text(text = "Minutes (1–$MAX_TIMER_MINUTES)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                isError = minutes.isNotEmpty() && !validMinutes,
                enabled = canCommand,
            )
            Button(
                onClick = {
                    minutesValue?.let { CommandBus.send(CommandBus.Command.SetTimer(it * 60)) }
                    minutes = ""
                },
                enabled = canCommand && validMinutes,
            ) {
                Text(text = "Set timer")
            }
        }

        OutlinedButton(
            onClick = { CommandBus.send(CommandBus.Command.Cancel) },
            modifier = Modifier.fillMaxWidth(),
            enabled = canCommand && state.countdownActive,
        ) {
            Text(text = "Cancel timer")
        }

        CountdownStatus(state = state)
    }
}

@Composable
private fun CountdownStatus(state: SignalState) {
    val command = state.command
    when {
        command.phase == CommandPhase.PENDING -> Text(
            text = "Waiting for the button to confirm…",
            style = MaterialTheme.typography.bodyMedium,
        )

        command.phase == CommandPhase.FAILED -> Text(
            text = command.error ?: "Command failed.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.error,
        )

        state.deviceState == "ARRIVED" -> Text(
            text = "Time is up.",
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.primary,
        )

        state.countdownActive -> RemainingText(state = state)

        else -> Text(
            text = "No countdown is running.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Ticks locally from the last `status.remaining_s`, so the UI is smooth. */
@Composable
private fun RemainingText(state: SignalState) {
    val deadline = state.countdownEndsAtLocalEpochSec ?: return
    var now by remember(deadline) { mutableLongStateOf(nowEpochSec()) }
    LaunchedEffect(deadline) {
        while (true) {
            now = nowEpochSec()
            delay(1_000L)
        }
    }
    val left = deadline - now
    Text(
        text = if (left > 0) {
            "Remaining " + TimeFormat.duration(left)
        } else {
            // The button flips to ARRIVED by itself; the server status follows.
            "Time is up."
        },
        style = MaterialTheme.typography.titleSmall,
    )
}

@Composable
private fun PressesSection(presses: List<PressItem>, modifier: Modifier = Modifier) {
    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Recent presses",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = presses.size.toString(),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        if (presses.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "No presses yet.\nPress the button — it will show up here.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                contentPadding = PaddingValues(horizontal = 20.dp),
            ) {
                items(items = presses) { press ->
                    PressRow(press = press)
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
private fun PressRow(press: PressItem) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "#${press.seq}",
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.width(64.dp),
        )
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "server " + (press.serverEpoch?.let(TimeFormat::hhmmss) ?: "—"),
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = "local  " + TimeFormat.hhmmss(press.localEpoch),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            text = press.from ?: "—",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** §4.4 allows 60 s … 86400 s, i.e. 1 … 1440 minutes. */
private const val MAX_TIMER_MINUTES = 1440

private fun nowEpochSec(): Long = System.currentTimeMillis() / 1000

private val SignalState.serviceRunning: Boolean
    get() = connection != ConnectionState.Idle && connection != ConnectionState.Disconnected

/** `WAIT_ETA` → `WAIT ETA`; protocol states are upper snake case. */
private fun buttonStateText(state: String): String = state.replace('_', ' ')

private fun needsNotificationPermission(context: Context): Boolean =
    Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) != PackageManager.PERMISSION_GRANTED

private fun connectionText(state: ConnectionState): String = when (state) {
    ConnectionState.Idle -> "WS: idle"
    is ConnectionState.Connecting -> "WS: connecting (attempt ${state.attempt + 1})"
    is ConnectionState.Connected -> "WS: connected (session ${state.session})"
    is ConnectionState.Reconnecting ->
        "WS: reconnecting in ${(state.delayMs + 999) / 1000} s (attempt ${state.attempt})"
    is ConnectionState.Failed ->
        if (state.reason == "auth_failed") "AUTH FAIL" else "WS: failed — ${state.reason}"
    ConnectionState.Disconnected -> "WS: disconnected"
}
