@file:OptIn(
    androidx.compose.foundation.ExperimentalFoundationApi::class,
    androidx.compose.material3.ExperimentalMaterial3Api::class,
)

package com.arfipod.r1manager.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AudioFile
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.DriveFileMove
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.RestoreFromTrash
import androidx.compose.material.icons.filled.SelectAll
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.arfipod.r1manager.model.ConflictPolicy
import com.arfipod.r1manager.model.ConnectionState
import com.arfipod.r1manager.model.LocalAudioPlan
import com.arfipod.r1manager.model.R1Profile
import com.arfipod.r1manager.model.RemoteEntry
import com.arfipod.r1manager.model.RemoteKind
import com.arfipod.r1manager.model.StorageHealth
import com.arfipod.r1manager.model.TransferDirection
import com.arfipod.r1manager.model.TransferItem
import com.arfipod.r1manager.model.TransferState
import com.arfipod.r1manager.model.TrashEntry
import com.arfipod.r1manager.util.MetadataPlanner
import java.text.DateFormat
import java.util.Date
import java.util.UUID
import kotlin.math.roundToInt

private val AppLightColors = lightColorScheme(
    primary = Color(0xFF245C73),
    secondary = Color(0xFF52636B),
    tertiary = Color(0xFF6B5F3E),
    surface = Color(0xFFF9F9FB),
    surfaceContainer = Color(0xFFF0F1F3),
)

private val AppDarkColors = darkColorScheme(
    primary = Color(0xFF89CEEB),
    secondary = Color(0xFFB8C8D0),
    tertiary = Color(0xFFD7C58B),
)

