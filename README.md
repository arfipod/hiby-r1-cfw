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
- The stock LiteGUI layouts can be linted, rendered, and inspected without
  losing their repeated JSON-like widget keys. All 438 firmware 1.6 layouts
  parse successfully.
- The real proprietary `hiby_player` reaches its 480×800 UI under qemu-user
  through emulator-only framebuffer, HGL DMA, evdev touch, alignment, SD, and
  `sys_server` adapters. A safe navigator records curated UI smoke artifacts.
- The first SSH lab image has booted on physical R1 hardware and its immutable
  files were matched against the locally re-extracted build. The newer toggle
  image is still treated as experimental until it completes the same cold-boot
  test.

See [firmware-1.6-analysis.md](docs/firmware-1.6-analysis.md) for the complete
anatomy, [cfw-guide.md](docs/cfw-guide.md) for build/test/recovery notes, and
[ui-emulation.md](docs/ui-emulation.md) for static and real-player UI testing.

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
publisher. Hardware capture corrected an earlier assumption: `kernel2` and
`rootfs2` are a smaller recovery system, not a second full-size main-OS slot.
The recovery updater writes the 45 MiB main rootfs and returns the boot marker
to `ota:kernel` after success.

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

The resulting local artifact is `dist/r1-cfw-ssh-toggle-1.6.upt`. The About page
shows `HiByR1 1,6 CFW`, and Developer Options contains an independent
**SSH server** switch. Both Developer Mode and that switch must be on; Dropbear
then follows the current Wi-Fi IPv4 address on port 2222. A fresh installation
defaults to off, while an upgrade from the earlier lab build preserves its
enabled state so it does not unexpectedly remove the recovery channel.
The validated artifact is 41,846,784 bytes with SHA-256
`a723cc8b851fb06b5d0b88ee81da24d500c4f8b69394ff156a6c677e11f4bbd9`.

Connect to the displayed Wi-Fi IP on port 2222:

```bash
ssh -p 2222 root@R1_WIFI_IP
```

The lab password is `hibyr1`. It is intentionally weak and must only be used on
a trusted test network. Public keys in `/usr/data/dropbear/authorized_keys` are
preferred.

For emergency recovery, an empty `CFW_SSH_ENABLE` file in the microSD root
temporarily overrides both UI gates. Remove it after access is restored.

## UI research lab

Render and inspect the stock external resources:

```bash
python3 tools/r1ui.py lint work/r1-1.6/rootfs
python3 tools/r1ui.py serve work/r1-1.6/rootfs --port 8765
```

Run the real stock UI and open its local mouse-as-touch viewer in a second
terminal:

```bash
tools/run-r1-ui-qemu.sh

python3 tools/r1-qemu-ui/bridge.py serve \
  work/gui-qemu/runtime/framebuffer.raw \
  work/gui-qemu/runtime/touch/event0 \
  --state work/gui-qemu/runtime/frame-state.bin
```

If `test-audio.mp3` is present at the repository root, the runner copies it into
the disposable simulated SD fixture. The curated navigator can review or run a
non-destructive route through every launcher branch and that audio file:

```bash
python3 tools/r1-qemu-ui/navigate.py smoke --dry-run
python3 tools/r1-qemu-ui/navigate.py smoke
```

This is application-level qemu-user emulation, not a virtual X1600 board. DAC,
radio, USB, update, and recovery behavior still require physical hardware.

## CI

`.github/workflows/ci.yml` runs for pushes to every branch and for pull requests.
It checks Python and POSIX shell syntax, runs unit tests, performs a complete
synthetic `.upt`/SquashFS round trip, and rejects accidentally committed firmware
or `work/` trees. Proprietary stock firmware is not required by CI.

## Known limits

The OTA wrapper, Linux boot chain, filesystem, init scripts, physical MTD map,
and main/recovery update roles are characterized. `hiby_player` remains a
proprietary stripped MIPS executable; its external assets and integration points
are inventoried, but this repository does not claim a complete source-level
reconstruction. Installed bootloader behavior and failure recovery still need
more hardware testing before kernel, module, MTD, or bootloader experiments are
reasonable.
