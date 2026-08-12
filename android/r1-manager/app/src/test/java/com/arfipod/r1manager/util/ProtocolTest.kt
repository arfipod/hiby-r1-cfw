package com.arfipod.r1manager.util

import com.arfipod.r1manager.model.RemoteKind
import org.junit.Assert.assertEquals
import org.junit.Test

class ProtocolTest {
    @Test
    fun base64UrlRoundTripPreservesUnicodeAndRoot() {
        val values = listOf("", "Music/Jorge Drexler/Taracá/11 - Las palabras.mp3", "literal $(not-a-shell)")
        values.forEach { value -> assertEquals(value, Protocol.decode(Protocol.encode(value))) }
        assertEquals("-", Protocol.encode(""))
    }

    @Test
    fun shellQuoteKeepsSingleQuotesLiteral() {
        assertEquals("'A'\\''B'", Protocol.shellQuote("A'B"))
    }

    @Test
    fun remoteKindsAreStable() {
        assertEquals(RemoteKind.FILE, Protocol.kind("file"))
        assertEquals(RemoteKind.DIRECTORY, Protocol.kind("directory"))
        assertEquals(RemoteKind.SYMLINK, Protocol.kind("symlink"))
        assertEquals(RemoteKind.OTHER, Protocol.kind("socket"))
    }
}
