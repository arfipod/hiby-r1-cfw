package com.arfipod.r1manager.ssh

import com.arfipod.r1manager.model.R1Profile
import com.arfipod.r1manager.util.Protocol
import com.jcraft.jsch.ChannelExec
import com.jcraft.jsch.HostKey
import com.jcraft.jsch.HostKeyRepository
import com.jcraft.jsch.JSch
import com.jcraft.jsch.JSchException
import com.jcraft.jsch.Session
import com.jcraft.jsch.UserInfo
import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.util.Base64

class HostTrustRequired(val fingerprint: String) : Exception("SSH host key approval is required")
class HostIdentityChanged(val expected: String, val observed: String) : Exception("SSH host identity changed")
class SshAuthenticationFailed(message: String, cause: Throwable? = null) : Exception(message, cause)
class SshConnectionFailed(message: String, cause: Throwable? = null) : Exception(message, cause)

data class ExecResult(val exitCode: Int, val stdout: String, val stderr: String)

class SshTransport(
    private val connectTimeoutMs: Int = 10_000,
    private val channelTimeoutMs: Int = 15_000,
) {
    private class RejectingUserInfo : UserInfo {
        override fun getPassphrase(): String? = null
        override fun getPassword(): String? = null
        override fun promptPassword(message: String?): Boolean = false
        override fun promptPassphrase(message: String?): Boolean = false
        override fun promptYesNo(message: String?): Boolean = false
        override fun showMessage(message: String?) = Unit
    }

    private class PinnedRepository(private val pinned: String?) : HostKeyRepository {
        var observed: String? = null
            private set

        override fun check(host: String?, key: ByteArray?): Int {
            if (key == null) return HostKeyRepository.CHANGED
            val fingerprint = "SHA256:" + Base64.getEncoder().withoutPadding().encodeToString(
                MessageDigest.getInstance("SHA-256").digest(key)
            )
            observed = fingerprint
            return when {
                pinned == null -> HostKeyRepository.NOT_INCLUDED
                pinned == fingerprint -> HostKeyRepository.OK
                else -> HostKeyRepository.CHANGED
            }
        }

        override fun add(hostkey: HostKey?, ui: UserInfo?) = Unit
        override fun remove(host: String?, type: String?) = Unit
        override fun remove(host: String?, type: String?, key: ByteArray?) = Unit
        override fun getKnownHostsRepositoryID(): String = "R1 Manager profile pin"
        override fun getHostKey(): Array<HostKey> = emptyArray()
        override fun getHostKey(host: String?, type: String?): Array<HostKey> = emptyArray()
    }

    private fun connect(profile: R1Profile): Session {
        val repository = PinnedRepository(profile.hostFingerprint)
        val jsch = JSch().apply { hostKeyRepository = repository }
        val session = jsch.getSession(profile.username, profile.host, profile.port)
        session.setPassword(profile.password)
        session.userInfo = RejectingUserInfo()
        session.setConfig("StrictHostKeyChecking", "ask")
        session.setConfig("PreferredAuthentications", "password")
        session.setServerAliveInterval(15_000)
        session.setServerAliveCountMax(2)
        try {
            session.connect(connectTimeoutMs)
            return session
        } catch (error: JSchException) {
            session.disconnect()
            val observed = repository.observed
            if (profile.hostFingerprint == null && observed != null) throw HostTrustRequired(observed)
            if (profile.hostFingerprint != null && observed != null && observed != profile.hostFingerprint) {
                throw HostIdentityChanged(profile.hostFingerprint, observed)
            }
            val message = error.message.orEmpty()
            if (message.contains("Auth fail", ignoreCase = true) ||
                message.contains("authentication", ignoreCase = true)) {
                throw SshAuthenticationFailed("The HiBy R1 rejected the username or password.", error)
            }
            throw SshConnectionFailed(message.ifBlank { "The HiBy R1 could not be reached." }, error)
        }
    }

    fun exec(profile: R1Profile, command: String): ExecResult {
        val session = connect(profile)
        val channel = session.openChannel("exec") as ChannelExec
        val errors = ByteArrayOutputStream()
        return try {
            channel.setCommand(command)
            channel.setInputStream(null)
            channel.setErrStream(errors)
            val input = channel.inputStream
            channel.connect(channelTimeoutMs)
            val output = input.readBytes().toString(Charsets.UTF_8)
            while (!channel.isClosed) Thread.sleep(5)
            ExecResult(channel.exitStatus, output, errors.toString(Charsets.UTF_8.name()))
        } finally {
            channel.disconnect()
            session.disconnect()
        }
    }

    fun upload(
        profile: R1Profile,
        remoteAbsolutePath: String,
        source: InputStream,
        size: Long,
        onProgress: (Long) -> Unit,
    ) {
        val session = connect(profile)
        val channel = session.openChannel("exec") as ChannelExec
        try {
            channel.setCommand("scp -t ${Protocol.shellQuote(remoteAbsolutePath)}")
            val input = channel.inputStream
            val output = channel.outputStream
            channel.connect(channelTimeoutMs)
            requireAck(input)
            val name = remoteAbsolutePath.substringAfterLast('/').ifBlank { "payload.part" }
            output.write("C0600 $size $name\n".toByteArray(Charsets.UTF_8))
            output.flush()
            requireAck(input)
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE * 4)
            var total = 0L
            while (true) {
                val count = source.read(buffer)
                if (count < 0) break
                output.write(buffer, 0, count)
                total += count
                onProgress(total)
            }
            if (total != size) throw EOFException("Expected $size bytes, read $total")
            output.write(0)
            output.flush()
            requireAck(input)
        } finally {
            channel.disconnect()
            session.disconnect()
        }
    }

    fun download(
        profile: R1Profile,
        remoteAbsolutePath: String,
        destination: OutputStream,
        onProgress: (Long, Long) -> Unit,
    ) {
        val session = connect(profile)
        val channel = session.openChannel("exec") as ChannelExec
        try {
            channel.setCommand("scp -f ${Protocol.shellQuote(remoteAbsolutePath)}")
            val input = channel.inputStream
            val output = channel.outputStream
            channel.connect(channelTimeoutMs)
            output.write(0)
            output.flush()
            val type = input.read()
            if (type != 'C'.code) throw SshConnectionFailed(readScpError(type, input))
            val mode = readToken(input, ' ')
            val size = readToken(input, ' ').toLong()
            readToken(input, '\n')
            if (mode.length != 4) throw SshConnectionFailed("Unexpected SCP mode: $mode")
            output.write(0)
            output.flush()
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE * 4)
            var remaining = size
            var copied = 0L
            while (remaining > 0) {
                val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                if (count < 0) throw EOFException("SCP stream ended early")
                destination.write(buffer, 0, count)
                remaining -= count
                copied += count
                onProgress(copied, size)
            }
            requireAck(input)
            output.write(0)
            output.flush()
        } finally {
            channel.disconnect()
            session.disconnect()
        }
    }

    private fun requireAck(input: InputStream) {
        val code = input.read()
        if (code == 0) return
        throw SshConnectionFailed(readScpError(code, input))
    }

    private fun readScpError(code: Int, input: InputStream): String {
        if (code < 0) return "The SCP connection closed unexpectedly."
        val text = readToken(input, '\n')
        return if (code == 1 || code == 2) text.ifBlank { "SCP failed." }
        else "Unexpected SCP response $code: $text"
    }

    private fun readToken(input: InputStream, delimiter: Char): String {
        val output = ByteArrayOutputStream()
        while (true) {
            val value = input.read()
            if (value < 0) throw EOFException("SCP stream ended unexpectedly")
            if (value == delimiter.code) return output.toString(Charsets.UTF_8.name())
            output.write(value)
            if (output.size() > 16_384) throw SshConnectionFailed("SCP response is too long")
        }
    }
}
