plugins {
    id("com.android.application")
    kotlin("android")
    kotlin("plugin.compose")
    id("com.chaquo.python")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.example.calibrenews"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.example.calibrenews"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        // Chaquopy with Python 3.12+ is 64-bit only
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.15"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    sourceSets {
        getByName("main") {
            // the portable news engine (python/newsengine) is packaged into the APK
            python.srcDir("../../python")
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("feedparser>=6.0")
            install("beautifulsoup4>=4.12")
            install("python-dateutil>=2.8")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.ui:ui:1.7.8")
    implementation("androidx.compose.ui:ui-tooling-preview:1.7.8")
    implementation("androidx.compose.material3:material3:1.3.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    debugImplementation("androidx.compose.ui:ui-tooling:1.7.8")
}

// copy the .recipe files into the APK assets (android/app/src/main/assets is gitignored)
tasks.register<Copy>("copyRecipes") {
    from("../../recipes")
    into("src/main/assets/recipes")
}
tasks.named("preBuild") {
    dependsOn("copyRecipes")
}
