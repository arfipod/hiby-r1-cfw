package com.arfipod.r1manager.util

import com.arfipod.r1manager.model.RemoteKind
import java.util.Base64

object Protocol {
    fun encode(value: String): String = if (value.isEmpty()) {
        "-"
    } else {
        Base64.getUrlEncoder().withoutPadding().encodeToString(value.toByteArray(Charsets.UTF_8))
    }

    fun decode(value: String): String {
        if (value == "-") return ""
        return Base64.getUrlDecoder().decode(value).toString(Charsets.UTF_8)
    }

    fun shellQuote(value: String): String = "'" + value.replace("'", "'\\''") + "'"

    fun kind(value: String): RemoteKind = when (value) {
        "file" -> RemoteKind.FILE
        "directory" -> RemoteKind.DIRECTORY
        "symlink" -> RemoteKind.SYMLINK
        else -> RemoteKind.OTHER
    }
}
