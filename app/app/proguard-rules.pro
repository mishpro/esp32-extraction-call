# Keep protocol data classes and their serializers (kotlinx-serialization uses them reflectively).
-keepclassmembers class io.guidecaller.app.net.** {
    *** Companion;
}
-keepclasseswithmembers class io.guidecaller.app.net.** {
    kotlinx.serialization.KSerializer serializer(...);
}
