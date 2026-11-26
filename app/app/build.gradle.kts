plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.google.services)
}

// Dev defaults, never secrets in VCS: override with -Pnightcall.url=… / -Pnightcall.token=…
val devServerUrl: String = providers.gradleProperty("nightcall.url")
    .orElse("ws://192.168.1.100:8000/ws")
    .get()
// Non-empty placeholder: §4.1 requires token >= 1 char even when AUTH_REQUIRED=false.
// A real secret is provisioned later (S5/S6) and never committed.
val devToken: String = providers.gradleProperty("nightcall.token").orElse("dev-token").get()

fun quoted(value: String): String =
    "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    namespace = "io.nightcall.app"
    // OkHttp 5.5.1 and the current AndroidX/Compose artifacts require compiling against API 37.
    // targetSdk stays 36 (plan/app.md); compileSdk only changes which SDK stubs are visible.
    compileSdk = 37
    compileSdkMinor = 2

    defaultConfig {
        applicationId = "io.nightcall.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField("String", "DEFAULT_SERVER_URL", quoted(devServerUrl))
        buildConfigField("String", "DEFAULT_TOKEN", quoted(devToken))
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.security.crypto)

    // S6: FCM when the process is killed. The BOM fixes the messaging version.
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.mockwebserver)
}
