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

## 7. Build the branded SSH-toggle CFW

```bash
tools/build-ssh-cfw.sh
```

The script always starts from `r1.upt`, extracts two independent stock rootfs
trees, applies the SSH overlay, installs the multi-call binary and symlinks,
adds the About marker and native Developer Options row, rebuilds the image,
packs it, re-extracts the final `.upt`, compares the kernel byte for byte, and
enforces this exact rootfs delta:

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
changed: 31
  etc/shadow
  usr/bin/hiby_player
  usr/resource/layout/{theme1,theme2,midi/theme1}/hiby_about_dev.view
  usr/resource/str/<all 13 languages>/about_dev.ini
  usr/resource/str/<all 13 languages>/developer_options.ini
removed: 0
```

The default output is deliberately different from the earlier hardware-tested
lab filename, so the recovery image is not overwritten:

```text
dist/r1-cfw-ssh-toggle-1.6.upt
```

The locally validated build produced on 2026-08-11 has these identifiers:

```text
UPT size:          41,846,784 bytes
UPT SHA-256:       a723cc8b851fb06b5d0b88ee81da24d500c4f8b69394ff156a6c677e11f4bbd9
rootfs size:       37,699,584 bytes
rootfs MD5:        9b679a80bb021998631ce925e9235427
stock rootfs size: 37,507,072 bytes
size increase:        192,512 bytes
```

The 45 MiB main rootfs partition therefore retains 9,486,336 bytes of raw
capacity. The final QEMU user-mode smoke run reached the stock UI, exposed the
full 3,072,000-byte double framebuffer and recorded no shim crash. These checks
do not replace the first cold-boot and touch test on real hardware.

The build also reruns both patch verifiers on the final extracted filesystem.
The binary patcher accepts only the exact stock 1.6 `hiby_player` SHA-256 and
three exact instruction preimages; it fails rather than guessing on any other
firmware version. The stock `xImage` must remain byte-identical.

## 8. SSH behavior on the player

This CFW adds **SSH server** as a third row under Developer Options:

- Fresh install: the SSH switch is off.
- Upgrade from the previous SSH lab image: the existing host key is migrated to
  the enabled state, avoiding an unexpected lockout.
- SSH switch on, Developer Mode on, and Wi-Fi has IPv4: start/rebind Dropbear.
- Either switch off, Wi-Fi down, or address removed: stop Dropbear.
- Listen address: the exact `wlan0` IPv4 address only.
- Port: TCP 2222.
- User: `root`.
- Lab password: `hibyr1`.
- Persistent state: `/usr/data/dropbear/`.

The About page has an explicit `HiByR1 1,6 CFW` label in every stock language
and theme. The original model/version widget, its five-tap Developer Mode
callback, and `/usr/resource/config.json` remain byte-for-byte stock; the label
uses a separate resource and a non-overlapping text view near the page bottom.

Connect over Wi-Fi:

```bash
ssh -p 2222 root@R1_WIFI_IP
```

For key authentication, create `/usr/data/dropbear/authorized_keys`, copy one or
more OpenSSH public keys into it, and set mode 0600. The rootfs symlink makes that
persistent file visible as `/root/.ssh/authorized_keys`.

The password is deliberately weak. Use only a trusted isolated network, install
a public key promptly, and disable the SSH switch when access is unnecessary.
Forwarding, X11, agent forwarding, DSS, RSA/SHA-1, 3DES, and CBC are not compiled
into this server.

For emergency recovery, create an empty `CFW_SSH_ENABLE` file in the microSD
root before boot. That file intentionally overrides both UI gates while the card
is inserted. Remove it as soon as normal access and the persistent switch have
been restored.

CFW 0.1 exposes the same persistent SSH switch on its main page. It calls
`/usr/bin/r1-ssh-control`; it does not duplicate Dropbear lifecycle logic. An
enabled switch is only configuration state: an active listener additionally
requires Developer Mode and a `wlan0` IPv4 address, unless the explicit microSD
recovery override is present. The UI reports `<wlan0 IPv4>:2222` when an address
is available and otherwise reports that SSH is enabled without Wi-Fi.

USB is not required for SSH. If testing the stock ADB gadget, select **Device**:
that makes the R1 a USB peripheral. **OTG** makes the R1 the USB host and cannot
enumerate it as ADB on the computer.

## 9. Experimental launcher and CFW v0.1

### Architecture

The launcher integration is intentionally hybrid:

```text
generated external launcher resources
    + fail-open boot-time layout selector
    + one guarded in-memory callback route
    + independent /usr/bin/r1-cfw-ui sidecar
