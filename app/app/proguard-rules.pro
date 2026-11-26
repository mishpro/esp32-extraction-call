# Keep protocol data classes and their serializers (kotlinx-serialization uses them reflectively).
-keepclassmembers class io.nightcall.app.net.** {
    *** Companion;
}
-keepclasseswithmembers class io.nightcall.app.net.** {
    kotlinx.serialization.KSerializer serializer(...);
}
