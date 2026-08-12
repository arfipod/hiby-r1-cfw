package com.arfipod.r1manager

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import com.arfipod.r1manager.model.RemoteEntry
import com.arfipod.r1manager.ui.R1ManagerApp
import com.arfipod.r1manager.ui.R1ManagerViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: R1ManagerViewModel by viewModels()

    private val pickAudio = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        uris.forEach(::retainReadPermission)
        viewModel.planUploads(uris)
    }

    private val createDownload = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/octet-stream")
    ) { uri ->
        if (uri != null) {
            retainWritePermission(uri)
            viewModel.downloadSingleTo(uri)
        }
    }

    private val pickDownloadFolder = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            retainTreePermission(uri)
            viewModel.downloadMultipleToTree(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        consumeShareIntent(intent)
        setContent {
            R1ManagerApp(
                viewModel = viewModel,
                onPickAudio = { pickAudio.launch(arrayOf("audio/*")) },
                onDownload = ::requestDownload,
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        consumeShareIntent(intent)
    }

    private fun consumeShareIntent(intent: Intent?) {
        val streams = R1ManagerViewModel.streamsFromIntent(intent)
        streams.forEach(::retainReadPermission)
        viewModel.acceptIncomingUris(streams)
    }

    private fun requestDownload(entries: List<RemoteEntry>) {
        val count = viewModel.prepareDownload(entries)
        when (count) {
            1 -> createDownload.launch(entries.single().name)
            in 2..Int.MAX_VALUE -> pickDownloadFolder.launch(null)
        }
    }

    private fun retainReadPermission(uri: Uri) {
        runCatching {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }

    private fun retainWritePermission(uri: Uri) {
        runCatching {
            contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
            )
        }
    }

    private fun retainTreePermission(uri: Uri) = retainWritePermission(uri)
}
