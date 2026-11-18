package com.example.calibrenews

import android.app.Application
import com.chaquo.python.Python

class NewsApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // Start the embedded CPython interpreter once for the whole (main) process.
        if (!Python.isStarted()) {
            Python.start(com.chaquo.python.android.AndroidPlatform(applicationContext))
        }
    }
}
