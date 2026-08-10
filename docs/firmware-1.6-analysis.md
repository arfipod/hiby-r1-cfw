# HiBy R1 firmware 1.6 reverse-engineering notes

## 1. Identification and reproducibility

| Field | Value |
|---|---|
| Input | `r1.upt` |
| Size | 41,652,224 bytes |
| SHA-256 | `9aada81995d8d2b2ed80d6cf292c62bc5f0f705e51e4f69c7e766ee67536ba60` |
| Container | ISO-9660, volume `CDROM` |
| ISO creation time | 2025-12-29 12:24:10 |
| Declared product/version | `HiBy R1`, `v1.6` |

The SHA-256 matches the `r1/firmware/original/r1(v1.6).upt` copy in
[hiby_os_crack](https://github.com/hiby-modding/hiby_os_crack) byte for byte.

## 2. `.upt` format

`ota_config.in` selects `ota_v0`. Its `ota_update.in` declares two images:

| Image | Size | Whole-image MD5 | Chunks |
|---|---:|---|---:|
| `xImage` | 3,731,520 | `022410af2bb16150f9597d14098dfe42` | 8 |
| `rootfs.squashfs` | 37,507,072 | `9c8b3a941dc2324ed6a641760928959c` | 72 |

Chunks are 524,288 bytes except for the final one. The suffix is also a chain:

```text
chunk 0000: image.0000.<whole-image MD5>
chunk N:    image.NNNN.<MD5 of chunk N-1>
manifest:   ota_md5_image.<whole-image MD5>
```

The manifest lists each chunk's real MD5 in order. `r1fw.py` validates contiguous
indexes, filename chaining, manifest values, total size, and whole-image MD5. Its
packer reproduces the same scheme.

No RSA/ECDSA signature, certificate, or public key was found in the package or
the readable updater scripts. MD5 and CRC32 provide integrity checks, not source
authentication. This is consistent with public projects being able to produce
modified `.upt` files without a HiBy private key.

## 3. Kernel `xImage`

This is a 64-byte legacy U-Boot uImage header followed by its payload:

| Field | Value |
|---|---|
| Name | `Linux-4.4.94+` |
| Architecture | MIPS |
| OS/type | Linux kernel |
| uImage compression | none |
| Load/entry address | `0x80f00000` |
| Payload size | 3,731,456 bytes |
| Header CRC | `0x29d467ee` |
| Data CRC | `0xb44e5372` |

Both CRCs were independently verified. The payload contains a MIPS decompressor
and a gzip stream that expands to an 8,143,180-byte kernel binary. Its build
string is:

```text
Linux version 4.4.94+ (zcz@androidserver3)
(gcc version 5.2.0 (Ingenic r3.2.1-gcc520 2017.12-15))
#12 PREEMPT Mon Dec 29 18:21:01 CST 2025
```

Strings and modules identify an Ingenic X1600/X1600E platform. The first CFWs
keep this kernel byte-identical; resource and userland changes do not need a
kernel rebuild.

## 4. Root filesystem

| Property | Value |
|---|---|
| Format | SquashFS 4.0 little-endian |
| Compression | LZO |
| Block size | 131,072 bytes |
| Logical SquashFS size | 37,506,078 bytes |
| Padded image size | 37,507,072 bytes |
| Inodes / files / directories | 5,076 / 4,306 / 288 |
| Symlinks / hardlinks | 482 / 412 |
| Userland ABI | BusyBox + glibc 2.22, MIPS32r2 o32 LE hard-float |

Use `unsquashfs`, not 7-Zip, for an editable tree: 7-Zip omits relative symlinks
it considers unsafe. `mksquashfs -comp lzo -b 131072 -all-root` preserves the
required layout.

### Hardware visible in modules

- Ingenic X1600/X1600E SoC.
- Cirrus Logic CS43131 DAC and the `x1600_hiby_r1` sound card.
- AXP2101 PMIC.
- LG35583 480×800 display and CST8xx touch controller.
- Cypress/CYW Wi-Fi with BCM firmware.
- MMC, GPIO, PWM, USB gadget, and other Ingenic platform drivers.

## 5. Boot sequence

```text
U-Boot
  └─ Linux + active SquashFS rootfs
       └─ BusyBox init: /etc/inittab
            └─ /etc/init.d/rcS
                 ├─ S11module_driver_default  → X1600/R1 modules
                 ├─ S11jpeg_display_shell    → splash and backlight
                 ├─ S21mount_ubifs           → writable /usr/data
                 ├─ S43wifi_bcm_init_config  → wlan0 device setup
                 ├─ S80_bt_init              → Bluetooth
                 └─ S92_03_start_music_player
                      └─ /usr/bin/hiby_player.sh
                           └─ /usr/bin/hiby_player
```

`/etc/inittab` also respawns a console shell. `rcS` runs only `S??*` scripts, so
the stock `T90adb` is not directly executed. ADB support exists in the rootfs,
but USB mode switching depends on application/UI behavior and was not reliable
on the connected device.

### Developer Mode state

The stock ADB scripts establish the persistent toggle convention:

```text
/usr/data/disableadb exists   Developer Mode / ADB disabled
/usr/data/disableadb absent   Developer Mode / ADB enabled
```

The SSH modification deliberately follows this existing marker instead of
binary-patching the proprietary settings UI to add a new widget.

### Boot splash

`S11jpeg_display_shell` waits for `/dev/fb0`, reads the theme from `/dev/mtd5` at
offset `0x20000`, and selects one of:

- `/etc/logo.jpeg`: 480×800 baseline JPEG.
- `/etc/logo1.jpeg`: 480×800 progressive JPEG.
- `/etc/logo2.jpeg`: 480×720 in stock firmware.

`install-splash` validates an 8-bit 480×800 JPEG and writes it to all three paths
so a theme switch cannot reveal an unchanged stock variant. `export-assets`
copies the current three files out before modification.

## 6. Application and resource inventory

`/usr/bin/hiby_player` is a 4,855,400-byte stripped, dynamically linked,
non-PIE MIPS32r2 ELF. It links ALSA, curl, OpenSSL 1.1, zlib, QR encoding,
pthread, and glibc among other libraries.

The loose `/usr/resource` tree contains 2,988 files:

| Kind | Count |
|---|---:|
| PNG raster assets | 1,845 |
| INI files | 613 |
| `.dlg` layouts | 219 |
| `.view` layouts | 201 |
| Text files | 45 |
| JSON files | 24 |
| `.listview` files | 12 |
| Binary tables | 12 |
| Font files | 4 |

The complete firmware has 1,848 loose PNG/JPEG raster files: 1,845 PNG UI
assets plus the three boot JPEGs. These are all externally discoverable raster
assets, but they are not necessarily every visual element: text, primitives,
runtime album art, and images embedded inside proprietary binaries are not
"sprites" in this count. `export-assets` produces a full copy and a manifest
with size, geometry, target for symlinks, and SHA-256.

| Path | Content | Initial risk |
|---|---|---|
| `/etc/logo*.jpeg` | Boot splash | low |
| `/usr/resource/layout/` | Theme view/layout descriptions | low-medium |
| `/usr/resource/litegui/` | 1,845 PNG UI assets | low-medium |
| `/usr/resource/str/` | Translations | low |
| `/usr/resource/fonts/` | Fonts | medium due to size/RAM |
| `/usr/resource/config.json` | Product, display, and limits | medium |
| `/usr/resource/set_functions.json` | Visible/hidden features | medium |
| `/usr/bin/hiby_player.sh` | Launcher and sidecars | medium |
| `hiby_player` / preload libraries | Proprietary code and hooking | high |
| Kernel, modules, MTD, bootloader | Platform and boot path | very high |

`set_functions.json` hides `usb_mode`, `dac_charge_disable`, `dac_feedback`,
`car_mode`, `standby`, `double_touch_wakeup`, and `about`, among others. Changing
a boolean can expose a UI whose backend is incomplete; enable and test one item
at a time.

## 7. A/B update behavior

Readable scripts in `/etc/ota_bin/` implement this flow:

```text
read ota_update.in
  → select the inactive kernel/rootfs pair
  → validate each chunk and MD5
  → flash_erase + nandwrite the inactive images
  → only after success, write the new ota:kernel[2] marker
```

The marker occupies a 256-byte MTD area. If it says `ota:kernel2`, the updater
writes pair 1; otherwise it writes pair 2. This reduces interruption risk but
does not prove automatic rollback when a fully written image fails to boot.

The exact physical MTD numbers, sizes, and kernel arguments still need a device
capture:

```sh
cat /proc/mtd
cat /proc/cmdline
mount
dmesg
```

The diagnostic overlay's `cfw-info.sh` saves these values to the SD card.

## 8. SSH implementation

Dropbear 2026.94 is built as a 384 KiB stripped PIE using Zig's pinned glibc 2.22
target and the stock `libcrypt-2.22.so` at link time. The output is MIPS32r2,
o32, hard-float and requires only `libcrypt.so.1`, `libc.so.6`, and
`libutil.so.1`; its newest referenced glibc symbol version is `GLIBC_2.19`.

`S91dropbear` starts a five-second monitor after writable UBIFS is mounted. The
monitor starts Dropbear only when Developer Mode is enabled and `wlan0` owns an
IPv4 address. It binds to that exact address on TCP 2222, follows DHCP changes,
and stops immediately after the marker appears or Wi-Fi goes down.

Persistent state is `/usr/data/dropbear/`: an ED25519 host key, a bounded log,
and optional `authorized_keys`. The rootfs has
`/root/.ssh/authorized_keys -> /usr/data/dropbear/authorized_keys`; this avoids
Dropbear rejecting stock `/usr` mode 0775 as an authorized-key parent.

Forwarding, agent forwarding, X11, DSS, RSA/SHA-1, 3DES, and CBC are disabled.
The lab root password is `hibyr1`, stored as SHA-512 crypt. This is not suitable
for an exposed or untrusted network.

## 9. QEMU scope and results

QEMU has no HiBy R1/Ingenic X1600 machine model. A full boot would require models
for the SoC interrupt/timer blocks, NAND/UBI, display, touch, PMIC, audio, Wi-Fi,
and the real bootloader environment. The Malta board is not hardware-compatible.

QEMU user mode is still valuable. `test-dropbear-qemu.sh` uses the stock rootfs,
QEMU MIPS, and PRoot to validate:

1. ELF interpreter and ABI startup.
2. Guest ED25519 host-key generation.
3. SSH key exchange and public-key login.
4. SSH password login using the R1 shadow file and guest `libcrypt`.
5. Execution of the R1 `/bin/sh` as UID 0.

The normal Dropbear listener forks and exposes a PRoot descriptor limitation, so
the harness uses Dropbear's inetd mode behind a loopback-only `socat` listener.
This is an emulator harness detail; the hardware service uses normal daemon mode.

## 10. Related open projects

- [hiby_os_crack](https://github.com/hiby-modding/hiby_os_crack): baseline `.upt`
  research, pack/unpack scripts, and X1600E material. Its QEMU work is explicitly
  incomplete; included generic Ingenic burner U-Boot binaries are not proof of
  the exact installed bootloader.
- [Hiby-R1-Mod](https://github.com/bidhata/Hiby-R1-Mod): practical resource mods,
  a Game Boy launcher, and return-to-stock-player patterns.
- [Meowby-R1](https://github.com/hiby-modding/Meowby-R1): useful overlay and Nix
  reproducibility patterns.
- [Hiby-R1-Audiobook-Mod](https://github.com/yetisoldier/Hiby-R1-Audiobook-Mod):
  native MIPS functionality through `LD_PRELOAD`, framebuffer, touch, and player
  integration.
- [hiby-r1-rockbox-bt](https://github.com/bidhata/hiby-r1-rockbox-bt): alternative
  Rockbox userland on the stock Linux system with experimental Bluetooth.
- [Ingenic-community/linux](https://github.com/Ingenic-community/linux): useful
  community kernel material for comparing Ingenic platform drivers.

## 11. Proven and unproven boundaries

Host-verified:

- OTA/ISO format and exact chunk algorithm;
- manifests, MD5 chains, and uImage CRCs;
- SquashFS extraction and reconstruction;
- architecture, SoC evidence, drivers, and init order;
- A/B update scripts;
- asset inventory and modification points;
- Dropbear ABI, key generation, key/password SSH sessions under QEMU;
- final CFW re-extraction and exact rootfs change allowlist.

Still requires physical hardware:

1. Capture MTD, cmdline, mounts, modules, and dmesg.
2. Confirm stock recovery and update behavior.
3. Boot-test the smallest reversible image while keeping a stock SD card ready.
4. Verify Developer Mode marker transitions and Wi-Fi binding on-device.
5. Only then investigate deeper hooks, kernel changes, or bootloader work.
