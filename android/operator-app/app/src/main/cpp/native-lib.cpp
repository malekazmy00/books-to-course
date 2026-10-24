#include <jni.h>
extern "C" JNIEXPORT jstring JNICALL Java_com_book2course_operator_MainActivity_nativeAbiLabel(JNIEnv *env, jobject) {
#if defined(__arm__) && !defined(__aarch64__)
 return env->NewStringUTF("ARM 32-bit (armeabi-v7a)");
#else
 return env->NewStringUTF("Unsupported ABI");
#endif
}
