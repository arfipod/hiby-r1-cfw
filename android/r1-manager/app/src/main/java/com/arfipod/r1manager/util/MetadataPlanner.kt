package com.arfipod.r1manager.util

import android.content.ContentResolver
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.OpenableColumns
import com.arfipod.r1manager.model.LocalAudioPlan
import java.util.Locale

object MetadataPlanner {
    private val forbidden = Regex("[\\\\/:*?\"<>|\\p{Cc}]")

    fun sanitize(component: String?, fallback: String): String {
        val cleaned = component.orEmpty()
            .replace(forbidden, " ")
            .replace(Regex("\\s+"), " ")
            .trim(' ', '.')
        return cleaned.ifBlank { fallback }.take(180)
    }

    fun destination(
        musicRoot: String,
        albumArtist: String?,
        artist: String?,
        album: String?,
        track: Int?,
        title: String?,
        originalName: String,
    ): String {
        val root = musicRoot.trim('/').ifBlank { "Music" }
        val chosenArtist = sanitize(
            albumArtist?.takeIf(String::isNotBlank) ?: artist,
            "Unknown Artist",
        )
        val chosenAlbum = sanitize(album, "Unknown Album")
        val extension = originalName.substringAfterLast('.', "mp3")
            .lowercase(Locale.ROOT)
            .replace(Regex("[^a-z0-9]"), "")
            .ifBlank { "mp3" }
            .take(8)
        val chosenTitle = sanitize(title ?: originalName.substringBeforeLast('.'), "Unknown Title")
        val prefix = track?.takeIf { it > 0 }?.let { "%02d - ".format(Locale.ROOT, it) }.orEmpty()
        return "$root/$chosenArtist/$chosenAlbum/$prefix$chosenTitle.$extension"
    }

    fun inspect(resolver: ContentResolver, uri: Uri, musicRoot: String): LocalAudioPlan {
        var displayName = "audio"
        var size = -1L
        resolver.query(
            uri,
            arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null,
            null,
            null,
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                val nameColumn = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val sizeColumn = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (nameColumn >= 0 && !cursor.isNull(nameColumn)) {
                    displayName = cursor.getString(nameColumn) ?: displayName
                }
                if (sizeColumn >= 0 && !cursor.isNull(sizeColumn)) {
                    size = cursor.getLong(sizeColumn)
                }
            }
        }

        var title = displayName.substringBeforeLast('.')
        var artist = ""
        var albumArtist = ""
        var album = ""
        var track: Int? = null
        val retriever = MediaMetadataRetriever()
        try {
            resolver.openAssetFileDescriptor(uri, "r")?.use { descriptor ->
                val declared = descriptor.declaredLength
                val statSize = descriptor.parcelFileDescriptor.statSize
                if (size < 0) size = when {
                    declared >= 0 -> declared
                    statSize >= 0 -> statSize
                    else -> -1L
                }
                if (declared >= 0) {
                    retriever.setDataSource(
                        descriptor.fileDescriptor,
                        descriptor.startOffset,
                        declared,
                    )
                } else {
                    retriever.setDataSource(descriptor.fileDescriptor)
                }
                title = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE) ?: title
                artist = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST).orEmpty()
                albumArtist = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ALBUMARTIST).orEmpty()
                album = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ALBUM).orEmpty()
                track = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_CD_TRACK_NUMBER)
                    ?.substringBefore('/')
                    ?.trim()
                    ?.toIntOrNull()
            }
        } catch (_: RuntimeException) {
            // Some document providers expose playable bytes but no seekable metadata
            // descriptor. Keep the filename fallbacks and let SCP transfer the file.
        } finally {
            retriever.release()
        }
        require(size >= 0) { "Android did not report the selected file size." }

        val destination = destination(
            musicRoot,
            albumArtist,
            artist,
            album,
            track,
            title,
            displayName,
        )
        return LocalAudioPlan(
            uri = uri,
            displayName = displayName,
            size = size,
            title = title,
            artist = artist,
            albumArtist = albumArtist,
            album = album,
            track = track,
            destination = destination,
            metadataComplete = album.isNotBlank() &&
                (albumArtist.isNotBlank() || artist.isNotBlank()),
        )
    }
}
