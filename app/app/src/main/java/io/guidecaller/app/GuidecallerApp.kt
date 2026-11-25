package io.guidecaller.app

import android.app.Application
import io.guidecaller.app.data.SecureStore

class GuidecallerApp : Application() {

    /** Single process-wide store; the foreground service (S3.5) will share it. */
    val secureStore: SecureStore by lazy { SecureStore(this) }
}
