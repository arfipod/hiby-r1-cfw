# Building and testing HiBy R1 custom firmware

## 1. Base requirements

On Debian/Ubuntu:

```bash
sudo apt install python3 p7zip-full squashfs-tools genisoimage u-boot-tools \
  socat openssh-client openssl util-linux
```

`r1fw.py` also accepts explicit `--unsquashfs`, `--mksquashfs`, and
`--genisoimage` paths. Build trees and all `.upt` files are ignored by Git.

## 2. Extract and validate stock firmware

```bash
python3 tools/r1fw.py unpack r1.upt work/original
python3 tools/r1fw.py inspect-kernel work/original/images/xImage
python3 tools/r1fw.py extract-rootfs \
  work/original/images/rootfs.squashfs work/rootfs
```

`unpack` fails for a missing/non-contiguous chunk, a broken filename chain, a
manifest mismatch, or a wrong final size/hash. `inspect-kernel` validates both
uImage CRC32 fields. Do not use 7-Zip to create an editable rootfs tree because
it omits valid relative symlinks.

## 3. Obtain the current splash and UI assets

Export everything before changing it:

```bash
python3 tools/r1fw.py export-assets \
  work/rootfs work/exported-assets
```

The output contains:

```text
work/exported-assets/
├── splash/
│   ├── logo.jpeg
│   ├── logo1.jpeg
│   └── logo2.jpeg
├── resource/          complete stock /usr/resource tree
└── manifest.tsv       type, byte size, geometry, SHA-256 or symlink target
```

There are 1,845 loose PNG UI assets and three splash JPEGs in firmware 1.6.
They are all external raster files, but text, vector-like primitives, runtime
album art, and images embedded in proprietary executables are outside this count.

Install a replacement splash:

```bash
python3 tools/r1fw.py install-splash \
  work/rootfs my-splash-480x800.jpg
```

The input must be an 8-bit 480×800 JPEG. The tool updates all three theme-selected
variants.

## 4. Apply reviewable overlays

An overlay contains only paths to add or replace:

```bash
python3 tools/r1fw.py apply-overlay \
  work/rootfs mods/diagnostic-logging/rootfs-overlay
```

Directory permissions are preserved when a directory is newly created, but an
overlay cannot silently change an existing stock directory's mode. Files and
symlinks retain their overlay metadata.

### Diagnostic logging overlay

The diagnostic launcher preserves normal player behavior unless a file named
`CFW_LOG` exists at the microSD root. After `hiby_player` causes `sys_server` to
mount the card, stdout/stderr is written to
`/data/mnt/sd_0/cfw-logs/hiby_player.log`. It retains the newest 1 MiB after the
file exceeds 4 MiB. `cfw-info.sh` also captures kernel, cmdline, MTD, mounts,
memory, processes, modules, and dmesg.

## 5. Reproducible SSH toolchain

The bootstrap downloads into `work/` and installs nothing system-wide:

```bash
tools/bootstrap-ssh-toolchain.sh
```

It pins and verifies:

- Dropbear 2026.94 source SHA-256 and detached signature, including signing-key
  fingerprint `F734 7EF2 EE2E 07A2 6762 8CA9 4493 1494 F29C 6773`;
- Zig 0.16.0 and its official SHA-256;
- locally extracted Debian MIPS binutils, QEMU user-mode, and PRoot packages.

Build and inspect the server:

```bash
tools/build-dropbear-r1.sh
file work/ssh-build/output/dropbearmulti
readelf -d work/ssh-build/output/dropbearmulti
readelf -V work/ssh-build/output/dropbearmulti
```

Expected properties are ELF32 little-endian MIPS32r2, o32, PIE, interpreter
`/lib/ld.so.1`, dependencies on `libcrypt.so.1`, `libc.so.6`, and
`libutil.so.1`, and no glibc symbol newer than 2.19.

## 6. QEMU validation

```bash
tools/test-dropbear-qemu.sh
```

The test uses a loopback-only dynamic TCP port. It checks the guest loader,
generates an ED25519 key, logs in with a generated client key, logs in again with
the lab password, and runs the R1 shell as root. PRoot needs `ptrace`; a restricted
sandbox may require approval.

This is QEMU **user-mode** validation, not a virtual R1. QEMU does not model the
Ingenic X1600 board, NAND/UBI, display, touch, DAC, PMIC, or Wi-Fi. A successful
test substantially validates userland but cannot prove the device will boot.

## 7. Build the SSH lab CFW