@Composable
fun R1ManagerApp(
    viewModel: R1ManagerViewModel,
    onPickAudio: () -> Unit,
    onDownload: (List<RemoteEntry>) -> Unit,
) {
    val state by viewModel.state.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    var createFolder by remember { mutableStateOf(false) }
    var renameTarget by remember { mutableStateOf<RemoteEntry?>(null) }
    var detailsTarget by remember { mutableStateOf<RemoteEntry?>(null) }
    var trashTargets by remember { mutableStateOf<List<String>>(emptyList()) }
    var deleteTargets by remember { mutableStateOf<List<String>>(emptyList()) }
    var emptyTrashConfirm by remember { mutableStateOf(false) }
    var profileEditor by remember { mutableStateOf<R1Profile?>(null) }
    var showNewProfile by remember { mutableStateOf(false) }

    LaunchedEffect(state.notice) {
        state.notice?.let {
            snackbar.showSnackbar(it)
            viewModel.clearNotice()
        }
    }

    MaterialTheme(colorScheme = if (androidx.compose.foundation.isSystemInDarkTheme()) AppDarkColors else AppLightColors) {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            topBar = {
                AppTopBar(
                    state = state,
                    profile = viewModel.selectedProfile(),
                    onRefresh = viewModel::refresh,
                )
            },
            bottomBar = {
                MainNavigation(
                    section = state.section,
                    transferCount = state.transfers.count { it.state in activeTransferStates },
                    profileCount = state.profiles.size,
                    onSection = viewModel::setSection,
                )
            },
            snackbarHost = { SnackbarHost(snackbar) },
            floatingActionButton = {
                if (state.section == MainSection.FILES && state.selectedProfileId != null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ExtendedFloatingActionButton(
                            onClick = { createFolder = true },
                            icon = { Icon(Icons.Default.Folder, contentDescription = null) },
                            text = { Text("New folder") },
                        )
                        ExtendedFloatingActionButton(
                            onClick = onPickAudio,
                            icon = { Icon(Icons.Default.UploadFile, contentDescription = null) },
                            text = { Text("Send music") },
                        )
                    }
                }
            },
        ) { padding ->
            Surface(Modifier.fillMaxSize().padding(padding)) {
                when (state.section) {
                    MainSection.FILES -> FilesScreen(
                        state = state,
                        onRetry = viewModel::refresh,
                        onOpenDirectory = viewModel::openDirectory,
                        onNavigate = viewModel::navigateTo,
                        onToggleSelection = viewModel::toggleSelection,
                        onSelectAll = viewModel::selectAll,
                        onClearSelection = viewModel::clearSelection,
                        onDetails = { detailsTarget = it },
                        onRename = { renameTarget = it },
                        onMove = { viewModel.openMovePicker(listOf(it.path)) },
                        onDownload = { onDownload(listOf(it)) },
                        onTrash = { trashTargets = listOf(it.path) },
                        onDelete = { deleteTargets = listOf(it.path) },
                        onMoveSelection = { viewModel.openMovePicker() },
                        onDownloadSelection = {
                            onDownload(state.entries.filter { it.path in state.selectedPaths })
                        },
                        onTrashSelection = { trashTargets = state.selectedPaths.toList() },
                        onDeleteSelection = { deleteTargets = state.selectedPaths.toList() },
                    )
                    MainSection.TRASH -> TrashScreen(
                        state = state,
                        onRefresh = viewModel::loadTrash,
                        onRestore = viewModel::restoreTrash,
                        onPurge = viewModel::purgeTrash,
                        onEmpty = { emptyTrashConfirm = true },
                    )
                    MainSection.TRANSFERS -> TransfersScreen(
                        transfers = state.transfers,
                        onClear = viewModel::clearCompletedTransfers,
                    )
                    MainSection.PROFILES -> ProfilesScreen(
                        state = state,
                        onSelect = viewModel::selectProfile,
                        onEdit = { profileEditor = it },
                        onAdd = { showNewProfile = true },
                        onDelete = viewModel::deleteProfile,
                        onResetKey = viewModel::resetPinnedHostKey,
                    )
                }
            }
        }

        if (createFolder) {
            TextInputDialog(
                title = "New folder",
                label = "Folder name",
                initial = "",
                confirm = "Create",
                onDismiss = { createFolder = false },
                onConfirm = {
                    createFolder = false
                    viewModel.createFolder(it)
                },
            )
        }
        renameTarget?.let { entry ->
            TextInputDialog(
                title = "Rename",
                label = "New name",
                initial = entry.name,
                confirm = "Rename",
                onDismiss = { renameTarget = null },
                onConfirm = {
                    renameTarget = null
                    viewModel.rename(entry.path, it)
                },
            )
        }
        detailsTarget?.let { entry ->
            EntryDetailsDialog(entry = entry, onDismiss = { detailsTarget = null })
        }
        if (trashTargets.isNotEmpty()) {
            ConfirmationDialog(
                title = "Move to Trash?",
                message = "${trashTargets.size} item(s) will be moved to the private R1 Manager Trash. They can be restored later.",
                confirm = "Move to Trash",
                onDismiss = { trashTargets = emptyList() },
                onConfirm = {
                    val values = trashTargets
                    trashTargets = emptyList()
                    viewModel.trashSelected(values)
                },
            )
        }
        if (deleteTargets.isNotEmpty()) {
            ConfirmationDialog(
                title = "Delete permanently?",
                message = "${deleteTargets.size} item(s) will be deleted recursively from the microSD. This cannot be undone.",
                confirm = "Delete permanently",
                destructive = true,
                onDismiss = { deleteTargets = emptyList() },
                onConfirm = {
                    val values = deleteTargets
                    deleteTargets = emptyList()
                    viewModel.deletePermanently(values)
                },
            )
        }
        if (emptyTrashConfirm) {
            ConfirmationDialog(
                title = "Empty Trash?",
                message = "Every item in Trash will be deleted permanently.",
                confirm = "Empty Trash",
                destructive = true,
                onDismiss = { emptyTrashConfirm = false },
                onConfirm = {
                    emptyTrashConfirm = false
                    viewModel.emptyTrash()
                },
            )
        }
        if (showNewProfile || profileEditor != null) {
            ProfileDialog(
                initial = profileEditor,
                onDismiss = {
                    showNewProfile = false
                    profileEditor = null
                },
                onSave = {
                    showNewProfile = false
                    profileEditor = null
                    viewModel.saveProfile(it)
                },
            )
        }
        state.problem?.let { problem ->
            AlertDialog(
                onDismissRequest = viewModel::clearProblem,
                icon = { Icon(Icons.Default.Error, contentDescription = null) },
                title = { Text(problem.title) },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(problem.message)
                        problem.suggestions.forEach { Text("• $it", style = MaterialTheme.typography.bodyMedium) }
                        Text("Code: ${problem.code}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                },
                confirmButton = { TextButton(onClick = viewModel::clearProblem) { Text("Close") } },
            )
        }
        state.trustRequest?.let { request ->
            TrustDialog(
                request = request,
                onAccept = viewModel::acceptTrust,
                onReject = viewModel::rejectTrust,
            )
        }
        state.movePicker?.let { picker ->
            MovePickerDialog(
                picker = picker,
                onNavigate = viewModel::loadMoveDirectories,
                onCreateFolder = viewModel::createMoveFolder,
                onMove = viewModel::moveSelected,
                onDismiss = viewModel::cancelMovePicker,
            )
        }
        state.uploadReview?.let { review ->
            UploadReviewDialog(
                review = review,
                currentPath = state.currentPath,
                onUpdate = viewModel::updateUploadReview,
                onDismiss = viewModel::cancelUploadReview,
                onConfirm = viewModel::confirmUploads,
            )
        }
        state.conflictRequest?.let { conflict ->
            ConflictDialog(conflict = conflict, onResolve = viewModel::resolveConflict)
        }
    }
}

private val activeTransferStates = setOf(
    TransferState.QUEUED,
    TransferState.RUNNING,
    TransferState.WAITING_FOR_TRUST,
    TransferState.WAITING_FOR_DECISION,
)

