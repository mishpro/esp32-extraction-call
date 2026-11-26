package io.nightcall.app.notify

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import io.nightcall.app.R
import io.nightcall.app.service.PressItem
import io.nightcall.app.service.SignalReducer
import io.nightcall.app.ui.MainActivity

/**
 * Notification channels and notifications of the app (drafts/Протокол.md §9).
 *
 *  - [CHANNEL_SIGNALS] (`signals`, IMPORTANCE_HIGH) — a press from the button;
 *  - [CHANNEL_STATUS] (`status`, IMPORTANCE_LOW) — the foreground service notification.
 *
 * Android 13+ needs `POST_NOTIFICATIONS` at runtime. `notify()` is a no-op (never a
 * crash) while the user has not granted it: [areNotificationsEnabled] is checked
 * first and the call is guarded anyway, because a press must still be acked (§4.6)
 * even when it cannot be shown.
 */
object Notifications {

    const val CHANNEL_SIGNALS = "signals"
    const val CHANNEL_STATUS = "status"

    /** One ongoing notification for the foreground service. */
    const val SERVICE_NOTIFICATION_ID = 1

    /** Press notifications get distinct ids above this, so they stack instead of replacing each other. */
    private const val PRESS_ID_BASE = 100

    private const val REQUEST_OPEN_APP = 0

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val signals = NotificationChannel(
            CHANNEL_SIGNALS,
            context.getString(R.string.channel_signals_name),
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = context.getString(R.string.channel_signals_description)
        }
        val status = NotificationChannel(
            CHANNEL_STATUS,
            context.getString(R.string.channel_status_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = context.getString(R.string.channel_status_description)
        }
        manager.createNotificationChannels(listOf(signals, status))
    }

    /** `POST_NOTIFICATIONS` is granted (or the device predates runtime notifications). */
    fun areNotificationsEnabled(context: Context): Boolean =
        NotificationManagerCompat.from(context).areNotificationsEnabled()

    /** Ongoing, silent notification that keeps [io.nightcall.app.service.SignalService] in the foreground. */
    fun serviceNotification(context: Context): Notification =
        NotificationCompat.Builder(context, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_signal)
            .setContentTitle(context.getString(R.string.app_name))
            .setContentText(context.getString(R.string.service_notification_text))
            .setContentIntent(openAppIntent(context))
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .build()

    /**
     * Shows "Вызов" with the server time of the press (§4.6, channel `signals`).
     * The time is `t_epoch` from the server, formatted `HH:mm:ss`; the local
     * receive time is only a fallback.
     */
    fun showPress(context: Context, press: PressItem) {
        if (!areNotificationsEnabled(context)) return
        val time = SignalReducer.formatTime(press.serverEpoch ?: press.localEpoch)
        val notification = NotificationCompat.Builder(context, CHANNEL_SIGNALS)
            .setSmallIcon(R.drawable.ic_signal)
            .setContentTitle(context.getString(R.string.press_notification_title))
            .setContentText(context.getString(R.string.press_notification_text, time))
            .setContentIntent(openAppIntent(context))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setShowWhen(false)
            .build()
        // The permission can be revoked between the check and the call.
        runCatching { NotificationManagerCompat.from(context).notify(pressNotificationId(press.seq), notification) }
    }

    /** Stable id per `seq`, so a re-delivered duplicate (dropped by the reducer) also cannot duplicate. */
    fun pressNotificationId(seq: Long): Int = PRESS_ID_BASE + (seq.hashCode() and 0x7FFF)

    private fun openAppIntent(context: Context): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or
                Intent.FLAG_ACTIVITY_CLEAR_TOP or
                Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        return PendingIntent.getActivity(
            context,
            REQUEST_OPEN_APP,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
