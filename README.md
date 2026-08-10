# HiBy R1 firmware 1.6 reverse engineering and CFW tools

This repository contains a reproducible analysis of the HiBy R1 `r1.upt`
firmware, tools to unpack and rebuild it, resource-export helpers, a diagnostic
overlay, and an experimental Dropbear SSH CFW. The stock input is never modified
in place and firmware images are intentionally excluded from Git.

## Current status

- The ISO/OTA container, chained chunks, MD5 manifests, SquashFS, and U-Boot
  `xImage` CRCs are validated during unpacking.
- SquashFS can be extracted with metadata, modified with reviewable overlays,
  rebuilt with the stock LZO geometry, packed, and re-extracted for verification.
- `export-assets` exports all loose `/usr/resource` files and the three current
  boot splashes, plus a SHA-256/geometry manifest.
- Dropbear 2026.94 is reproducibly cross-compiled for MIPS32r2 little-endian,
  o32 hard-float, and glibc 2.22. Its highest referenced glibc symbol version is
  2.19.
- Public-key and password SSH logins have passed an end-to-end QEMU user-mode
  test against the R1 rootfs. Full X1600 board emulation is not available.
- The final SSH image is re-extracted and checked against an exact rootfs change
  allowlist. It has **not yet been boot-tested on physical hardware**.

See [firmware-1.6-analysis.md](docs/firmware-1.6-analysis.md) for the complete
anatomy and [cfw-guide.md](docs/cfw-guide.md) for build, test, and recovery notes.

## Firmware anatomy

```text
r1.upt (ISO-9660)
├── ota_config.in
└── ota_v0/
    ├── ota_update.in
    ├── ota_md5_xImage.<whole-image-md5>
    ├── xImage.0000.<whole-image-md5>
    ├── xImage.0001.<previous-chunk-md5>
    ├── ...
    ├── ota_md5_rootfs.squashfs.<whole-image-md5>
    └── rootfs.squashfs.NNNN.<chained-md5>

xImage             U-Boot uImage, Linux 4.4.94+, MIPS32, Ingenic X1600
rootfs.squashfs     SquashFS 4.0 LE, LZO, 128 KiB blocks, glibc 2.22
```

The updater does not expose a cryptographic firmware signature check. Its MD5
chain and uImage CRC32 fields detect corruption but do not authenticate the
publisher. Kernel and rootfs use A/B pairs; the updater changes the boot marker
only after writing the inactive pair.

## Quick start

System requirements for the base tools are Python 3, `7z`, `unsquashfs`,
`mksquashfs`, and `genisoimage`. The QEMU SSH test also uses `socat`, OpenSSH,
OpenSSL, and `setsid` from util-linux.

```bash
python3 tools/r1fw.py unpack r1.upt work/original
python3 tools/r1fw.py inspect-kernel work/original/images/xImage
python3 tools/r1fw.py extract-rootfs \
  work/original/images/rootfs.squashfs work/rootfs

# Export the stock splash, all loose UI resources, and a manifest.
python3 tools/r1fw.py export-assets work/rootfs work/exported-assets

# Replace all three boot-splash variants with a validated 480x800 JPEG.
python3 tools/r1fw.py install-splash work/rootfs splash.jpg
```

To reproduce the SSH lab CFW:

```bash
tools/bootstrap-ssh-toolchain.sh
tools/build-dropbear-r1.sh
tools/test-dropbear-qemu.sh
tools/build-ssh-cfw.sh
```

The resulting local artifact is `dist/r1-cfw-ssh-lab-1.6.upt`. Developer Mode
is the SSH switch: enabled starts SSH when Wi-Fi has an IPv4 address; disabled
stops it. Connect to the displayed Wi-Fi IP on port 2222:

```bash
ssh -p 2222 root@R1_WIFI_IP
```

The lab password is `hibyr1`. It is intentionally weak and must only be used on
a trusted test network. Public keys in `/usr/data/dropbear/authorized_keys` are
preferred.

## CI

`.github/workflows/ci.yml` runs for pushes to every branch and for pull requests.
It checks Python and POSIX shell syntax, runs unit tests, performs a complete
synthetic `.upt`/SquashFS round trip, and rejects accidentally committed firmware
or `work/` trees. Proprietary stock firmware is not required by CI.

## Known limits

The OTA wrapper, Linux boot chain, filesystem, init scripts, and updater logic are
characterized. `hiby_player` remains a proprietary stripped MIPS executable; its
external assets and integration points are inventoried, but this repository does
not claim a complete source-level reconstruction. The physical partition table,
installed bootloader behavior, and recovery path still require captures from a
real player before kernel, module, MTD, or bootloader experiments are reasonable.