@Composable
private fun AppTopBar(state: R1ManagerUiState, profile: R1Profile?, onRefresh: () -> Unit) {
    TopAppBar(
        title = {
            Column {
                Text("R1 Manager", maxLines = 1)
                Text(
                    profile?.name ?: "No R1 profile selected",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
        },
        actions = {
            if (state.section in setOf(MainSection.FILES, MainSection.TRASH) && profile != null) {
                IconButton(onClick = onRefresh, enabled = !state.loading) {
                    Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                }
            }
        },
        colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface),
    )
}

@Composable
private fun MainNavigation(
    section: MainSection,
    transferCount: Int,
    profileCount: Int,
    onSection: (MainSection) -> Unit,
) {
    NavigationBar {
        NavigationItem(MainSection.FILES, section, "Files", Icons.Default.Storage, onSection)
        NavigationItem(MainSection.TRASH, section, "Trash", Icons.Default.RestoreFromTrash, onSection)
        NavigationBarItem(
            selected = section == MainSection.TRANSFERS,
            onClick = { onSection(MainSection.TRANSFERS) },
            icon = {
                BadgedBox(badge = { if (transferCount > 0) Badge { Text(transferCount.toString()) } }) {
                    Icon(Icons.Default.SwapVert, contentDescription = null)
                }
            },
            label = { Text("Transfers") },
        )
        NavigationBarItem(
            selected = section == MainSection.PROFILES,
            onClick = { onSection(MainSection.PROFILES) },
            icon = {
                BadgedBox(badge = { if (profileCount > 1) Badge { Text(profileCount.toString()) } }) {
                    Icon(Icons.Default.Person, contentDescription = null)
                }
            },
            label = { Text("Profiles") },
        )
    }
}

@Composable
private fun RowScope.NavigationItem(
    target: MainSection,
    current: MainSection,
    label: String,
    icon: ImageVector,
    onSection: (MainSection) -> Unit,
) {
    NavigationBarItem(
        selected = current == target,
        onClick = { onSection(target) },
        icon = { Icon(icon, contentDescription = null) },
        label = { Text(label) },
    )
}

@Composable
private fun FilesScreen(
    state: R1ManagerUiState,
    onRetry: () -> Unit,
    onOpenDirectory: (RemoteEntry) -> Unit,
    onNavigate: (String) -> Unit,
    onToggleSelection: (String) -> Unit,
    onSelectAll: () -> Unit,
    onClearSelection: () -> Unit,
    onDetails: (RemoteEntry) -> Unit,
    onRename: (RemoteEntry) -> Unit,
    onMove: (RemoteEntry) -> Unit,
    onDownload: (RemoteEntry) -> Unit,
    onTrash: (RemoteEntry) -> Unit,
    onDelete: (RemoteEntry) -> Unit,
    onMoveSelection: () -> Unit,
    onDownloadSelection: () -> Unit,
    onTrashSelection: () -> Unit,
    onDeleteSelection: () -> Unit,
) {
    if (state.selectedProfileId == null) {
        EmptyState(Icons.Default.Person, "Add an R1 profile", "Save the R1 IP address, SSH port, username and password before browsing its microSD.")
        return
    }
    Column(Modifier.fillMaxSize()) {
        ConnectionCard(state.connection, onRetry)
        if (state.connection is ConnectionState.Connected) {
            StorageCard((state.connection as ConnectionState.Connected).health)
        }
        Breadcrumbs(state.currentPath, onNavigate)
        if (state.selectedPaths.isNotEmpty()) {
            SelectionBar(
                count = state.selectedPaths.size,
                onSelectAll = onSelectAll,
                onClear = onClearSelection,
                onMove = onMoveSelection,
                onDownload = onDownloadSelection,
                onTrash = onTrashSelection,
                onDelete = onDeleteSelection,
            )
        }
        if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        state.statusText?.let { Text(it, Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.labelMedium) }
        if (!state.loading && state.entries.isEmpty() && state.connection is ConnectionState.Connected) {
            EmptyState(Icons.Default.FolderOpen, "This folder is empty", "Use Send music or New folder to add content.")
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(bottom = 104.dp),
            ) {
                items(state.entries, key = RemoteEntry::path) { entry ->
                    RemoteEntryRow(
                        entry = entry,
                        selected = entry.path in state.selectedPaths,
                        selectionMode = state.selectedPaths.isNotEmpty(),
                        onClick = {
                            when {
                                state.selectedPaths.isNotEmpty() -> onToggleSelection(entry.path)
                                entry.kind == RemoteKind.DIRECTORY -> onOpenDirectory(entry)
                                else -> onDetails(entry)
                            }
                        },
                        onLongClick = { onToggleSelection(entry.path) },
                        onDetails = { onDetails(entry) },
                        onRename = { onRename(entry) },
                        onMove = { onMove(entry) },
                        onDownload = { onDownload(entry) },
                        onTrash = { onTrash(entry) },
                        onDelete = { onDelete(entry) },
                    )
                    HorizontalDivider(Modifier.padding(start = 72.dp))
                }
            }
        }
    }
}

