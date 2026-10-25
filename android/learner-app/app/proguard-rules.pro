# kotlinx.serialization: keep generated serializers of the API models.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class com.book2course.learner.data.api.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.book2course.learner.data.api.**$$serializer { *; }
# Retrofit service interfaces are used through reflection.
-keep,allowobfuscation interface com.book2course.learner.data.api.LearnerApi
-keepattributes Signature, Exceptions
