package com.arfipod.r1manager.data

import android.content.Context
import com.arfipod.r1manager.model.R1Profile
import org.json.JSONArray
import org.json.JSONObject

class ProfileStore(context: Context, private val cipher: CredentialCipher = CredentialCipher()) {
    private val preferences = context.getSharedPreferences("r1_profiles", Context.MODE_PRIVATE)

    fun profiles(): List<R1Profile> {
        val array = runCatching { JSONArray(preferences.getString("profiles", "[]")) }.getOrDefault(JSONArray())
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                runCatching {
                    add(
                        R1Profile(
                            id = item.getString("id"),
                            name = item.getString("name"),
                            host = item.getString("host"),
                            port = item.optInt("port", 2222),
                            username = item.optString("username", "root"),
                            password = cipher.decrypt(item.optString("password")),
                            musicRoot = item.optString("musicRoot", "Music"),
                            hostFingerprint = item.optString("fingerprint").takeIf { it.isNotBlank() },
                        )
                    )
                }
            }
        }
    }

    fun selectedId(): String? = preferences.getString("selected", null)

    fun select(id: String?) {
        preferences.edit().putString("selected", id).apply()
    }

    fun save(profile: R1Profile) {
        val values = profiles().toMutableList()
        val index = values.indexOfFirst { it.id == profile.id }
        if (index >= 0) values[index] = profile else values += profile
        write(values)
        select(profile.id)
    }

    fun delete(id: String) {
        val remaining = profiles().filterNot { it.id == id }
        write(remaining)
        if (selectedId() == id) select(remaining.firstOrNull()?.id)
    }

    private fun write(values: List<R1Profile>) {
        val array = JSONArray()
        values.forEach { profile ->
            array.put(
                JSONObject()
                    .put("id", profile.id)
                    .put("name", profile.name)
                    .put("host", profile.host)
                    .put("port", profile.port)
                    .put("username", profile.username)
                    .put("password", cipher.encrypt(profile.password))
                    .put("musicRoot", profile.musicRoot)
                    .put("fingerprint", profile.hostFingerprint.orEmpty())
            )
        }
        preferences.edit().putString("profiles", array.toString()).apply()
    }
}
