plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.icewalnut.grokgen"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.icewalnut.grokgen"
        // 开发机上装了 android-26 与 android-36 两个平台，不额外下载别的。
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    // ⚠️ 系统默认的 java 是 11，**不要动它**（AGENTS.md §4）。
    //    这里用 toolchain 声明 17，Gradle 会自己去找已装的 JDK 17。
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    val composeBom = platform(libs.compose.bom)
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    debugImplementation(libs.compose.ui.tooling)

    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.datastore.preferences)

    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.kotlinx.serialization)
    implementation(libs.okhttp.logging.interceptor)
    implementation(libs.kotlinx.serialization.json)

    testImplementation(libs.junit)
}

// ⭐ 扫源码的那两条约束（VS-30 分层、VS-41 颜色字面量）需要知道源码根在哪。
//
// ⚠️ **不要让测试去猜当前工作目录。** Gradle 的 Test 任务默认工作目录是工程目录，
//    但那是个约定而不是保证，而一旦猜错，测试会扫到一个空目录然后**静默通过** ——
//    那正是这两条约束最不能出的错。所以把绝对路径注入进去，
//    测试里读不到这个属性就直接失败。
tasks.withType<Test>().configureEach {
    systemProperty("grokgen.srcRoot", layout.projectDirectory.dir("src/main/java").asFile.absolutePath)
}