@Composable
private fun ConnectionCard(connection: ConnectionState, onRetry: () -> Unit) {
    val (icon, title, detail, color) = when (connection) {
        ConnectionState.NoProfile -> ConnectionVisual(Icons.Default.Person, "No profile", "Open Profiles to add the HiBy R1.", MaterialTheme.colorScheme.onSurfaceVariant)
        ConnectionState.Disconnected -> ConnectionVisual(Icons.Default.Wifi, "Not connected", "Tap Retry to contact the R1.", MaterialTheme.colorScheme.onSurfaceVariant)
        ConnectionState.Connecting -> ConnectionVisual(Icons.Default.Wifi, "Connecting", "Opening the SSH connection and checking the microSD.", MaterialTheme.colorScheme.primary)
        is ConnectionState.Connected -> ConnectionVisual(Icons.Default.CheckCircle, "Connected", if (connection.health.writable) "SSH and microSD are ready." else "The microSD is read-only.", if (connection.health.writable) Color(0xFF2E7D32) else MaterialTheme.colorScheme.error)
        is ConnectionState.Failed -> ConnectionVisual(Icons.Default.Error, connection.problem.title, connection.problem.message, MaterialTheme.colorScheme.error)
    }
    OutlinedCard(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
        Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = color)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.SemiBold)
                Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            if (connection is ConnectionState.Disconnected || connection is ConnectionState.Failed) {
                TextButton(onClick = onRetry) { Text("Retry") }
            }
            if (connection is ConnectionState.Connecting) CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
        }
    }
}

private data class ConnectionVisual(val icon: ImageVector, val title: String, val detail: String, val color: Color)

@Composable
private fun StorageCard(health: StorageHealth) {
    val used = (health.totalBytes - health.availableBytes).coerceAtLeast(0)
    val progress = if (health.totalBytes > 0) used.toFloat() / health.totalBytes else 0f
    Card(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("microSD", fontWeight = FontWeight.SemiBold)
                Text("${formatBytes(health.availableBytes)} free", style = MaterialTheme.typography.bodyMedium)
            }
            LinearProgressIndicator(progress = { progress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
            Text("${formatBytes(used)} used of ${formatBytes(health.totalBytes)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Breadcrumbs(path: String, onNavigate: (String) -> Unit) {
    val parts = path.split('/').filter(String::isNotBlank)
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = { onNavigate("") }) {
            Icon(Icons.Default.Home, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(4.dp))
            Text("SD")
        }
        parts.forEachIndexed { index, part ->
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, modifier = Modifier.size(18.dp))
            TextButton(onClick = { onNavigate(parts.take(index + 1).joinToString("/")) }) { Text(part, maxLines = 1) }
        }
    }
}

@Composable
private fun SelectionBar(
    count: Int,
    onSelectAll: () -> Unit,
    onClear: () -> Unit,
    onMove: () -> Unit,
    onDownload: () -> Unit,
    onTrash: () -> Unit,
    onDelete: () -> Unit,
) {
    Surface(color = MaterialTheme.colorScheme.secondaryContainer) {
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onClear) { Icon(Icons.Default.Close, contentDescription = "Clear selection") }
            Text("$count selected", fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.width(8.dp))
            IconButton(onClick = onSelectAll) { Icon(Icons.Default.SelectAll, contentDescription = "Select all") }
            IconButton(onClick = onMove) { Icon(Icons.Default.DriveFileMove, contentDescription = "Move") }
            IconButton(onClick = onDownload) { Icon(Icons.Default.Download, contentDescription = "Download") }
            IconButton(onClick = onTrash) { Icon(Icons.Default.RestoreFromTrash, contentDescription = "Move to Trash") }
            IconButton(onClick = onDelete) { Icon(Icons.Default.Delete, contentDescription = "Delete permanently") }
        }
    }
}

