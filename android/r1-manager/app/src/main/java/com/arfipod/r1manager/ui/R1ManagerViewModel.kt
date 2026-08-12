package com.arfipod.r1manager.ui

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.arfipod.r1manager.R1ManagerApplication
import com.arfipod.r1manager.data.ProfileStore
import com.arfipod.r1manager.data.R1CommandException
import com.arfipod.r1manager.data.R1Repository
import com.arfipod.r1manager.model.ConflictPolicy
import com.arfipod.r1manager.model.ConnectionState
import com.arfipod.r1manager.model.LocalAudioPlan
import com.arfipod.r1manager.model.R1Problem
import com.arfipod.r1manager.model.R1Profile
import com.arfipod.r1manager.model.RemoteEntry
import com.arfipod.r1manager.model.RemoteKind
import com.arfipod.r1manager.model.StorageHealth
import com.arfipod.r1manager.model.TransferDirection
import com.arfipod.r1manager.model.TransferItem
import com.arfipod.r1manager.model.TransferState
import com.arfipod.r1manager.model.TrashEntry
import com.arfipod.r1manager.ssh.HostIdentityChanged
import com.arfipod.r1manager.ssh.HostTrustRequired
import com.arfipod.r1manager.ssh.SshAuthenticationFailed
import com.arfipod.r1manager.ssh.SshConnectionFailed
import com.arfipod.r1manager.util.MetadataPlanner
import java.util.Locale
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch


enum class MainSection { FILES, TRASH, TRANSFERS, PROFILES }

data class TrustRequest(
    val profileId: String,
    val profileName: String,
    val expectedFingerprint: String?,
    val observedFingerprint: String,
    val changed: Boolean,
)

data class MovePickerState(
    val sources: List<String>,
    val currentPath: String = "",
    val directories: List<RemoteEntry> = emptyList(),
    val loading: Boolean = false,
)

data class UploadReviewState(
    val plans: List<LocalAudioPlan>,
    val conflictPolicy: ConflictPolicy = ConflictPolicy.ASK,
    val organizeByMetadata: Boolean = true,
    val keepOriginalFilename: Boolean = false,
)

data class ConflictRequest(
    val transferId: String,
    val label: String,
    val destination: String,
)

data class R1ManagerUiState(
    val profiles: List<R1Profile> = emptyList(),
    val selectedProfileId: String? = null,
    val section: MainSection = MainSection.PROFILES,
    val connection: ConnectionState = ConnectionState.NoProfile,
    val currentPath: String = "",
    val entries: List<RemoteEntry> = emptyList(),
    val selectedPaths: Set<String> = emptySet(),
    val trashEntries: List<TrashEntry> = emptyList(),
    val transfers: List<TransferItem> = emptyList(),
    val loading: Boolean = false,
    val statusText: String? = null,
    val problem: R1Problem? = null,
    val notice: String? = null,
    val trustRequest: TrustRequest? = null,
    val movePicker: MovePickerState? = null,
    val uploadReview: UploadReviewState? = null,
    val conflictRequest: ConflictRequest? = null,
    val pendingSharedCount: Int = 0,
)

private class TrustRejected : CancellationException("SSH host key approval was cancelled")
private class ConflictCancelled : CancellationException("Transfer conflict was cancelled")

