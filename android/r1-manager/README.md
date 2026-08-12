# R1 Manager for Android

R1 Manager is a native Android client for the HiBy R1 CFW. It connects to the
existing Dropbear SSH server on Wi-Fi, uses SSH execution for the constrained
`/usr/bin/r1-filectl` protocol, and uses Dropbear's SCP implementation only for
file bytes. It does not require a second server or daemon on the player.

## First-run setup

1. On the R1, enable **Developer Mode** and **SSH Server** in the CFW menu.
2. Add a profile in the app with the R1 Wi-Fi IP, port `2222`, SSH username and
   password. The current lab firmware uses `root` and `hibyr1`; a public key or
   a stronger password is preferable before using an untrusted network.
3. Review the ED25519 host-key fingerprint shown on the first connection. The
   app stores the approved fingerprint and rejects an unexpected replacement.
4. Keep the phone and R1 on the same Wi-Fi network.

Passwords are encrypted with an AES/GCM key held by Android Keystore. Profiles,
password ciphertext and host-key pins are excluded from Android cloud backup
and device transfer.

## Included v0.1 flows

- multiple saved R1 profiles;
- explicit connection, authentication, host-key, helper and microSD errors;
- real microSD browsing with breadcrumbs and refresh after every mutation;
- create folder, rename, move and explicit conflict policies;
- multi-selection, Trash, restore, purge and permanent recursive delete;
- download one file to an Android document or several files to a selected tree;
- select or share audio from Android;
- local ID3/media metadata planning as
  `Music/Album Artist/Album/Track - Title.ext`;
- review metadata destinations before upload;
- staged SCP uploads with free-space checks, progress, exact-size verification,
  `fsync`, conflict handling and an atomic final rename;
- a sequential transfer queue that resumes the same operation after approving
  the R1 host key.

The remote R1 filesystem remains the source of truth. The app does not update a
folder optimistically: after a successful mutation it asks `r1-filectl` for a
fresh listing. After an uncertain transport failure it reports the problem
instead of claiming the operation completed.

## Build

The project targets Android API 36, supports Android 8.0 and newer (`minSdk 26`),
and is tested in CI with JDK 17, Gradle 8.13 and AGP 8.13.2:

```sh
cd android/r1-manager
gradle :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

GitHub Actions publishes the signed, installable debug APK as the
`r1-manager-debug-apk` workflow artifact. The intended physical target is an
HONOR Magic5 Pro running Android 16 / MagicOS 10, while the app uses standard
Android storage, networking and Keystore APIs rather than vendor-specific APIs.

## Security model

`r1-filectl` accepts only relative Base64URL paths below the mounted microSD,
blocks `.` and `..`, never follows symbolic links, and hides its private staging
and Trash directory. Android never interpolates a remote file name into an
arbitrary shell command. See [`docs/r1-filectl-protocol.md`](../../docs/r1-filectl-protocol.md).
