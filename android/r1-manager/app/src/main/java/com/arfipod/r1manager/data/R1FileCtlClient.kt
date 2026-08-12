package com.arfipod.r1manager.data

import com.arfipod.r1manager.model.ConflictPolicy
import com.arfipod.r1manager.model.R1Profile
import com.arfipod.r1manager.model.RemoteEntry
import com.arfipod.r1manager.model.StorageHealth
import com.arfipod.r1manager.model.TrashEntry
import com.arfipod.r1manager.ssh.ExecResult
import com.arfipod.r1manager.ssh.SshTransport
import com.arfipod.r1manager.util.Protocol
import org.json.JSONObject

class R1CommandException(val code: String, override val message: String) : Exception(message)

data class UploadTicket(
    val id: String,
    val stagingAbsolute: String,
    val expectedSize: Long,
)

data class CommitResult(val path: String, val skipped: Boolean)

class R1FileCtlClient(private val transport: SshTransport) {
    private val executable = "/usr/bin/r1-filectl"

    fun protocol(profile: R1Profile): Int = scalar(profile, "$executable protocol").getInt("protocol")

    fun health(profile: R1Profile): StorageHealth {
        val value = scalar(profile, "$executable health")
        return StorageHealth(
            root = value.getString("sd_root"),
            totalBytes = value.getLong("total_bytes"),
            availableBytes = value.getLong("available_bytes"),
            writable = value.getBoolean("writable"),
        )
    }

    fun exists(profile: R1Profile, path: String): Boolean = try {
        scalar(profile, "$executable stat ${Protocol.encode(path)}")
        true
    } catch (error: R1CommandException) {
        if (error.code == "SOURCE_NOT_FOUND") false else throw error
    }

    fun list(profile: R1Profile, path: String): List<RemoteEntry> {
        val result = run(profile, "$executable list ${Protocol.encode(path)}")
        return result.stdout.lineSequence().filter { it.isNotBlank() }.map(::JSONObject)
            .filter { it.optBoolean("entry") }
            .map { value ->
                RemoteEntry(
                    name = value.getString("name"),
                    path = value.getString("path"),
                    encodedPath = value.getString("path_b64"),
                    kind = Protocol.kind(value.getString("kind")),
                    size = value.optLong("size"),
                    modifiedEpochSeconds = value.optLong("mtime"),
                    hidden = value.optBoolean("hidden"),
                )
            }.toList()
    }

    fun mkdir(profile: R1Profile, parent: String, name: String): String =
        scalar(profile, "$executable mkdir ${Protocol.encode(parent)} ${Protocol.encode(name)}").getString("path")

    fun rename(profile: R1Profile, path: String, name: String): String =
        scalar(profile, "$executable rename ${Protocol.encode(path)} ${Protocol.encode(name)}").getString("path")

    fun move(profile: R1Profile, source: String, destination: String, policy: ConflictPolicy): CommitResult {
        val value = scalar(
            profile,
            "$executable move ${Protocol.encode(source)} ${Protocol.encode(destination)} ${policy.wireName}",
        )
        return CommitResult(value.getString("path"), value.optBoolean("skipped"))
    }

    fun delete(profile: R1Profile, path: String, recursive: Boolean) {
        scalar(profile, "$executable delete ${Protocol.encode(path)} ${if (recursive) "recursive" else "empty"}")
    }

    fun trash(profile: R1Profile, path: String): String =
        scalar(profile, "$executable trash ${Protocol.encode(path)}").getString("trash_id")

    fun trashList(profile: R1Profile): List<TrashEntry> {
        val result = run(profile, "$executable trash-list")
        return result.stdout.lineSequence().filter { it.isNotBlank() }.map(::JSONObject)
            .filter { it.optBoolean("entry") }
            .map { value ->
                TrashEntry(
                    id = value.getString("trash_id"),
                    originalPath = value.getString("original_path"),
                    kind = Protocol.kind(value.getString("kind")),
                    size = value.optLong("size"),
                    modifiedEpochSeconds = value.optLong("mtime"),
                )
            }.toList()
    }

    fun restore(profile: R1Profile, id: String): String =
        scalar(profile, "$executable restore $id").getString("path")

    fun purge(profile: R1Profile, id: String) {
        scalar(profile, "$executable purge $id")
    }

    fun emptyTrash(profile: R1Profile): Int =
        scalar(profile, "$executable empty-trash").getInt("deleted")

    fun prepareUpload(
        profile: R1Profile,
        destination: String,
        size: Long,
        policy: ConflictPolicy,
    ): UploadTicket {
        val value = scalar(
            profile,
            "$executable upload-prepare ${Protocol.encode(destination)} $size ${policy.wireName}",
        )
        return UploadTicket(
            id = value.getString("upload_id"),
            stagingAbsolute = value.getString("staging_absolute"),
            expectedSize = value.getLong("expected_size"),
        )
    }

    fun uploadStatus(profile: R1Profile, id: String): Pair<Long, Long> {
        val value = scalar(profile, "$executable upload-status $id")
        return value.getLong("received_size") to value.getLong("expected_size")
    }

    fun commitUpload(profile: R1Profile, id: String): CommitResult {
        val value = scalar(profile, "$executable upload-commit $id")
        return CommitResult(value.getString("path"), value.optBoolean("skipped"))
    }

    fun cancelUpload(profile: R1Profile, id: String) {
        scalar(profile, "$executable upload-cancel $id")
    }

    private fun scalar(profile: R1Profile, command: String): JSONObject {
        val result = run(profile, command)
        return JSONObject(result.stdout.lineSequence().first { it.isNotBlank() })
    }

    private fun run(profile: R1Profile, command: String): ExecResult {
        val result = transport.exec(profile, command)
        if (result.exitCode == 0) return result
        val value = result.stdout.lineSequence().firstOrNull { it.trimStart().startsWith("{") }
            ?.let { runCatching { JSONObject(it) }.getOrNull() }
        if (value != null && !value.optBoolean("ok", true)) {
            throw R1CommandException(value.optString("code", "REMOTE_ERROR"), value.optString("message", "The R1 rejected the operation."))
        }
        throw R1CommandException(
            "REMOTE_ERROR",
            result.stderr.ifBlank { result.stdout }.ifBlank { "The remote command failed with exit code ${result.exitCode}." }.trim(),
        )
    }
}
