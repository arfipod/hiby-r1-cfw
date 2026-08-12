package com.arfipod.r1manager.ssh

import com.jcraft.jsch.bc.SignatureEd25519
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.junit.Assert.assertTrue
import org.junit.Test

class SshEd25519SupportTest {
    @Test
    fun bouncyCastleImplementationCanVerifySshEd25519Blob() {
        val seed = ByteArray(32) { index -> (index + 1).toByte() }
        val privateKey = Ed25519PrivateKeyParameters(seed, 0)
        val publicKey = privateKey.generatePublicKey().encoded
        val payload = "HiBy R1 host-key compatibility".toByteArray()

        val signer = SignatureEd25519().apply {
            init()
            setPrvKey(seed)
            update(payload)
        }
        val rawSignature = signer.sign()
        val sshSignature = sshString("ssh-ed25519".toByteArray()) + sshString(rawSignature)

        val verifier = SignatureEd25519().apply {
            init()
            setPubKey(publicKey)
            update(payload)
        }

        assertTrue(verifier.verify(sshSignature))
    }

    private fun sshString(value: ByteArray): ByteArray {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).use { output ->
            output.writeInt(value.size)
            output.write(value)
        }
        return bytes.toByteArray()
    }
}
