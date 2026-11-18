package com.example.calibrenews.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.RoomDatabase

@Entity(tableName = "schedules")
data class ScheduleEntity(
    @androidx.room.PrimaryKey(autoGenerate = true) val id: Long = 0,
    val recipe: String,
    val hour: Int,
    val minute: Int,
    val enabled: Boolean = true,
)

@Dao
interface ScheduleDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(schedule: ScheduleEntity)

    @Query("DELETE FROM schedules WHERE recipe = :recipe")
    suspend fun deleteByRecipe(recipe: String)

    @Query("SELECT * FROM schedules WHERE recipe = :recipe LIMIT 1")
    suspend fun getByRecipe(recipe: String): ScheduleEntity?

    @Query("SELECT * FROM schedules WHERE enabled = 1")
    suspend fun getAllEnabled(): List<ScheduleEntity>
}

@Database(entities = [ScheduleEntity::class], version = 1)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scheduleDao(): ScheduleDao

    companion object {
        @Volatile
        private var instance: AppDatabase? = null

        fun get(context: android.content.Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: androidx.room.Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "calibre-news.db",
                ).build().also { instance = it }
            }
    }
}