@Composable
private fun RemoteEntryRow(
    entry: RemoteEntry,
    selected: Boolean,
    selectionMode: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onDetails: () -> Unit,
    onRename: () -> Unit,
    onMove: () -> Unit,
    onDownload: () -> Unit,
    onTrash: () -> Unit,
    onDelete: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    val icon = when (entry.kind) {
        RemoteKind.DIRECTORY -> Icons.Default.Folder
        RemoteKind.FILE -> if (entry.name.substringAfterLast('.', "").lowercase() in audioExtensions) Icons.Default.AudioFile else Icons.Default.InsertDriveFile
        RemoteKind.SYMLINK -> Icons.Default.Link
        RemoteKind.OTHER -> Icons.Default.InsertDriveFile
    }
    Row(
        Modifier.fillMaxWidth()
            .background(if (selected) MaterialTheme.colorScheme.secondaryContainer else Color.Transparent)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(start = 18.dp, top = 11.dp, bottom = 11.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (selectionMode) Checkbox(checked = selected, onCheckedChange = { onClick() })
        else Icon(icon, contentDescription = null, modifier = Modifier.size(32.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(entry.name, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = if (entry.kind == RemoteKind.DIRECTORY) FontWeight.Medium else FontWeight.Normal)
            Text(
                when (entry.kind) {
                    RemoteKind.FILE -> "${formatBytes(entry.size)} · ${formatDate(entry.modifiedEpochSeconds)}"
                    RemoteKind.DIRECTORY -> "Folder · ${formatDate(entry.modifiedEpochSeconds)}"
                    RemoteKind.SYMLINK -> "Symbolic link"
                    RemoteKind.OTHER -> "Unsupported filesystem item"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Box {
            IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, contentDescription = "File actions") }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                DropdownMenuItem(text = { Text("Details") }, leadingIcon = { Icon(Icons.Default.Info, null) }, onClick = { menu = false; onDetails() })
                DropdownMenuItem(text = { Text("Rename") }, leadingIcon = { Icon(Icons.Default.Edit, null) }, onClick = { menu = false; onRename() })
                DropdownMenuItem(text = { Text("Move") }, leadingIcon = { Icon(Icons.Default.DriveFileMove, null) }, onClick = { menu = false; onMove() })
                if (entry.kind == RemoteKind.FILE) {
                    DropdownMenuItem(text = { Text("Download") }, leadingIcon = { Icon(Icons.Default.Download, null) }, onClick = { menu = false; onDownload() })
                }
                DropdownMenuItem(text = { Text("Move to Trash") }, leadingIcon = { Icon(Icons.Default.RestoreFromTrash, null) }, onClick = { menu = false; onTrash() })
                DropdownMenuItem(text = { Text("Delete permanently") }, leadingIcon = { Icon(Icons.Default.Delete, null) }, onClick = { menu = false; onDelete() })
            }
        }
    }
}

private val audioExtensions = setOf("mp3", "flac", "m4a", "mp4", "ogg", "opus", "wav", "aac", "aiff", "ape")

@Composable
private fun TrashScreen(
    state: R1ManagerUiState,
    onRefresh: () -> Unit,
    onRestore: (String) -> Unit,
    onPurge: (String) -> Unit,
    onEmpty: () -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("R1 Manager Trash", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text("Items stay on the microSD and can be restored.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onClick = onRefresh) { Text("Refresh") }
            if (state.trashEntries.isNotEmpty()) TextButton(onClick = onEmpty) { Text("Empty") }
        }
        if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (!state.loading && state.trashEntries.isEmpty()) {
            EmptyState(Icons.Default.RestoreFromTrash, "Trash is empty", "Deleted items moved to Trash will appear here.")
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(state.trashEntries, key = TrashEntry::id) { item ->
                    Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(if (item.kind == RemoteKind.DIRECTORY) Icons.Default.Folder else Icons.Default.InsertDriveFile, null, tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            Text(item.originalPath.substringAfterLast('/'), fontWeight = FontWeight.Medium)
                            Text(item.originalPath, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(formatDate(item.modifiedEpochSeconds), style = MaterialTheme.typography.labelSmall)
                        }
                        TextButton(onClick = { onRestore(item.id) }) { Text("Restore") }
                        IconButton(onClick = { onPurge(item.id) }) { Icon(Icons.Default.Delete, contentDescription = "Delete permanently") }
                    }
                    HorizontalDivider(Modifier.padding(start = 56.dp))
                }
            }
        }
    }
}

@Composable
private fun TransfersScreen(transfers: List<TransferItem>, onClear: () -> Unit) {
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Transfers", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text("Uploads and downloads run sequentially and keep partial files outside Music.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (transfers.any { it.state !in activeTransferStates }) TextButton(onClick = onClear) { Text("Clear finished") }
        }
        if (transfers.isEmpty()) {
            EmptyState(Icons.Default.SwapVert, "No transfers yet", "Use Send music, Download, or Android's Share menu.")
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(transfers.reversed(), key = TransferItem::id) { transfer ->
                    TransferRow(transfer)
                    HorizontalDivider(Modifier.padding(start = 64.dp))
                }
            }
        }
    }
}