class R1ManagerViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as R1ManagerApplication
    private val profiles: ProfileStore = app.profileStore
    private val repository: R1Repository = app.repository

    private val _state = MutableStateFlow(initialState())
    val state: StateFlow<R1ManagerUiState> = _state.asStateFlow()

    private var trustDecision: CompletableDeferred<Boolean>? = null
    private var conflictDecision: CompletableDeferred<ConflictPolicy?>? = null
    private var pendingIncomingUris: List<Uri> = emptyList()
    private var pendingSingleDownload: RemoteEntry? = null
    private var pendingMultipleDownloads: List<RemoteEntry> = emptyList()
    private var transferJob: Job? = null
    private var activeTransferId: String? = null

    init {
        if (selectedProfile() != null) refresh()
    }

    private fun initialState(): R1ManagerUiState {
        val values = profiles.profiles()
        val selected = profiles.selectedId()?.takeIf { id -> values.any { it.id == id } }
            ?: values.firstOrNull()?.id
        if (selected != profiles.selectedId()) profiles.select(selected)
        return R1ManagerUiState(
            profiles = values,
            selectedProfileId = selected,
            section = if (selected == null) MainSection.PROFILES else MainSection.FILES,
            connection = if (selected == null) ConnectionState.NoProfile else ConnectionState.Disconnected,
        )
    }

    fun selectedProfile(): R1Profile? {
        val snapshot = _state.value
        return snapshot.profiles.firstOrNull { it.id == snapshot.selectedProfileId }
    }

    fun setSection(section: MainSection) {
        _state.value = _state.value.copy(section = section, selectedPaths = emptySet())
        when (section) {
            MainSection.FILES -> if (selectedProfile() != null) refresh()
            MainSection.TRASH -> loadTrash()
            else -> Unit
        }
    }

    fun saveProfile(profile: R1Profile) {
        val problem = validateProfile(profile)
        if (problem != null) {
            _state.value = _state.value.copy(problem = problem)
            return
        }
        profiles.save(profile)
        reloadProfiles(profile.id)
        _state.value = _state.value.copy(section = MainSection.FILES)
        if (pendingIncomingUris.isNotEmpty()) planPendingIncoming() else refresh()
    }

    fun deleteProfile(id: String) {
        profiles.delete(id)
        reloadProfiles(profiles.selectedId())
        val selected = selectedProfile()
        _state.value = _state.value.copy(
            section = if (selected == null) MainSection.PROFILES else _state.value.section,
            connection = if (selected == null) ConnectionState.NoProfile else ConnectionState.Disconnected,
            entries = if (selected == null) emptyList() else _state.value.entries,
            selectedPaths = emptySet(),
        )
    }

    fun selectProfile(id: String) {
        profiles.select(id)
        reloadProfiles(id)
        _state.value = _state.value.copy(
            currentPath = "",
            entries = emptyList(),
            selectedPaths = emptySet(),
            connection = ConnectionState.Disconnected,
            section = MainSection.FILES,
        )
        if (pendingIncomingUris.isNotEmpty()) planPendingIncoming() else refresh()
    }

    fun resetPinnedHostKey(id: String) {
        val value = _state.value.profiles.firstOrNull { it.id == id } ?: return
        profiles.save(value.copy(hostFingerprint = null))
        reloadProfiles(id)
        _state.value = _state.value.copy(notice = "Saved SSH host key removed. The next connection will ask for approval.")
    }

    private fun reloadProfiles(selectedId: String?) {
        val values = profiles.profiles()
        val selected = selectedId?.takeIf { id -> values.any { it.id == id } }
            ?: values.firstOrNull()?.id
        profiles.select(selected)
        _state.value = _state.value.copy(
            profiles = values,
            selectedProfileId = selected,
            pendingSharedCount = pendingIncomingUris.size,
        )
    }

    private fun validateProfile(profile: R1Profile): R1Problem? = when {
        profile.name.isBlank() -> R1Problem("INVALID_PROFILE", "Profile name required", "Give this HiBy R1 profile a recognizable name.")
        profile.host.isBlank() || profile.host.any { it.isWhitespace() } -> R1Problem("INVALID_HOST", "Invalid address", "Enter the R1 IPv4 address or a resolvable host name without spaces.")
        profile.port !in 1..65535 -> R1Problem("INVALID_PORT", "Invalid SSH port", "The SSH port must be between 1 and 65535.")
        profile.username.isBlank() -> R1Problem("INVALID_USERNAME", "Username required", "Enter the Dropbear SSH username.")
        profile.musicRoot.trim('/').isBlank() -> R1Problem("INVALID_MUSIC_ROOT", "Music folder required", "Enter a relative folder such as Music.")
        profile.musicRoot.startsWith('/') || profile.musicRoot.split('/').any { it.isBlank() || it == "." || it == ".." } ->
            R1Problem("INVALID_MUSIC_ROOT", "Invalid music folder", "Use a relative folder below the microSD root, for example Music.")
        else -> null
    }

    fun refresh() {
        val profileId = _state.value.selectedProfileId ?: run {
            _state.value = _state.value.copy(connection = ConnectionState.NoProfile, section = MainSection.PROFILES)
            return
        }
        launchRemote("Reading the microSD") { profile ->
            val protocol = repository.fileCtl.protocol(profile)
            require(protocol == 1) { "Unsupported r1-filectl protocol $protocol." }
            val health = repository.fileCtl.health(profile)
            val current = _state.value.currentPath
            val entries = repository.fileCtl.list(profile, current)
            _state.value = _state.value.copy(
                connection = ConnectionState.Connected(health),
                entries = entries,
                selectedPaths = _state.value.selectedPaths.intersect(entries.map { it.path }.toSet()),
                loading = false,
                statusText = null,
            )
        }
    }

    fun openDirectory(entry: RemoteEntry) {
        if (entry.kind != RemoteKind.DIRECTORY) return
        _state.value = _state.value.copy(currentPath = entry.path, selectedPaths = emptySet())
        refresh()
    }

    fun navigateTo(path: String) {
        _state.value = _state.value.copy(currentPath = path.trim('/'), selectedPaths = emptySet())
        refresh()
    }

    fun toggleSelection(path: String) {
        val selected = _state.value.selectedPaths.toMutableSet()
        if (!selected.add(path)) selected.remove(path)
        _state.value = _state.value.copy(selectedPaths = selected)
    }

    fun selectAll() {
        _state.value = _state.value.copy(selectedPaths = _state.value.entries.map { it.path }.toSet())
    }

    fun clearSelection() {
        _state.value = _state.value.copy(selectedPaths = emptySet())
    }

    fun createFolder(name: String) {
        val parent = _state.value.currentPath
        launchMutation("Creating folder") { profile ->
            repository.fileCtl.mkdir(profile, parent, name)
            "Folder created."
        }
    }

    fun rename(path: String, newName: String) {
        launchMutation("Renaming item") { profile ->
            repository.fileCtl.rename(profile, path, newName)
            "Item renamed."
        }
    }

    fun openMovePicker(paths: List<String> = _state.value.selectedPaths.toList()) {
        if (paths.isEmpty()) return
        _state.value = _state.value.copy(
            movePicker = MovePickerState(sources = paths, currentPath = ""),
            selectedPaths = emptySet(),
        )
        loadMoveDirectories("")
    }

    fun loadMoveDirectories(path: String) {
        val existing = _state.value.movePicker ?: return
        _state.value = _state.value.copy(movePicker = existing.copy(currentPath = path, loading = true))
        launchRemote("Reading destination folders", affectGlobalLoading = false) { profile ->
            val directories = repository.fileCtl.list(profile, path)
                .filter { it.kind == RemoteKind.DIRECTORY }
            val latest = _state.value.movePicker ?: return@launchRemote
            _state.value = _state.value.copy(
                movePicker = latest.copy(currentPath = path, directories = directories, loading = false),
            )
        }
    }

    fun cancelMovePicker() {
        _state.value = _state.value.copy(movePicker = null)
    }

    fun createMoveFolder(name: String) {
        val picker = _state.value.movePicker ?: return
        val parent = picker.currentPath
        launchRemote("Creating destination folder", affectGlobalLoading = false) { profile ->
            repository.fileCtl.mkdir(profile, parent, name)
            val directories = repository.fileCtl.list(profile, parent)
                .filter { it.kind == RemoteKind.DIRECTORY }
            val latest = _state.value.movePicker ?: return@launchRemote
            _state.value = _state.value.copy(
                movePicker = latest.copy(directories = directories, loading = false),
                notice = "Folder created.",
            )
        }
    }

    fun moveSelected(destination: String, policy: ConflictPolicy) {
        val picker = _state.value.movePicker ?: return
        _state.value = _state.value.copy(movePicker = null)
        launchMutation("Moving ${picker.sources.size} item(s)") { profile ->
            var moved = 0
            var skipped = 0
            picker.sources.forEach { source ->
                val result = repository.fileCtl.move(profile, source, destination, policy)
                if (result.skipped) skipped++ else moved++
            }
            buildString {
                append("Moved $moved item(s).")
                if (skipped > 0) append(" Skipped $skipped existing item(s).")
            }
        }
    }

    fun trashSelected(paths: List<String> = _state.value.selectedPaths.toList()) {
        if (paths.isEmpty()) return
        launchMutation("Moving ${paths.size} item(s) to Trash") { profile ->
            paths.forEach { repository.fileCtl.trash(profile, it) }
            "Moved ${paths.size} item(s) to Trash."
        }
    }

    fun deletePermanently(paths: List<String> = _state.value.selectedPaths.toList()) {
        if (paths.isEmpty()) return
        launchMutation("Deleting ${paths.size} item(s)") { profile ->
            paths.forEach { repository.fileCtl.delete(profile, it, recursive = true) }
            "Deleted ${paths.size} item(s) permanently."
        }
    }

    fun loadTrash() {
        if (selectedProfile() == null) return
        launchRemote("Reading Trash") { profile ->
            val health = repository.fileCtl.health(profile)
            val entries = repository.fileCtl.trashList(profile)
            _state.value = _state.value.copy(
                connection = ConnectionState.Connected(health),
                trashEntries = entries,
                loading = false,
                statusText = null,
            )
        }
    }

    fun restoreTrash(id: String) {
        launchRemote("Restoring item") { profile ->
            val path = repository.fileCtl.restore(profile, id)
            val entries = repository.fileCtl.trashList(profile)
            _state.value = _state.value.copy(trashEntries = entries, notice = "Restored to $path.", loading = false, statusText = null)
        }
    }

    fun purgeTrash(id: String) {
        launchRemote("Deleting Trash item") { profile ->
            repository.fileCtl.purge(profile, id)
            val entries = repository.fileCtl.trashList(profile)
            _state.value = _state.value.copy(trashEntries = entries, notice = "Trash item deleted permanently.", loading = false, statusText = null)
        }
    }

    fun emptyTrash() {
        launchRemote("Emptying Trash") { profile ->
            val count = repository.fileCtl.emptyTrash(profile)
            _state.value = _state.value.copy(trashEntries = emptyList(), notice = "Deleted $count Trash item(s).", loading = false, statusText = null)
        }
    }

    fun acceptIncomingUris(uris: List<Uri>) {
        if (uris.isEmpty()) return
        pendingIncomingUris = (pendingIncomingUris + uris).distinctBy(Uri::toString)
        _state.value = _state.value.copy(pendingSharedCount = pendingIncomingUris.size)
        if (selectedProfile() == null) {
            _state.value = _state.value.copy(
                section = MainSection.PROFILES,
                notice = "Create or select an R1 profile to send the shared audio.",
            )
        } else {
            planPendingIncoming()
        }
    }

    fun planUploads(uris: List<Uri>) {
        if (uris.isEmpty()) return
        val profile = selectedProfile()
        if (profile == null) {
            acceptIncomingUris(uris)
            return
        }
        planUris(uris, profile)
    }

    private fun planPendingIncoming() {
        val profile = selectedProfile() ?: return
        val values = pendingIncomingUris
        pendingIncomingUris = emptyList()
        _state.value = _state.value.copy(pendingSharedCount = 0)
        planUris(values, profile)
    }

    private fun planUris(uris: List<Uri>, profile: R1Profile) {
        viewModelScope.launch(Dispatchers.IO) {
            _state.value = _state.value.copy(loading = true, statusText = "Reading audio metadata")
            try {
                val plans = uris.map { MetadataPlanner.inspect(app.contentResolver, it, profile.musicRoot) }
                if (plans.any { it.size <= 0L }) {
                    throw IllegalArgumentException("Android did not report a usable size for one or more selected files.")
                }
                _state.value = _state.value.copy(
                    uploadReview = UploadReviewState(plans),
                    loading = false,
                    statusText = null,
                    section = MainSection.FILES,
                )
            } catch (error: Throwable) {
                presentError(error)
            }
        }
    }

    fun updateUploadReview(
        conflictPolicy: ConflictPolicy? = null,
        organizeByMetadata: Boolean? = null,
        keepOriginalFilename: Boolean? = null,
    ) {
        val review = _state.value.uploadReview ?: return
        _state.value = _state.value.copy(
            uploadReview = review.copy(
                conflictPolicy = conflictPolicy ?: review.conflictPolicy,
                organizeByMetadata = organizeByMetadata ?: review.organizeByMetadata,
                keepOriginalFilename = keepOriginalFilename ?: review.keepOriginalFilename,
            )
        )
    }

    fun cancelUploadReview() {
        _state.value = _state.value.copy(uploadReview = null)
    }

    fun confirmUploads() {
        val review = _state.value.uploadReview ?: return
        if (transferJob?.isActive == true) {
            _state.value = _state.value.copy(
                problem = R1Problem(
                    "TRANSFER_BUSY",
                    "Transfers already running",
                    "Wait for the current upload batch to finish before starting another one.",
                )
            )
            return
        }
        val currentPath = _state.value.currentPath
        val planned = review.plans.map { original ->
            val destination = when {
                !review.organizeByMetadata -> joinPath(currentPath, MetadataPlanner.sanitize(original.displayName, "audio.mp3"))
                review.keepOriginalFilename -> {
                    val parent = original.destination.substringBeforeLast('/', "")
                    joinPath(parent, MetadataPlanner.sanitize(original.displayName, "audio.mp3"))
                }
                else -> original.destination
            }
            original.copy(destination = destination)
        }
        val transfers = planned.map { plan ->
            TransferItem(
                direction = TransferDirection.UPLOAD,
                label = plan.displayName,
                source = plan.uri.toString(),
                destination = plan.destination,
                totalBytes = plan.size,
            )
        }
        _state.value = _state.value.copy(
            uploadReview = null,
            transfers = _state.value.transfers + transfers,
            section = MainSection.TRANSFERS,
        )
        processUploadBatch(planned.zip(transfers), review.conflictPolicy)
    }

    private fun processUploadBatch(
        work: List<Pair<LocalAudioPlan, TransferItem>>,
        configuredPolicy: ConflictPolicy,
    ) {
        val profileId = _state.value.selectedProfileId ?: return
        transferJob = viewModelScope.launch(Dispatchers.IO) {
            work.forEach { (plan, item) ->
                if (!isTransferActive(item.id)) return@forEach
                try {
                    activeTransferId = item.id
                    updateTransfer(item.id, state = TransferState.RUNNING, transferred = 0, message = "Preparing on the R1")
                    var policy = configuredPolicy
                    if (configuredPolicy == ConflictPolicy.ASK && withTrustedProfile(profileId) { repository.fileCtl.exists(it, plan.destination) }) {
                        policy = awaitConflict(item, plan.destination)
                    }
                    val result = withTrustedProfile(profileId) { trusted ->
                        repository.upload(trusted, plan, policy) { copied ->
                            updateTransfer(item.id, state = TransferState.RUNNING, transferred = copied, message = "Uploading")
                        }
                    }
                    val state = if (result.skipped) TransferState.SKIPPED else TransferState.COMPLETED
                    updateTransfer(
                        item.id,
                        state = state,
                        transferred = if (result.skipped) 0 else plan.size,
                        message = if (result.skipped) "Skipped because the destination exists" else "Saved as ${result.path}",
                    )
                    runCatching {
                        withTrustedProfile(profileId) { refreshAfterTransfer(it) }
                    }
                } catch (_: TrustRejected) {
                    updateTransfer(item.id, state = TransferState.CANCELLED, message = "SSH host key approval was cancelled")
                } catch (_: ConflictCancelled) {
                    updateTransfer(item.id, state = TransferState.CANCELLED, message = "Transfer cancelled")
                } catch (error: Throwable) {
                    updateTransfer(item.id, state = TransferState.FAILED, message = problemFor(error).message)
                    _state.value = _state.value.copy(problem = problemFor(error))
                } finally {
                    if (activeTransferId == item.id) activeTransferId = null
                }
            }
            transferJob = null
        }
    }

    private suspend fun refreshAfterTransfer(profile: R1Profile) {
        val health = repository.fileCtl.health(profile)
        val entries = if (_state.value.section == MainSection.FILES) {
            repository.fileCtl.list(profile, _state.value.currentPath)
        } else _state.value.entries
        _state.value = _state.value.copy(connection = ConnectionState.Connected(health), entries = entries)
    }

    private suspend fun awaitConflict(item: TransferItem, destination: String): ConflictPolicy {
        val deferred = CompletableDeferred<ConflictPolicy?>()
        conflictDecision = deferred
        updateTransfer(item.id, state = TransferState.WAITING_FOR_DECISION, message = "Waiting for conflict decision")
        _state.value = _state.value.copy(
            conflictRequest = ConflictRequest(item.id, item.label, destination),
        )
        val result = deferred.await()
        conflictDecision = null
        _state.value = _state.value.copy(conflictRequest = null)
        return result ?: throw ConflictCancelled()
    }

    fun resolveConflict(policy: ConflictPolicy?) {
        conflictDecision?.complete(policy)
    }

    fun cancelTransfer(id: String) {
        updateTransfer(id, state = TransferState.CANCELLED, message = "Cancelled")
    }

    fun clearCompletedTransfers() {
        _state.value = _state.value.copy(
            transfers = _state.value.transfers.filter {
                it.state !in setOf(TransferState.COMPLETED, TransferState.SKIPPED, TransferState.CANCELLED)
            }
        )
    }

    fun prepareDownload(entries: List<RemoteEntry>): Int {
        val files = entries.filter { it.kind == RemoteKind.FILE }
        if (files.size != entries.size || files.isEmpty()) {
            _state.value = _state.value.copy(problem = R1Problem("DOWNLOAD_FILES_ONLY", "Select files to download", "Folder downloads are not supported in this version. Select one or more individual files."))
            return 0
        }
        if (files.size == 1) pendingSingleDownload = files.single()
        else pendingMultipleDownloads = files
        return files.size
    }

    fun consumeSingleDownloadName(): String? = pendingSingleDownload?.name

    fun downloadSingleTo(destination: Uri) {
        val entry = pendingSingleDownload ?: return
        pendingSingleDownload = null
        val transfer = TransferItem(
            direction = TransferDirection.DOWNLOAD,
            label = entry.name,
            source = entry.path,
            destination = destination.toString(),
            totalBytes = entry.size,
        )
        _state.value = _state.value.copy(transfers = _state.value.transfers + transfer, section = MainSection.TRANSFERS)
        val profileId = _state.value.selectedProfileId ?: return
        viewModelScope.launch(Dispatchers.IO) {
            try {
                activeTransferId = transfer.id
                updateTransfer(transfer.id, state = TransferState.RUNNING, message = "Downloading")
                withTrustedProfile(profileId) { profile ->
                    repository.download(profile, entry.path, destination) { copied, total ->
                        updateTransfer(transfer.id, state = TransferState.RUNNING, transferred = copied, total = total, message = "Downloading")
                    }
                }
                updateTransfer(transfer.id, state = TransferState.COMPLETED, transferred = transfer.totalBytes, message = "Saved on Android")
            } catch (_: TrustRejected) {
                updateTransfer(transfer.id, state = TransferState.CANCELLED, message = "SSH host key approval was cancelled")
            } catch (error: Throwable) {
                updateTransfer(transfer.id, state = TransferState.FAILED, message = problemFor(error).message)
                _state.value = _state.value.copy(problem = problemFor(error))
            } finally {
                if (activeTransferId == transfer.id) activeTransferId = null
            }
        }
    }

    fun downloadMultipleToTree(treeUri: Uri) {
        val entries = pendingMultipleDownloads
        pendingMultipleDownloads = emptyList()
        if (entries.isEmpty()) return
        val transfers = entries.map { entry ->
            TransferItem(
                direction = TransferDirection.DOWNLOAD,
                label = entry.name,
                source = entry.path,
                destination = treeUri.toString(),
                totalBytes = entry.size,
            )
        }
        _state.value = _state.value.copy(transfers = _state.value.transfers + transfers, section = MainSection.TRANSFERS)
        val profileId = _state.value.selectedProfileId ?: return
        viewModelScope.launch(Dispatchers.IO) {
            entries.zip(transfers).forEach { (entry, transfer) ->
                try {
                    activeTransferId = transfer.id
                    updateTransfer(transfer.id, state = TransferState.RUNNING, message = "Creating Android document")
                    val destination = createDocument(treeUri, entry.name)
                    withTrustedProfile(profileId) { profile ->
                        repository.download(profile, entry.path, destination) { copied, total ->
                            updateTransfer(transfer.id, state = TransferState.RUNNING, transferred = copied, total = total, message = "Downloading")
                        }
                    }
                    updateTransfer(transfer.id, state = TransferState.COMPLETED, transferred = entry.size, message = "Saved on Android")
                } catch (_: TrustRejected) {
                    updateTransfer(transfer.id, state = TransferState.CANCELLED, message = "SSH host key approval was cancelled")
                } catch (error: Throwable) {
                    updateTransfer(transfer.id, state = TransferState.FAILED, message = problemFor(error).message)
                    _state.value = _state.value.copy(problem = problemFor(error))
                } finally {
                    if (activeTransferId == transfer.id) activeTransferId = null
                }
            }
        }
    }

    private fun createDocument(treeUri: Uri, name: String): Uri {
        val resolver = app.contentResolver
        val rootId = DocumentsContract.getTreeDocumentId(treeUri)
        val parent = DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId)
        return requireNotNull(
            DocumentsContract.createDocument(resolver, parent, mimeFor(name), name)
        ) { "Android could not create $name in the selected folder." }
    }

    private fun mimeFor(name: String): String = when (name.substringAfterLast('.', "").lowercase(Locale.ROOT)) {
        "mp3" -> "audio/mpeg"
        "flac" -> "audio/flac"
        "m4a", "mp4" -> "audio/mp4"
        "ogg", "opus" -> "audio/ogg"
        "wav" -> "audio/wav"
        else -> "application/octet-stream"
    }

    fun acceptTrust() {
        val request = _state.value.trustRequest ?: return
        val profile = _state.value.profiles.firstOrNull { it.id == request.profileId }
        if (profile != null) {
            profiles.save(profile.copy(hostFingerprint = request.observedFingerprint))
            reloadProfiles(request.profileId)
        }
        _state.value = _state.value.copy(trustRequest = null)
        trustDecision?.complete(true)
    }

    fun rejectTrust() {
        _state.value = _state.value.copy(trustRequest = null)
        trustDecision?.complete(false)
    }

    fun clearProblem() {
        _state.value = _state.value.copy(problem = null)
    }

    fun clearNotice() {
        _state.value = _state.value.copy(notice = null)
    }

    private fun launchMutation(status: String, block: suspend (R1Profile) -> String) {
        launchRemote(status) { profile ->
            val message = block(profile)
            val health = repository.fileCtl.health(profile)
            val entries = repository.fileCtl.list(profile, _state.value.currentPath)
            _state.value = _state.value.copy(
                connection = ConnectionState.Connected(health),
                entries = entries,
                selectedPaths = emptySet(),
                notice = message,
                loading = false,
                statusText = null,
            )
        }
    }

    private fun launchRemote(
        status: String,
        affectGlobalLoading: Boolean = true,
        block: suspend (R1Profile) -> Unit,
    ) {
        val profileId = _state.value.selectedProfileId ?: run {
            _state.value = _state.value.copy(section = MainSection.PROFILES, connection = ConnectionState.NoProfile)
            return
        }
        viewModelScope.launch(Dispatchers.IO) {
            if (affectGlobalLoading) {
                _state.value = _state.value.copy(loading = true, statusText = status, connection = ConnectionState.Connecting)
            }
            try {
                withTrustedProfile(profileId, block)
            } catch (_: TrustRejected) {
                _state.value = _state.value.copy(loading = false, statusText = null)
            } catch (error: Throwable) {
                presentError(error)
            }
        }
    }

    private suspend fun <T> withTrustedProfile(
        profileId: String,
        block: suspend (R1Profile) -> T,
    ): T {
        while (true) {
            val profile = currentProfile(profileId)
            try {
                return block(profile)
            } catch (required: HostTrustRequired) {
                if (!awaitTrust(profile, null, required.fingerprint, changed = false)) throw TrustRejected()
            } catch (changed: HostIdentityChanged) {
                if (!awaitTrust(profile, changed.expected, changed.observed, changed = true)) throw TrustRejected()
            }
        }
    }

    private suspend fun awaitTrust(
        profile: R1Profile,
        expected: String?,
        observed: String,
        changed: Boolean,
    ): Boolean {
        val deferred = CompletableDeferred<Boolean>()
        trustDecision = deferred
        activeTransferId?.let {
            updateTransfer(it, state = TransferState.WAITING_FOR_TRUST, message = "Waiting for SSH host-key approval")
        }
        _state.value = _state.value.copy(
            trustRequest = TrustRequest(
                profileId = profile.id,
                profileName = profile.name,
                expectedFingerprint = expected,
                observedFingerprint = observed,
                changed = changed,
            ),
            loading = false,
            statusText = null,
        )
        val result = deferred.await()
        trustDecision = null
        activeTransferId?.let {
            if (result) updateTransfer(it, state = TransferState.RUNNING, message = "Resuming transfer")
        }
        return result
    }

    private fun currentProfile(id: String): R1Profile =
        _state.value.profiles.firstOrNull { it.id == id }
            ?: throw IllegalStateException("The selected R1 profile no longer exists.")

    private fun presentError(error: Throwable) {
        val problem = problemFor(error)
        val connection = when (error) {
            is SshAuthenticationFailed, is SshConnectionFailed -> ConnectionState.Failed(problem)
            else -> _state.value.connection
        }
        _state.value = _state.value.copy(
            loading = false,
            statusText = null,
            problem = problem,
            connection = connection,
            movePicker = _state.value.movePicker?.copy(loading = false),
        )
    }

    private fun problemFor(error: Throwable): R1Problem = when (error) {
        is R1CommandException -> commandProblem(error)
        is SshAuthenticationFailed -> R1Problem(
            "AUTHENTICATION_FAILED",
            "Authentication failed",
            error.message ?: "The R1 rejected the username or password.",
            listOf("Check the profile username and password.", "Confirm SSH Server is enabled in the CFW menu."),
        )
        is SshConnectionFailed -> R1Problem(
            "CONNECTION_FAILED",
            "HiBy R1 unavailable",
            error.message ?: "The R1 could not be reached.",
            listOf("Confirm the R1 and phone are on the same Wi-Fi network.", "Check whether the R1 IP address changed.", "Enable SSH Server in the CFW menu."),
        )
        is SecurityException -> R1Problem(
            "ANDROID_PERMISSION",
            "Android document access lost",
            "Android no longer allows access to the selected file or folder.",
            listOf("Select the document again."),
        )
        is IllegalArgumentException -> R1Problem("INVALID_INPUT", "Operation could not start", error.message ?: "The supplied value is invalid.")
        else -> R1Problem("UNEXPECTED_ERROR", "Operation failed", error.message ?: error::class.java.simpleName)
    }

    private fun commandProblem(error: R1CommandException): R1Problem {
        val title = when (error.code) {
            "SD_NOT_MOUNTED" -> "microSD not available"
            "DESTINATION_EXISTS" -> "Destination already exists"
            "NOT_ENOUGH_SPACE" -> "Not enough space"
            "READ_ONLY_FILESYSTEM" -> "microSD is read-only"
            "CARD_REMOVED" -> "microSD was removed"
            "SOURCE_NOT_FOUND" -> "Item no longer exists"
            "DIRECTORY_NOT_EMPTY" -> "Folder is not empty"
            "UPLOAD_INCOMPLETE", "UPLOAD_SIZE_MISMATCH" -> "Transfer incomplete"
            else -> "R1 filesystem operation failed"
        }
        val suggestions = when (error.code) {
            "SD_NOT_MOUNTED" -> listOf("Insert the microSD card and wait for the player to mount it.")
            "NOT_ENOUGH_SPACE" -> listOf("Delete or move files from the microSD, then retry.")
            "CARD_REMOVED" -> listOf("Reinsert the card and refresh the current folder. The app has not assumed the operation completed.")
            "DESTINATION_EXISTS" -> listOf("Choose Skip, Replace, Keep both, or another name.")
            else -> emptyList()
        }
        return R1Problem(error.code, title, error.message, suggestions)
    }

    private fun updateTransfer(
        id: String,
        state: TransferState? = null,
        transferred: Long? = null,
        total: Long? = null,
        message: String? = null,
    ) {
        _state.value = _state.value.copy(
            transfers = _state.value.transfers.map { item ->
                if (item.id != id) item else item.copy(
                    state = state ?: item.state,
                    transferredBytes = transferred ?: item.transferredBytes,
                    totalBytes = total ?: item.totalBytes,
                    message = message ?: item.message,
                )
            }
        )
    }

    private fun isTransferActive(id: String): Boolean =
        _state.value.transfers.firstOrNull { it.id == id }?.state != TransferState.CANCELLED

    private fun joinPath(parent: String, name: String): String =
        listOf(parent.trim('/'), name.trim('/')).filter { it.isNotBlank() }.joinToString("/")

    companion object {
        fun streamsFromIntent(intent: Intent?): List<Uri> {
            if (intent == null) return emptyList()
            return when (intent.action) {
                Intent.ACTION_SEND -> listOfNotNull(intent.streamUri())
                Intent.ACTION_SEND_MULTIPLE -> intent.multipleStreamUris()
                else -> emptyList()
            }
        }

        @Suppress("DEPRECATION")
        private fun Intent.streamUri(): Uri? = if (android.os.Build.VERSION.SDK_INT >= 33) {
            getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            getParcelableExtra(Intent.EXTRA_STREAM)
        }

        @Suppress("DEPRECATION")
        private fun Intent.multipleStreamUris(): List<Uri> = if (android.os.Build.VERSION.SDK_INT >= 33) {
            getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        } else {
            getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()
        }
    }
}
