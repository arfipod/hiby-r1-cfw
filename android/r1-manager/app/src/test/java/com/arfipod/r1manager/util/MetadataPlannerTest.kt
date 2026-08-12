package com.arfipod.r1manager.util

import org.junit.Assert.assertEquals
import org.junit.Test

class MetadataPlannerTest {
    @Test
    fun albumArtistWinsForCollaborations() {
        assertEquals(
            "Music/Jorge Drexler/Taracá/11 - Las palabras.mp3",
            MetadataPlanner.destination(
                musicRoot = "Music",
                albumArtist = "Jorge Drexler",
                artist = "Jorge Drexler, Falta y Resto",
                album = "Taracá",
                track = 11,
                title = "Las palabras",
                originalName = "11. Jorge Drexler, Falta y Resto - Las palabras.mp3",
            ),
        )
    }

    @Test
    fun missingTagsUsePredictableFallbacks() {
        assertEquals(
            "Audio/Unknown Artist/Unknown Album/recording.flac",
            MetadataPlanner.destination(
                musicRoot = "/Audio/",
                albumArtist = null,
                artist = null,
                album = null,
                track = null,
                title = null,
                originalName = "recording.flac",
            ),
        )
    }

    @Test
    fun pathComponentsAreSanitized() {
        assertEquals(
            "Music/Artist Name/Album Name/03 - Title with separators.mp3",
            MetadataPlanner.destination(
                musicRoot = "Music",
                albumArtist = "Artist/Name",
                artist = null,
                album = "Album:Name",
                track = 3,
                title = "Title*with?separators",
                originalName = "ignored.MP3",
            ),
        )
    }
}
