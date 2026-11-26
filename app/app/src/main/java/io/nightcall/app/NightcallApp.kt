package io.nightcall.app

import android.app.Application
import io.nightcall.app.data.SecureStore

class NightcallApp : Application() {

    /** Single process-wide store; the foreground service (S3.5) will share it. */
    val secureStore: SecureStore by lazy { SecureStore(this) }
}