```bash
tools/build-ssh-cfw.sh
```

The script always starts from `r1.upt`, extracts two independent stock rootfs
trees, applies the SSH overlay, installs the multi-call binary and symlinks,
rebuilds the image, packs it, re-extracts the final `.upt`, compares the kernel
byte for byte, and enforces this exact rootfs delta:

```text
added: 8
  etc/init.d/S91dropbear
  root/.ssh
  root/.ssh/authorized_keys -> /usr/data/dropbear/authorized_keys
  usr/bin/dropbearkey -> ../sbin/dropbearmulti
  usr/bin/r1-ssh-control
  usr/bin/scp -> ../sbin/dropbearmulti
  usr/sbin/dropbear -> dropbearmulti
  usr/sbin/dropbearmulti
changed: 1
  etc/shadow
removed: 0
```

Current locally verified artifact:

```text
dist/r1-cfw-ssh-lab-1.6.upt
Size     41,842,688 bytes
SHA-256  459412b7de66338febed8b5a22e964d3bb8286f24d0f7d7dcb05d48ce150a635
```

The rootfs is 188,416 bytes larger than stock after compression and padding. The
stock `xImage` MD5 remains `022410af2bb16150f9597d14098dfe42`.

## 8. SSH behavior on the player

This CFW reuses the existing Developer Mode setting:

- Developer Mode enabled and Wi-Fi has IPv4: start/rebind Dropbear.
- Developer Mode disabled, Wi-Fi down, or address removed: stop Dropbear.
- Listen address: the exact `wlan0` IPv4 address only.
- Port: TCP 2222.
- User: `root`.
- Lab password: `hibyr1`.
- Persistent state: `/usr/data/dropbear/`.

Connect over Wi-Fi:

```bash
ssh -p 2222 root@R1_WIFI_IP
```

For key authentication, create `/usr/data/dropbear/authorized_keys`, copy one or
more OpenSSH public keys into it, and set mode 0600. The rootfs symlink makes that
persistent file visible as `/root/.ssh/authorized_keys`.

The password is deliberately weak. Use only a trusted isolated network, install
a public key promptly, and disable Developer Mode when access is unnecessary.
Forwarding, X11, agent forwarding, DSS, RSA/SHA-1, 3DES, and CBC are not compiled
into this server.

USB is not required for SSH. If testing the stock ADB gadget, select **Device**:
that makes the R1 a USB peripheral. **OTG** makes the R1 the USB host and cannot
enumerate it as ADB on the computer.

## 9. Hardware test and recovery discipline

1. Use only normal R1 firmware, never R1 MIDI firmware.
2. Keep an untouched stock `r1.upt` and its SHA-256 on a separate FAT32 microSD.
3. Fully charge the battery and do not interrupt NAND writes.
4. Confirm the stock recovery/update gesture before installing a CFW.
5. Start with one reversible change and keep the kernel byte-identical.
6. After boot, collect `/proc/mtd`, `/proc/cmdline`, mounts, modules, and dmesg.
7. Do not alter the kernel, PMIC, MTD indexes, bootloader, or rootfs partition
   assumptions until the real device map and recovery behavior are recorded.

The current artifact is host- and QEMU-verified but not hardware-verified. It
must be treated as experimental even though the vendor's A/B updater reduces the
risk of an interrupted write.

## 10. CI behavior

`.github/workflows/ci.yml` runs on every branch push and every pull request. It:

1. installs only open host tooling;
2. checks Python bytecode compilation;
3. checks every POSIX shell script with `sh -n`;
4. runs unit tests for chains, metadata, uImage CRC, JPEG/PNG parsing, overlays,
   and strict rootfs diffs;
5. creates, packs, unpacks, and byte-compares a synthetic `.upt` fixture;
6. fails if firmware images or `work/` build trees are committed.

CI intentionally does not download or redistribute HiBy firmware. The final ABI,
QEMU, and CFW build commands remain reproducible local gates when `r1.upt` is
available.

## 11. Adding further features

Recommended order of increasing risk:

1. External resources, layouts, translations, or one known hidden option.
2. A sidecar process with a trigger and a stock-player fallback.
3. A dynamic MIPS32 framebuffer/input/ALSA application.
4. A narrow, well-understood `LD_PRELOAD` hook.
5. A proprietary player binary patch.
6. Kernel, module, MTD, or bootloader changes.

For a new feature, prefer an independently testable sidecar that can fail or exit
without preventing the stock player from launching.
