package com.example.calibrenews.work

import android.content.Context
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.example.calibrenews.data.AppDatabase
import com.example.calibrenews.data.ScheduleEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.Calendar
import java.util.concurrent.TimeUnit

/**
 * Daily scheduling of recipe downloads via WorkManager.
 * For each enabled schedule we enqueue a unique one-shot work request at the next
 * occurrence; NewsDownloadWorker re-enqueues the next day's run when it finishes.
 */
object Scheduler {

    fun workName(recipe: String) = "news_$recipe"

    fun schedule(context: Context, recipe: String, hour: Int, minute: Int) {
        CoroutineScope(Dispatchers.IO).launch {
            AppDatabase.get(context).scheduleDao().upsert(ScheduleEntity(recipe = recipe, hour = hour, minute = minute))
            enqueueNext(context, recipe, hour, minute)
        }
    }

    fun cancel(context: Context, recipe: String) {
        CoroutineScope(Dispatchers.IO).launch {
            AppDatabase.get(context).scheduleDao().deleteByRecipe(recipe)
        }
        WorkManager.getInstance(context).cancelUniqueWork(workName(recipe))
    }

    /** Re-create all pending work requests (e.g. after reboot). */
    fun rescheduleAll(context: Context) {
        CoroutineScope(Dispatchers.IO).launch {
            for (s in AppDatabase.get(context).scheduleDao().getAllEnabled()) {
                enqueueNext(context, s.recipe, s.hour, s.minute)
            }
        }
    }

    /** Called by the worker after a run: enqueue the next daily occurrence. */
    fun onRunFinished(context: Context, recipe: String) {
        CoroutineScope(Dispatchers.IO).launch {
            val s = AppDatabase.get(context).scheduleDao().getByRecipe(recipe)
            if (s != null && s.enabled) {
                enqueueNext(context, s.recipe, s.hour, s.minute)
            }
        }
    }

    private fun enqueueNext(context: Context, recipe: String, hour: Int, minute: Int) {
        val now = Calendar.getInstance()
        val next = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            if (before(now)) add(Calendar.DAY_OF_YEAR, 1)
        }
        val delayMs = next.timeInMillis - now.timeInMillis
        val request = OneTimeWorkRequestBuilder<NewsDownloadWorker>()
            .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .setRequiresBatteryNotLow(true)
                    .build(),
            )
            .setInputData(Data.Builder().putString(NewsDownloadWorker.KEY_RECIPE, recipe).build())
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            workName(recipe),
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }
}
