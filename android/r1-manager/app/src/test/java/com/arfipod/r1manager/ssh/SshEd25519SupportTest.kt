package com.arfipod.r1manager.ssh

import com.jcraft.jsch.bc.SignatureEd25519
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.junit.Assert.assertTrue
import org.junit.Test

class SshEd25519SupportTest {
    @Test
    fun bouncyCastleImplementationCanSignAndVerify() {
        val seed = ByteArray(32) { index -> (index + 1).toByte() }
        val privateKey = Ed25519PrivateKeyParameters(seed, 0)
        val publicKey = privateKey.generatePublicKey().encoded
        val payload = "HiBy R1 host-key compatibility".toByteArray()

        val signer = SignatureEd25519().apply {
            init()
            setPrvKey(seed)
            update(payload)
        }
        val signature = signer.sign()

        val verifier = SignatureEd25519().apply {
            init()
            setPubKey(publicKey)
            update(payload)
        }

        assertTrue(verifier.verify(signature))
    }
}