```

LiteGUI resources define tile geometry, icons, labels, visibility, and touch
bounds. Stock `hiby_player` descriptors select actions. The optional preload
hook verifies the exact dormant Step descriptor and callback preimages before
changing that callback pointer in process memory; it does not patch the launcher
callback on disk. The pre-existing Developer Options SSH integration remains a
separate exact, fail-closed stock-1.6 binary patch.

On a CFW tap, the hook transfers the existing framebuffer and touchscreen
descriptors to the sidecar and waits synchronously. Back, a connectivity route,
an exec failure, or a child signal restores the framebuffer pages and touch
ownership. A missing hook or failed runtime guard leaves the stock player
bootable and the dormant action unchanged.

### Launcher configuration

The seven visibility bits are:

```text
0x01 Music      0x02 Stream      0x04 Wireless    0x08 eBook
0x10 System     0x20 CFW         0x40 About
```

All 42 safe masks retain CFW and at least four large tiles. They are generated
for all three stock theme paths, giving 126 deterministic layout files. The
default mask is `0x71` (Music, System, CFW, About). The all-enabled mask is
`0x7f`; its 984-pixel content surface uses the stock vertical-scroll gesture
model rather than shrinking touch targets.

The sidecar atomically persists the selected two-digit lowercase hexadecimal
mask as:

```text
/usr/data/r1-cfw/launcher.conf
launcher_mask=71
```

`S90r1-cfw` validates a complete three-theme variant set before bind-mounting
it over the stock launcher resources. Missing or malformed state falls back to
`71`; any partial bind failure removes all CFW binds and exposes the stock
SquashFS layouts. Changes apply on the next player/userland restart because the
stock process caches its parsed launcher tree. Hidden Stream, Wireless, and
eBook tiles remain installed and can be restored; hiding a tile does not remove
its backend.

### CFW menu

The touch-only 480×800 sidecar provides:

- SSH configuration through `r1-ssh-control`;
- Wi-Fi and Bluetooth routes to the preserved stock Wireless hub;
- persistent launcher visibility controls;
- live internal-storage and mounted-microSD statistics from `statvfs`;
- RAM data from `/proc/meminfo`;
- CFW/stock version, kernel, uptime, Wi-Fi IP, and hostname;
- experimental CFW branding and About information.

Wi-Fi association and Bluetooth scanning/pairing remain stock responsibilities.
The shared Wireless-hub route preserves both configuration paths even when the
Wireless launcher tile is hidden. QEMU can validate that route and its Back
behavior, but not physical radio operation.

### Prepare, validate, publish

The release process is split so a successfully packed image is not automatically
treated as a distributable image:

```bash
tools/bootstrap-ssh-toolchain.sh
tools/build-dropbear-r1.sh
tools/build-cfw-0.1.sh prepare r1.upt
```

`prepare` requires the exact HiBy R1 1.6 input SHA-256
`9aada81995d8d2b2ed80d6cf292c62bc5f0f705e51e4f69c7e766ee67536ba60`.
It creates
`work/r1-cfw-0.1/r1-cfw-0.1-experimental.candidate.upt`, then re-extracts and
verifies the candidate. Checks include the OTA chains, byte-identical stock
`xImage`, exact patch/resource preimages, payload comparisons, a strict
added/changed/removed rootfs allowlist, and the 47,185,920-byte main-rootfs
partition ceiling.

The v0.1 build pins `umask` and `SOURCE_DATE_EPOCH`. UPT packing runs
`genisoimage` in UTC and normalizes only parsed ISO9660 Rock Ridge `TF`
timestamps, including continuation areas. Repeated delayed builds from the same
base and source tree therefore produce byte-identical UPT files. A fail-fast
lock below `work/r1-cfw-0.1/` covers the complete prepare/publish lifecycle, so
two invocations cannot mix or replace one another's verification trees. The
publish copy is also hashed again against the exact UPT verified earlier in the
same locked run before its atomic rename into `dist/`.

Inspect the fail-closed matrix, then validate the prepared candidate:

```bash
python3 tools/cfw_validate.py plan

candidate_rootfs_sha256=$(sha256sum \
  work/r1-cfw-0.1/check/images/rootfs.squashfs | awk '{print $1}')

