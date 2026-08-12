package com.arfipod.r1manager

import android.app.Application
import com.arfipod.r1manager.data.ProfileStore
import com.arfipod.r1manager.data.R1Repository
import com.arfipod.r1manager.ssh.SshTransport

class R1ManagerApplication : Application() {
    val profileStore: ProfileStore by lazy { ProfileStore(this) }
    val sshTransport: SshTransport by lazy { SshTransport() }
    val repository: R1Repository by lazy {
        R1Repository(contentResolver, sshTransport)
    }
}
