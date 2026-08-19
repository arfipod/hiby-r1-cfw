# Elden Ring Steam Deck Save Downloader

Android app that connects to a Steam Deck over SSH/SFTP and downloads Elden Ring's `ER0000.sl2` and optionally `ER0000.sl2.bak` without modifying the remote save.

It discovers saves under:

`~/.local/share/Steam/steamapps/compatdata/1245620/pfx/drive_c/users/steamuser/AppData/Roaming/EldenRing/<SteamID>/ER0000.sl2`

Backups are written to `Downloads/EldenRingBackups/` on Android.
