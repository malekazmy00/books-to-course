import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// The tailnet API origin (D-030) may be preset at build time; the learner can change it in setup.
val defaultApiBaseUrl: String = System.getenv("B2C_API_BASE_URL") ?: ""
val learnerOpenApi = rootProject.file("../../docs/api/learner-v3.openapi.json")
val operatorOpenApi = rootProject.file("../../docs/api/operator-v3.openapi.json")

// Robolectric downloads its Android framework jar at test time with its own HTTP client, which
// cannot reach Maven from this build host. Resolve it through Gradle instead (cached like any
// dependency; test-only) and run Robolectric offline against that directory. One configuration per
// SDK, because inside a single configuration Gradle would resolve the android-all versions to one.
// Versions: Robolectric 4.14.1 DefaultSdkProvider, preinstrumented "i7".
val robolectricAndroidAll = mapOf(
    "Sdk24" to "org.robolectric:android-all-instrumented:7.0.0_r1-robolectric-r1-i7", // Android 7.0 (minSdk)
    "Sdk25" to "org.robolectric:android-all-instrumented:7.1.0_r7-robolectric-r1-i7", // Android 7.1
    "Sdk35" to "org.robolectric:android-all-instrumented:15-robolectric-12650502-i7", // default SDK
).map { (sdk, coordinate) ->
    configurations.create("robolectricAndroidAll$sdk").also { dependencies.add(it.name, coordinate) }
}
val robolectricDepsDir = layout.buildDirectory.dir("robolectric-deps")
val prepareRobolectricDeps by tasks.registering(Copy::class) {
    robolectricAndroidAll.forEach { from(it) }
    into(robolectricDepsDir)
}

// Release signing stays outside the repository (audit 09 §5.5): a properties file with storeFile,
// storePassword, keyAlias and keyPassword. Without it the release APK is built unsigned.
val signingPropertiesFile = file(
    System.getenv("B2C_SIGNING_PROPERTIES")
        ?: "${System.getProperty("user.home")}/.config/book2course/learner-signing.properties",
)
val signingProperties = Properties().apply {
    if (signingPropertiesFile.isFile) signingPropertiesFile.inputStream().use { load(it) }
}

android {
    namespace = "com.book2course.learner"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.book2course.learner"
        minSdk = 24
        targetSdk = 35
        versionCode = 4
        versionName = "0.2.0"
        buildConfigField("String", "DEFAULT_API_BASE_URL", "\"" + defaultApiBaseUrl.replace("\"", "") + "\"")
        // Must match LearnerApi.CLIENT_VERSION and the server's min_client_version contract.
        buildConfigField("int", "CLIENT_VERSION", "1")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (signingProperties.isNotEmpty()) {
            create("release") {
                storeFile = file(signingProperties.getProperty("storeFile"))
                storePassword = signingProperties.getProperty("storePassword")
                keyAlias = signingProperties.getProperty("keyAlias")
                keyPassword = signingProperties.getProperty("keyPassword")
                // Old/vendor Android 7 installers: sign with JAR (v1) as well as v2.
                enableV1Signing = true
                enableV2Signing = true
            }
        }
    }

    // One APK per CPU family plus a universal one. Old 32-bit phones (Android 7, armeabi-v7a) take
    // the armeabi-v7a split; the only native code is Compose's libandroidx.graphics.path.so.
    splits {
        abi {
            isEnable = true
            reset()
            include("armeabi-v7a", "arm64-v8a")
            isUniversalApk = true
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            buildConfigField("boolean", "ALLOW_CLEARTEXT_LOCAL", "true")
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            buildConfigField("boolean", "ALLOW_CLEARTEXT_LOCAL", "false")
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.systemProperty("b2c.learnerOpenApi", learnerOpenApi.absolutePath)
                it.systemProperty("b2c.operatorOpenApi", operatorOpenApi.absolutePath)
                it.systemProperty("robolectric.logging", "stdout")
                it.systemProperty("robolectric.offline", "true")
                it.systemProperty("robolectric.dependency.dir", robolectricDepsDir.get().asFile.absolutePath)
                it.dependsOn(prepareRobolectricDeps)
                it.maxHeapSize = "2g"
            }
        }
    }
    packaging {
        // Store native libs compressed and extract them at install time (extractNativeLibs=true):
        // the most compatible layout for old vendor package installers.
        jniLibs.useLegacyPackaging = true
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "META-INF/versions/9/previous-compilation-data.bin")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    implementation(libs.compose.animation)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.media3.datasource.okhttp)
    implementation(libs.work.runtime.ktx)
    implementation(libs.okhttp)
    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.robolectric)
    testImplementation(libs.androidx.test.core)
    testImplementation(libs.androidx.test.ext.junit)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    testImplementation(libs.work.testing)
}
