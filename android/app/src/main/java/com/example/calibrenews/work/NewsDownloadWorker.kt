package com.example.calibrenews.work

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.example.calibrenews.PythonBridge
import com.example.calibrenews.R
import java.io.File

/**
 * Background download of one recipe: runs the Python engine via Chaquopy and
 * stores the resulting EPUB in the public Downloads collection.
 */
class NewsDownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        const val KEY_RECIPE = "recipe"
        private const val CHANNEL_ID = "downloads"
        private const val NOTIFICATION_ID = 4242
    }

    override suspend fun doWork(): Result {
        val recipe = inputData.getString(KEY_RECIPE) ?: return Result.failure()
        ensureChannel()
        notify("Downloading $recipe ...")
        return try {
            PythonBridge.ensureStarted(applicationContext)
            val result = PythonBridge.runRecipe(applicationContext, recipe)
            val ok = result["ok"] as? Boolean == true
            val epubPath = result["epub_path"] as? String
            val title = (result["title"] as? String) ?: recipe
            val nArticles = (result["articles_downloaded"] as? Number)?.toInt() ?: 0
            if (ok && epubPath != null) {
                val exported = exportToDownloads(File(epubPath), "$title.epub")
                notify("$title: $nArticles articles -> ${exported ?: epubPath}")
                Scheduler.onRunFinished(applicationContext, recipe)
                Result.success()
            } else {
                val err = result["error"] as? String ?: "unknown error"
                notify("$title failed: $err")
                Scheduler.onRunFinished(applicationContext, recipe)
                Result.failure()
            }
        } catch (e: Exception) {
            notify("$recipe failed: ${e.message}")
            Scheduler.onRunFinished(applicationContext, recipe)
            Result.failure()
        }
    }

    private fun exportToDownloads(file: File, displayName: String): String? {
        return try {
            val resolver = applicationContext.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, displayName)
                put(MediaStore.Downloads.MIME_TYPE, "application/epub+zip")
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/calibre-news")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return null
            resolver.openOutputStream(uri).use { out ->
                file.inputStream().use { it.copyTo(out!!) }
            }
            values.clear()
            values.put(MediaStore.Downloads.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            uri.toString()
        } catch (e: Exception) {
            null
        }
    }

    private fun ensureChannel() {
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, applicationContext.getString(R.string.channel_downloads), NotificationManager.IMPORTANCE_DEFAULT),
            )
        }
    }

    private fun notify(text: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        nm.notify(
            NOTIFICATION_ID,
            NotificationCompat.Builder(applicationContext, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle(applicationContext.getString(R.string.app_name))
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .build(),
        )
    }
}
