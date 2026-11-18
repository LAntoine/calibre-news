package com.example.calibrenews

import android.content.Context
import android.os.Environment
import com.chaquo.python.Python
import java.io.File

/**
 * Bridge between Kotlin and the embedded Python engine (newsengine).
 * All Python calls go through dict-based helpers (run_recipe_dict / list_recipes_dict)
 * because Chaquopy converts Python dict/list natively to java.util.Map/List.
 */
object PythonBridge {

    fun ensureStarted(context: Context) {
        if (!Python.isStarted()) {
            Python.start(com.chaquo.python.android.AndroidPlatform(context.applicationContext))
        }
    }

    /** Extract the bundled .recipe assets to filesDir so Python can read them. */
    fun recipesDir(context: Context): File {
        val dir = File(context.filesDir, "recipes")
        dir.mkdirs()
        val assets = context.assets.list("recipes") ?: emptyArray()
        for (name in assets) {
            val out = File(dir, name)
            if (!out.exists()) {
                context.assets.open("recipes/$name").use { input ->
                    out.outputStream().use { input.copyTo(it) }
                }
            }
        }
        return dir
    }

    private fun runner(context: Context) = Python.getInstance().getModule("newsengine.runner")

    @Suppress("UNCHECKED_CAST")
    fun listRecipes(context: Context): List<Map<String, Any?>> {
        ensureStarted(context)
        val result = runner(context).callAttr("list_recipes_dict", recipesDir(context).absolutePath)
        return result.toJavaObject() as List<Map<String, Any?>>
    }

    /**
     * Run a recipe and return the result dict (see newsengine.runner.run_recipe_dict).
     * @param recipeName recipe file name without extension, e.g. "le_monde"
     * @param options optional dict: max_feeds, max_articles_per_feed, oldest_article, verbose
     */
    fun runRecipe(context: Context, recipeName: String, options: Map<String, Any?>? = null): Map<String, Any?> {
        ensureStarted(context)
        val recipePath = File(recipesDir(context), "$recipeName.recipe").absolutePath
        val outDir = File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "calibre-news")
        outDir.mkdirs()
        val outFile = File(outDir, "$recipeName.epub").absolutePath
        val opts = (options ?: emptyMap())
        val result = runner(context).callAttr("run_recipe_dict", recipePath, outFile, opts)
        return result.toJavaObject() as Map<String, Any?>
    }
}
