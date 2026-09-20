# kotlinx.serialization keeps its generated serializers off the entry graph
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class com.baestheorem.inbox.** {
    *** Companion;
}
-keepclasseswithmembers class com.baestheorem.inbox.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.baestheorem.inbox.**$$serializer { *; }

# OkHttp's optional platform hooks
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# Tink (pulled in by androidx.security-crypto) compiles against Error Prone
# annotations that are not on the runtime classpath
-dontwarn com.google.errorprone.annotations.**
-dontwarn javax.annotation.**