@Composable
private fun TransferRow(item: TransferItem) {
    val progress = if (item.totalBytes > 0) item.transferredBytes.toFloat() / item.totalBytes else 0f
    val icon = if (item.direction == TransferDirection.UPLOAD) Icons.Default.UploadFile else Icons.Default.Download
    Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.Top) {
        Icon(icon, contentDescription = null, tint = transferColor(item.state), modifier = Modifier.size(32.dp))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(item.label, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.width(8.dp))
                Text(transferLabel(item.state), style = MaterialTheme.typography.labelMedium, color = transferColor(item.state))
            }
            Text(item.destination, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (item.state in activeTransferStates) {
                LinearProgressIndicator(progress = { progress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
                Text("${formatBytes(item.transferredBytes)} of ${formatBytes(item.totalBytes)}", style = MaterialTheme.typography.labelSmall)
            }
            item.message?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
private fun transferColor(state: TransferState): Color = when (state) {
    TransferState.COMPLETED -> Color(0xFF2E7D32)
    TransferState.FAILED -> MaterialTheme.colorScheme.error
    TransferState.CANCELLED, TransferState.SKIPPED -> MaterialTheme.colorScheme.onSurfaceVariant
    TransferState.WAITING_FOR_DECISION, TransferState.WAITING_FOR_TRUST -> MaterialTheme.colorScheme.tertiary
    else -> MaterialTheme.colorScheme.primary
}

private fun transferLabel(state: TransferState): String = when (state) {
    TransferState.QUEUED -> "Queued"
    TransferState.RUNNING -> "Running"
    TransferState.WAITING_FOR_TRUST -> "Trust required"
    TransferState.WAITING_FOR_DECISION -> "Decision required"
    TransferState.COMPLETED -> "Complete"
    TransferState.SKIPPED -> "Skipped"
    TransferState.FAILED -> "Failed"
    TransferState.CANCELLED -> "Cancelled"
}

@Composable
private fun ProfilesScreen(
    state: R1ManagerUiState,
    onSelect: (String) -> Unit,
    onEdit: (R1Profile) -> Unit,
    onAdd: () -> Unit,
    onDelete: (String) -> Unit,
    onResetKey: (String) -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("R1 profiles", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text("Credentials are encrypted with Android Keystore. Each profile pins the R1 SSH host key after approval.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Button(onClick = onAdd) {
                Icon(Icons.Default.Add, null)
                Spacer(Modifier.width(6.dp))
                Text("Add")
            }
        }
        if (state.pendingSharedCount > 0) {
            Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.tertiaryContainer)) {
                Text("${state.pendingSharedCount} shared audio file(s) are waiting for a profile.", Modifier.padding(14.dp))
            }
        }
        if (state.profiles.isEmpty()) {
            EmptyState(Icons.Default.Person, "No profiles", "Add the R1 IP address and the Dropbear credentials configured in your CFW.")
        } else {
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(state.profiles, key = R1Profile::id) { profile ->
                    ProfileCard(
                        profile = profile,
                        selected = profile.id == state.selectedProfileId,
                        onSelect = { onSelect(profile.id) },
                        onEdit = { onEdit(profile) },
                        onDelete = { onDelete(profile.id) },
                        onResetKey = { onResetKey(profile.id) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ProfileCard(
    profile: R1Profile,
    selected: Boolean,
    onSelect: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    onResetKey: () -> Unit,
) {
    OutlinedCard(
        onClick = onSelect,
        colors = CardDefaults.outlinedCardColors(containerColor = if (selected) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Storage, null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(profile.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text("${profile.username}@${profile.host}:${profile.port}", style = MaterialTheme.typography.bodyMedium)
                }
                if (selected) AssistChip(onClick = onSelect, label = { Text("Selected") }, leadingIcon = { Icon(Icons.Default.CheckCircle, null, Modifier.size(18.dp)) })
            }
            Text("Music root: /${profile.musicRoot.trim('/')}", style = MaterialTheme.typography.bodySmall)
            Text(
                profile.hostFingerprint?.let { "Pinned host key: $it" } ?: "Host key not approved yet",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilledTonalButton(onClick = onSelect) { Text("Connect") }
                TextButton(onClick = onEdit) { Icon(Icons.Default.Edit, null); Spacer(Modifier.width(4.dp)); Text("Edit") }
                if (profile.hostFingerprint != null) TextButton(onClick = onResetKey) { Icon(Icons.Default.Key, null); Spacer(Modifier.width(4.dp)); Text("Forget key") }
                TextButton(onClick = onDelete) { Icon(Icons.Default.Delete, null); Spacer(Modifier.width(4.dp)); Text("Delete") }
            }
        }
    }
}

@Composable
private fun ProfileDialog(initial: R1Profile?, onDismiss: () -> Unit, onSave: (R1Profile) -> Unit) {
    var name by rememberSaveable(initial?.id) { mutableStateOf(initial?.name.orEmpty()) }
    var host by rememberSaveable(initial?.id) { mutableStateOf(initial?.host.orEmpty()) }
    var port by rememberSaveable(initial?.id) { mutableStateOf((initial?.port ?: 2222).toString()) }
    var username by rememberSaveable(initial?.id) { mutableStateOf(initial?.username ?: "root") }
    var password by rememberSaveable(initial?.id) { mutableStateOf(initial?.password.orEmpty()) }
    var musicRoot by rememberSaveable(initial?.id) { mutableStateOf(initial?.musicRoot ?: "Music") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (initial == null) "Add HiBy R1" else "Edit ${initial.name}") },
        text = {
            Column(Modifier.fillMaxWidth().imePadding(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text("Profile name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(host, { host = it }, label = { Text("IP address or host name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(port, { port = it.filter(Char::isDigit).take(5) }, label = { Text("Port") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), singleLine = true, modifier = Modifier.weight(0.35f))
                    OutlinedTextField(username, { username = it }, label = { Text("Username") }, singleLine = true, modifier = Modifier.weight(0.65f))
                }
                OutlinedTextField(password, { password = it }, label = { Text("Password") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(musicRoot, { musicRoot = it }, label = { Text("Music folder on microSD") }, supportingText = { Text("Relative path, usually Music") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                initial?.hostFingerprint?.let {
                    Text("Editing the network address keeps the currently pinned host key. Use Forget key on the profile card after reinstalling the firmware.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = {
            Button(onClick = {
                onSave(
                    R1Profile(
                        id = initial?.id ?: UUID.randomUUID().toString(),
                        name = name.trim(),
                        host = host.trim(),
                        port = port.toIntOrNull() ?: 0,
                        username = username.trim(),
                        password = password,
                        musicRoot = musicRoot.trim('/'),
                        hostFingerprint = initial?.hostFingerprint,
                    )
                )
            }) { Text("Save and connect") }
        },
    )
}

@Composable
private fun MovePickerDialog(
    picker: MovePickerState,
    onNavigate: (String) -> Unit,
    onCreateFolder: (String) -> Unit,
    onMove: (String, ConflictPolicy) -> Unit,
    onDismiss: () -> Unit,
) {
    var policy by remember { mutableStateOf(ConflictPolicy.ASK) }
    var newFolder by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Move ${picker.sources.size} item(s)") },
        text = {
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Destination: /${picker.currentPath}", style = MaterialTheme.typography.bodyMedium)
                Row {
                    TextButton(onClick = {
                        val parent = picker.currentPath.substringBeforeLast('/', "")
                        onNavigate(parent)
                    }, enabled = picker.currentPath.isNotEmpty()) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, null)
                        Text("Up")
                    }
                    TextButton(onClick = { newFolder = true }) {
                        Icon(Icons.Default.Add, null)
                        Text("New folder")
                    }
                }
                if (picker.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 300.dp)) {
                    items(picker.directories, key = RemoteEntry::path) { directory ->
                        Row(
                            Modifier.fillMaxWidth().combinedClickable(onClick = { onNavigate(directory.path) }, onLongClick = {}).padding(vertical = 11.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Icon(Icons.Default.Folder, null, tint = MaterialTheme.colorScheme.primary)
                            Spacer(Modifier.width(10.dp))
                            Text(directory.name, Modifier.weight(1f))
                            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null)
                        }
                    }
                }
                Text("When an item already exists", style = MaterialTheme.typography.labelLarge)
                PolicyDropdown(policy = policy, includeAsk = true, onPolicy = { policy = it })
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = { Button(onClick = { onMove(picker.currentPath, policy) }) { Text("Move here") } },
    )
    if (newFolder) {
        TextInputDialog(
            title = "New destination folder",
            label = "Folder name",
            initial = "",
            confirm = "Create",
            onDismiss = { newFolder = false },
            onConfirm = { newFolder = false; onCreateFolder(it) },
        )
    }
}

@Composable
private fun UploadReviewDialog(
    review: UploadReviewState,
    currentPath: String,
    onUpdate: (ConflictPolicy?, Boolean?, Boolean?) -> Unit,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Send ${review.plans.size} audio file(s)") },
        text = {
            LazyColumn(Modifier.fillMaxWidth().heightIn(max = 520.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                item {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Organize from metadata", fontWeight = FontWeight.Medium)
                            Text("Album Artist / Album / Track - Title", style = MaterialTheme.typography.bodySmall)
                        }
                        Switch(review.organizeByMetadata, { onUpdate(null, it, null) })
                    }
                }
                if (review.organizeByMetadata) {
                    item {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("Keep original filenames")
                                Text("Still creates Artist / Album folders", style = MaterialTheme.typography.bodySmall)
                            }
                            Switch(review.keepOriginalFilename, { onUpdate(null, null, it) })
                        }
                    }
                } else {
                    item { Text("Files will be sent to /$currentPath using their original names.", style = MaterialTheme.typography.bodySmall) }
                }
                item {
                    Text("Existing destinations", style = MaterialTheme.typography.labelLarge)
                    PolicyDropdown(review.conflictPolicy, includeAsk = true) { onUpdate(it, null, null) }
                }
                items(review.plans, key = { it.uri.toString() }) { plan ->
                    UploadPlanCard(plan, review, currentPath)
                }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = { Button(onClick = onConfirm) { Text("Start transfers") } },
    )
}

@Composable
private fun UploadPlanCard(plan: LocalAudioPlan, review: UploadReviewState, currentPath: String) {
    val shownDestination = when {
        !review.organizeByMetadata -> joinPathUi(currentPath, MetadataPlanner.sanitize(plan.displayName, "audio.mp3"))
        review.keepOriginalFilename -> joinPathUi(plan.destination.substringBeforeLast('/', ""), MetadataPlanner.sanitize(plan.displayName, "audio.mp3"))
        else -> plan.destination
    }
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(plan.title, fontWeight = FontWeight.SemiBold)
            Text((plan.albumArtist.ifBlank { plan.artist }).ifBlank { "Unknown Artist" }, style = MaterialTheme.typography.bodyMedium)
            Text(plan.album.ifBlank { "Unknown Album" }, style = MaterialTheme.typography.bodySmall)
            Text("${formatBytes(plan.size)} → /$shownDestination", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (!plan.metadataComplete) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Warning, null, tint = MaterialTheme.colorScheme.tertiary, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Some tags are missing; fallback folders will be used.", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@Composable
private fun PolicyDropdown(policy: ConflictPolicy, includeAsk: Boolean, onPolicy: (ConflictPolicy) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { expanded = true }) { Text(policyLabel(policy)) }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            ConflictPolicy.entries.filter { includeAsk || it != ConflictPolicy.ASK }.forEach { item ->
                DropdownMenuItem(
                    text = { Text(policyLabel(item)) },
                    leadingIcon = { RadioButton(selected = item == policy, onClick = null) },
                    onClick = { expanded = false; onPolicy(item) },
                )
            }
        }
    }
}

private fun policyLabel(policy: ConflictPolicy): String = when (policy) {
    ConflictPolicy.ASK -> "Ask for each conflict"
    ConflictPolicy.SKIP -> "Skip existing files"
    ConflictPolicy.REPLACE -> "Replace existing files"
    ConflictPolicy.KEEP_BOTH -> "Keep both with a new name"
}

@Composable
private fun ConflictDialog(conflict: ConflictRequest, onResolve: (ConflictPolicy?) -> Unit) {
    AlertDialog(
        onDismissRequest = { onResolve(null) },
        icon = { Icon(Icons.Default.Warning, null) },
        title = { Text("File already exists") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(conflict.label, fontWeight = FontWeight.Medium)
                Text("/${conflict.destination}", style = MaterialTheme.typography.bodySmall)
                Text("Choose what the R1 should do with this transfer.")
            }
        },
        dismissButton = { TextButton(onClick = { onResolve(null) }) { Text("Cancel transfer") } },
        confirmButton = {
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                TextButton(onClick = { onResolve(ConflictPolicy.SKIP) }) { Text("Skip") }
                TextButton(onClick = { onResolve(ConflictPolicy.KEEP_BOTH) }) { Text("Keep both") }
                TextButton(onClick = { onResolve(ConflictPolicy.REPLACE) }) { Text("Replace") }
            }
        },
    )
}

@Composable
private fun TrustDialog(request: TrustRequest, onAccept: () -> Unit, onReject: () -> Unit) {
    AlertDialog(
        onDismissRequest = onReject,
        icon = { Icon(if (request.changed) Icons.Default.Warning else Icons.Default.Key, null) },
        title = { Text(if (request.changed) "R1 identity changed" else "Trust this HiBy R1?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    if (request.changed) "The SSH host key no longer matches the key saved for ${request.profileName}. Continue only after reinstalling the firmware or intentionally regenerating the Dropbear host key."
                    else "Verify and save this ED25519 host-key fingerprint for ${request.profileName}. Future connections will reject a different key.",
                )
                request.expectedFingerprint?.let {
                    Text("Saved fingerprint", style = MaterialTheme.typography.labelMedium)
                    SelectableFingerprint(it)
                }
                Text("Observed fingerprint", style = MaterialTheme.typography.labelMedium)
                SelectableFingerprint(request.observedFingerprint)
            }
        },
        dismissButton = { TextButton(onClick = onReject) { Text("Cancel") } },
        confirmButton = { Button(onClick = onAccept) { Text(if (request.changed) "Trust new key" else "Trust and continue") } },
    )
}

@Composable
private fun SelectableFingerprint(value: String) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainer, shape = RoundedCornerShape(8.dp)) {
        Text(value, Modifier.fillMaxWidth().padding(10.dp), style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun TextInputDialog(
    title: String,
    label: String,
    initial: String,
    confirm: String,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    var value by rememberSaveable(title, initial) { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { OutlinedTextField(value, { value = it }, label = { Text(label) }, singleLine = true, modifier = Modifier.fillMaxWidth()) },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = { Button(onClick = { onConfirm(value.trim()) }, enabled = value.trim().isNotEmpty()) { Text(confirm) } },
    )
}

@Composable
private fun ConfirmationDialog(
    title: String,
    message: String,
    confirm: String,
    destructive: Boolean = false,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(if (destructive) Icons.Default.Warning else Icons.Default.Info, null) },
        title = { Text(title) },
        text = { Text(message) },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = {
            Button(onClick = onConfirm) { Text(confirm) }
        },
    )
}

@Composable
private fun EntryDetailsDialog(entry: RemoteEntry, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = { Icon(if (entry.kind == RemoteKind.DIRECTORY) Icons.Default.Folder else Icons.Default.InsertDriveFile, null) },
        title = { Text(entry.name) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                DetailRow("Path", "/${entry.path}")
                DetailRow("Type", entry.kind.name.lowercase().replaceFirstChar(Char::uppercase))
                if (entry.kind == RemoteKind.FILE) DetailRow("Size", formatBytes(entry.size))
                DetailRow("Modified", formatDate(entry.modifiedEpochSeconds))
                if (entry.kind == RemoteKind.SYMLINK) Text("R1 Manager lists symbolic links but never follows them for filesystem operations.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun DetailRow(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun EmptyState(icon: ImageVector, title: String, detail: String) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(56.dp), tint = MaterialTheme.colorScheme.primary)
            Text(title, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun formatBytes(bytes: Long): String {
    if (bytes < 0) return "Unknown size"
    if (bytes < 1024) return "$bytes B"
    val units = arrayOf("KiB", "MiB", "GiB", "TiB")
    var value = bytes.toDouble()
    var unit = -1
    do {
        value /= 1024.0
        unit++
    } while (value >= 1024.0 && unit < units.lastIndex)
    return if (value >= 100) "${value.roundToInt()} ${units[unit]}" else "%.1f %s".format(value, units[unit])
}

private fun formatDate(epochSeconds: Long): String = if (epochSeconds <= 0) "Unknown date" else
    DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(epochSeconds * 1000))

private fun joinPathUi(parent: String, name: String): String =
    listOf(parent.trim('/'), name.trim('/')).filter(String::isNotBlank).joinToString("/")
