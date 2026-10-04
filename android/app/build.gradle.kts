import java.net.URI
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// The site this build wraps. Override for a self-hosted deployment or a local
// dev server: ./gradlew assembleDebug -PdecintBaseUrl=http://10.0.2.2:3000
val baseUrl = (findProperty("decintBaseUrl") as String? ?: "https://decint.tools").trimEnd('/')
val baseHost: String = requireNotNull(URI(baseUrl).host) { "decintBaseUrl has no host: $baseUrl" }

// Release signing (the Play upload key) comes from the environment, so the key
// never sits in the repo. Without it, release builds come out unsigned.
val uploadKeystore: String? = System.getenv("DECINT_UPLOAD_KEYSTORE")

android {
    namespace = "tools.decint.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "tools.decint.app"
        minSdk = 29
        targetSdk = 36
        // CI passes the workflow run number, so every build Play sees is newer.
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = findProperty("versionName") as String? ?: "1.0.0"

        buildConfigField("String", "BASE_URL", "\"$baseUrl\"")
        manifestPlaceholders["decintHost"] = baseHost
    }

    signingConfigs {
        if (uploadKeystore != null) {
            create("upload") {
                storeFile = file(uploadKeystore)
                storePassword = System.getenv("DECINT_UPLOAD_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("DECINT_UPLOAD_KEY_ALIAS")
                keyPassword = System.getenv("DECINT_UPLOAD_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (uploadKeystore != null) signingConfig = signingConfigs.getByName("upload")
        }
        debug {
            // Installs beside the Play build instead of replacing it.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.ktx)
    implementation(libs.androidx.webkit)
    implementation(libs.androidx.core.splashscreen)
    implementation(libs.play.billing)
    constraints {
        implementation(libs.androidx.fragment) {
            because("Play Billing's transitive fragment predates the Activity Result API fixes")
        }
    }

    testImplementation(libs.junit)
}
