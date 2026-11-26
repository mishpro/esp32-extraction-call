package io.nightcall.app.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.SecureRandom

/**
 * Secret app state from drafts/Протокол.md §3:
 *  - `app_id` — 8 lowercase hex chars, generated once on first use, stable across restarts;
 *  - `token` — shared secret for `hello`, never written in plain text.
 *
 * Both live in [EncryptedSharedPreferences] (AES256-GCM value encryption,
 * AES256-SIV keys), backed by a Tink master key in the Android Keystore.
 */
class SecureStore(context: Context) {

    private val appContext: Context = context.applicationContext

    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(appContext)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            appContext,
            FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    /** Stable identifier of this app installation (not a secret). */
    val appId: String
        get() = synchronized(this) {
            prefs.getString(KEY_APP_ID, null) ?: newAppId().also {
                prefs.edit().putString(KEY_APP_ID, it).apply()
            }
        }

    /** Shared secret for `hello`; null until provisioned (config/settings, S4+). */
    var token: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrEmpty()) remove(KEY_TOKEN) else putString(KEY_TOKEN, value)
            }.apply()
        }

    private fun newAppId(): String {
        val bytes = ByteArray(4)
        SecureRandom().nextBytes(bytes)
        return buildString(APP_ID_LENGTH) {
            for (b in bytes) {
                val v = b.toInt() and 0xFF
                append(HEX[v ushr 4])
                append(HEX[v and 0x0F])
            }
        }
    }

    private companion object {
        const val FILE_NAME = "nightcall_secure"
        const val KEY_APP_ID = "app_id"
        const val KEY_TOKEN = "token"
        const val APP_ID_LENGTH = 8
        const val HEX = "0123456789abcdef"
    }
}
