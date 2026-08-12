package com.arfipod.r1manager.data

import android.content.ContentResolver
import android.net.Uri
import com.arfipod.r1manager.model.ConflictPolicy
import com.arfipod.r1manager.model.LocalAudioPlan
import com.arfipod.r1manager.model.R1Profile
import com.arfipod.r1manager.ssh.SshTransport

class R1Repository(
    private val resolver: ContentResolver,
    private val transport: SshTransport,
    val fileCtl: R1FileCtlClient = R1FileCtlClient(transport),
) {
    fun upload(
        profile: R1Profile,
        plan: LocalAudioPlan,
        policy: ConflictPolicy,
        onProgress: (Long) -> Unit,
    ): CommitResult {
        val ticket = fileCtl.prepareUpload(profile, plan.destination, plan.size, policy)
        try {
            resolver.openInputStream(plan.uri).use { source ->
                requireNotNull(source) { "The selected Android document is no longer available." }
                transport.upload(profile, ticket.stagingAbsolute, source, plan.size, onProgress)
            }
            val status = fileCtl.uploadStatus(profile, ticket.id)
            require(status.first == status.second) { "The R1 received ${status.first} of ${status.second} bytes." }
            return fileCtl.commitUpload(profile, ticket.id)
        } catch (error: Throwable) {
            runCatching { fileCtl.cancelUpload(profile, ticket.id) }
            throw error
        }
    }

    fun download(
        profile: R1Profile,
        remotePath: String,
        destination: Uri,
        onProgress: (Long, Long) -> Unit,
    ) {
        val health = fileCtl.health(profile)
        val absolute = health.root.trimEnd('/') + "/" + remotePath.trimStart('/')
        resolver.openOutputStream(destination, "w").use { output ->
            requireNotNull(output) { "Android could not open the selected destination." }
            transport.download(profile, absolute, output, onProgress)
        }
    }
}