python3 tools/cfw_validate.py run \
  --candidate-rootfs-dir work/r1-cfw-0.1/check-rootfs \
  --candidate-rootfs-image work/r1-cfw-0.1/check/images/rootfs.squashfs \
  --stock-rootfs-dir work/r1-cfw-0.1/stock-rootfs \
  --profile work/r1-1.6/rootfs-full/usr/data/user.ini \
  --runtime-root work/cfw-qemu-validation \
  --artifacts-dir artifacts/ui/cfw-v0.1 \
  --expected-rootfs-sha256 "$candidate_rootfs_sha256"
```

The validator hashes the candidate SquashFS, independently extracts that exact
image into the disposable per-run runtime, and executes every candidate session
from that extraction rather than trusting the parallel prepared tree. It hashes
the image again immediately before writing PASS, so a candidate changed during
the run cannot inherit its evidence.

The matrix covers the stock baseline; default and all-enabled launchers;
launcher persistence and scrolling; three sidecar open/Back cycles with
framebuffer restoration; and a forced sidecar `SIGABRT` followed by proof that
the player remains alive, its framebuffer is restored, and Music still accepts
touch input. It also covers SSH off/on; storage, SD, memory, and system pages;
and the Wi-Fi/Bluetooth stock-hub routes. Hardware radio, DAC, NAND, recovery,
and board-level behavior are explicitly outside qemu-user coverage.

The validation run writes its ignored evidence below `artifacts/ui/cfw-v0.1/`,
including a PASS manifest bound to the exact candidate rootfs SHA-256. The
publish stage independently re-verifies the candidate, all 23 mandatory PASS
records, and the identity, dimensions, complete PNG structure, and SHA-256 of
the three featured framebuffer captures. It fails for missing, malformed,
failed, tampered, or stale evidence:

```bash
tools/build-cfw-0.1.sh publish r1.upt
sha256sum dist/r1-cfw-0.1-experimental.upt
```

Only a successful publish creates
`dist/r1-cfw-0.1-experimental.upt`. Do not record or rely on a final UPT hash
before that point.

### Safety boundary

The v0.1 tools only read the immutable stock `r1.upt` and create files under
dedicated `work/`, `artifacts/`, and `dist/` paths. They do not flash hardware,
write `/dev/mtd*`, modify the bootloader/kernel/recovery image, format storage,
or invoke a device updater. Installing a candidate on a physical R1 is outside
this build and validation workflow.

## 10. Hardware test and recovery discipline

1. Use only normal R1 firmware, never R1 MIDI firmware.
2. Keep an untouched stock `r1.upt` and its SHA-256 on a separate FAT32 microSD.
3. Fully charge the battery and do not interrupt NAND writes.
4. Confirm the stock recovery/update gesture before installing a CFW.
5. Start with one reversible change and keep the kernel byte-identical.
6. After boot, collect `/proc/mtd`, `/proc/cmdline`, mounts, modules, and dmesg.
7. Do not alter the captured main/recovery MTD map, kernel, PMIC, or bootloader;
   the 24 MiB `rootfs2` is recovery and cannot hold the full main filesystem.

The earlier SSH lab artifact has been cold-booted on hardware and its immutable
filesystem matched the locally extracted image. The new native-toggle binary
hook remains experimental until it is cold-booted and both UI states are tested.
The vendor uses a dedicated recovery kernel/rootfs to validate and write the
main image; it is not symmetric A/B and does not make an untested application
patch risk-free.

## 11. CI behavior

`.github/workflows/ci.yml` runs on every branch push and every pull request. It:

1. installs only open host tooling;
2. checks Python bytecode compilation;
3. checks every POSIX shell script with `sh -n`;
4. runs unit tests for chains, metadata, uImage CRC, JPEG/PNG parsing, overlays,
   strict rootfs diffs, launcher generation/state, guarded integration patches,
   sidecar data/input behavior, QEMU helpers, and the release evidence gate;
5. creates, packs, unpacks, and byte-compares a synthetic `.upt` fixture;
6. fails if firmware images or `work/` build trees are committed.

CI intentionally does not download or redistribute HiBy firmware. The final ABI,
QEMU, and CFW build commands remain reproducible local gates when `r1.upt` is
available.

## 12. Adding further features

Recommended order of increasing risk:

1. External resources, layouts, translations, or one known hidden option.
2. A sidecar process with a trigger and a stock-player fallback.
3. A dynamic MIPS32 framebuffer/input/ALSA application.
4. A narrow, well-understood `LD_PRELOAD` hook.
5. A proprietary player binary patch.
6. Kernel, module, MTD, or bootloader changes.

For a new feature, prefer an independently testable sidecar that can fail or exit
without preventing the stock player from launching.
