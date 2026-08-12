package com.arfipod.r1manager.model

import android.net.Uri
import java.util.UUID

data class R1Profile(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val host: String,
    val port: Int = 2222,
    val username: String = "root",
    val password: String,
    val musicRoot: String = "Music",
    val hostFingerprint: String? = null,
)

enum class RemoteKind { FILE, DIRECTORY, SYMLINK, OTHER }

data class RemoteEntry(
    val name: String,
    val path: String,
    val encodedPath: String,
    val kind: RemoteKind,
    val size: Long,
    val modifiedEpochSeconds: Long,
    val hidden: Boolean,
)

data class StorageHealth(
    val root: String,
    val totalBytes: Long,
    val availableBytes: Long,
    val writable: Boolean,
)

data class TrashEntry(
    val id: String,
    val originalPath: String,
    val kind: RemoteKind,
    val size: Long,
    val modifiedEpochSeconds: Long,
)

enum class ConflictPolicy(val wireName: String) {
    ASK("fail"), SKIP("skip"), REPLACE("replace"), KEEP_BOTH("keep")
}

data class LocalAudioPlan(
    val uri: Uri,
    val displayName: String,
    val size: Long,
    val title: String,
    val artist: String,
    val albumArtist: String,
    val album: String,
    val track: Int?,
    val destination: String,
    val metadataComplete: Boolean,
)

enum class TransferDirection { UPLOAD, DOWNLOAD }
enum class TransferState {
    QUEUED,
    RUNNING,
    WAITING_FOR_TRUST,
    WAITING_FOR_DECISION,
    COMPLETED,
    SKIPPED,
    FAILED,
    CANCELLED,
}

data class TransferItem(
    val id: String = UUID.randomUUID().toString(),
    val direction: TransferDirection,
    val label: String,
    val source: String,
    val destination: String,
    val totalBytes: Long,
    val transferredBytes: Long = 0,
    val state: TransferState = TransferState.QUEUED,
    val message: String? = null,
)

data class R1Problem(
    val code: String,
    val title: String,
    val message: String,
    val suggestions: List<String> = emptyList(),
)

sealed interface ConnectionState {
    data object NoProfile : ConnectionState
    data object Disconnected : ConnectionState
    data object Connecting : ConnectionState
    data class Connected(val health: StorageHealth) : ConnectionState
    data class Failed(val problem: R1Problem) : ConnectionState
}
