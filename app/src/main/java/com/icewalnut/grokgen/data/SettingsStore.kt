package com.icewalnut.grokgen.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.icewalnut.grokgen.net.ServerAddress
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "grokgen_settings")

/**
 * 本地设置。M2R1 只存一件事：用户填的后端地址。
 *
 * ⚠️ **不用数据库。** 一个字符串不值得一张表。
 * 任务列表要不要存本地是另一个问题，那条在架构文档 §11 里还挂着未决 ——
 * M1 的网关不持久化任务，本地缓存会出现「App 里有、网关里没有」的条目。
 */
class SettingsStore(private val context: Context) {

    /**
     * 用户填的后端地址，**原样存**（没规整过的那一份）。
     *
     * 存原样是故意的：用户下次打开连接页时，输入框里应该是他自己写的那串，
     * 而不是被程序改写过的样子 —— 否则他会以为自己记错了。
     * 规整成 base URL 的事在 [ServerAddress.toBaseUrl] 里做，每次用的时候做。
     */
    val serverInput: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_SERVER_INPUT] ?: ServerAddress.DEFAULT_INPUT
    }

    suspend fun setServerInput(value: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_SERVER_INPUT] = value
        }
    }

    private companion object {
        val KEY_SERVER_INPUT = stringPreferencesKey("server_input")
    }
}
