package com.example.calibrenews.ui

import android.Manifest
import android.app.TimePickerDialog
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.example.calibrenews.PythonBridge
import com.example.calibrenews.data.AppDatabase
import com.example.calibrenews.work.NewsDownloadWorker
import com.example.calibrenews.work.Scheduler
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Calendar

data class RecipeUi(
    val name: String,
    val title: String,
    val author: String,
    val description: String,
    val language: String,
)

class RecipeViewModel(private val context: android.content.Context) : ViewModel() {
    var recipes by mutableStateOf<List<RecipeUi>>(emptyList())
    var status by mutableStateOf<String?>(null)
    var schedules by mutableStateOf<Map<String, Pair<Int, Int>>>(emptyMap())

    fun load() {
        viewModelScope.launch {
            recipes = withContext(Dispatchers.IO) {
                runCatching {
                    PythonBridge.listRecipes(context).map {
                        RecipeUi(
                            name = it["name"] as String,
                            title = it["title"] as String,
                            author = (it["author"] as? String) ?: "",
                            description = (it["description"] as? String) ?: "",
                            language = (it["language"] as? String) ?: "",
                        )
                    }
                }.getOrElse { emptyList() }
            }
            schedules = withContext(Dispatchers.IO) {
                AppDatabase.get(context).scheduleDao().getAllEnabled()
                    .associate { it.recipe to (it.hour to it.minute) }
            }
        }
    }

    fun downloadNow(recipe: String) {
        viewModelScope.launch {
            status = "Queued $recipe (see notification when done)"
            withContext(Dispatchers.IO) {
                val request = OneTimeWorkRequestBuilder<NewsDownloadWorker>()
                    .setInputData(Data.Builder().putString(NewsDownloadWorker.KEY_RECIPE, recipe).build())
                    .build()
                WorkManager.getInstance(context).enqueueUniqueWork(
                    "manual_$recipe", ExistingWorkPolicy.REPLACE, request,
                )
            }
        }
    }

    fun toggleSchedule(recipe: String, enabled: Boolean, hour: Int = 7, minute: Int = 0) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                if (enabled) Scheduler.schedule(context, recipe, hour, minute)
                else Scheduler.cancel(context, recipe)
            }
            load()
        }
    }
}

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) {}.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        Scheduler.rescheduleAll(applicationContext)
        setContent {
            MaterialTheme {
                RecipeListScreen()
            }
        }
    }
}

@Composable
fun RecipeListScreen(vm: RecipeViewModel = viewModel(factory = object : androidx.lifecycle.ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T =
        RecipeViewModel(LocalContext.current.applicationContext) as T
})) {
    val context = LocalContext.current
    LaunchedEffect(Unit) { vm.load() }

    Column(modifier = Modifier.padding(16.dp)) {
        Text("calibre-news", style = MaterialTheme.typography.headlineMedium)
        vm.status?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(vm.recipes) { recipe ->
                RecipeCard(
                    recipe = recipe,
                    scheduled = vm.schedules[recipe.name],
                    onDownload = { vm.downloadNow(recipe.name) },
                    onToggleSchedule = { enabled ->
                        if (enabled) {
                            val now = Calendar.getInstance()
                            TimePickerDialog(context, { _, hour, minute ->
                                vm.toggleSchedule(recipe.name, true, hour, minute)
                            }, now.get(Calendar.HOUR_OF_DAY), now.get(Calendar.MINUTE), true).show()
                        } else {
                            vm.toggleSchedule(recipe.name, false)
                        }
                    },
                )
            }
        }
    }
}

@Composable
fun RecipeCard(
    recipe: RecipeUi,
    scheduled: Pair<Int, Int>?,
    onDownload: () -> Unit,
    onToggleSchedule: (Boolean) -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(recipe.title, style = MaterialTheme.typography.titleMedium)
            if (recipe.description.isNotBlank()) {
                Text(recipe.description, style = MaterialTheme.typography.bodySmall)
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.padding(top = 8.dp),
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            ) {
                Button(onClick = onDownload) { Text("Download now") }
                Switch(
                    checked = scheduled != null,
                    onCheckedChange = { onToggleSchedule(it) },
                )
                Text(
                    if (scheduled != null) "Daily at %02d:%02d".format(scheduled.first, scheduled.second) else "Schedule daily",
                )
            }
        }
    }
}
