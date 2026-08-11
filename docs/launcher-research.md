# HiBy R1 1.6 launcher integration

This note documents only the launcher path needed by CFW 0.1. Addresses are
virtual addresses in the exact stock `hiby_player` whose SHA-256 is
`8398e1e1295e83b033bf7b8c39932fff3f620831f5a91682869554047b26f6b2`.

## 1. Stock launcher architecture

The launcher is hybrid. LiteGUI resources define each visible group, its
geometry, icon, text, and therefore its touch area. A fixed descriptor table in
`hiby_player` maps the group name to a click callback. Removing a group from the
layout removes both its pixels and its actionable object; changing only the
resource cannot assign a new native action to an unknown group.

## 2. Exact launcher resources

The three equivalent layouts are:

```text
/usr/resource/layout/theme1/launcher/hiby_launcher_apps.view
/usr/resource/layout/theme2/launcher/hiby_launcher_apps.view
/usr/resource/layout/midi/theme1/launcher/hiby_launcher_apps.view
```

Icons are below each active LiteGUI theme's `launcher/` directory. Labels are
UTF-16LE entries in `/usr/resource/str/<language>/settings.ini`, `sys_set.ini`,
and `book.ini`. CFW adds only the `cfw` key to `launcher.ini`.

The outer `vg_launcher_apps_hiby` is a 480x750 HGL viewport displayed at screen
Y=50. Child `lg_view` rectangles are the tile hit targets.

## 3. Tile to widget to action mapping

| Tile | Group | Label key | Icon | Stock content rectangle | Callback | Proven destination |
|---|---|---|---|---|---|---|
| Music | `launcher_apps_vg_player` | `settings.ini:music` | `music` | 0,0,240,246 | `0x53b120` | Music category |
| Stream | `launcher_apps_vg_stream_media` | `sys_set.ini:stream_media` | `stream_media` | 240,0,240,246 | `0x53c300` | Stream media |
| Wireless | `launcher_apps_vg_wireless` | `settings.ini:net_set` | `wireless` | 0,246,240,246 | `0x53ae60` | Wireless hub |
| eBook | `launcher_apps_vg_ebook` | `book.ini:ebook` | `book` | 240,246,240,246 | `0x53bb20` | Books list |
| System | `launcher_apps_vg_sysset` | `settings.ini:sys_set` | `sys_set` | 0,492,240,268 | `0x53bbe0` | System settings |
| About | `launcher_apps_vg_about` | `settings.ini:about` | `about` | 240,492,240,268 | `0x53bc20` | About device |
| CFW | `launcher_apps_vg_step` | `launcher.ini:cfw` | `cfw` | generated | stock no-op `0x53bbc0` | CFW sidecar hook |

The descriptor table starts at `0x891c28`, contains 31 records of 0x60 bytes,
stores the inline group name at offset 0 and the click callback at offset 0x48.
The CFW reuses the dormant Step descriptor at `0x892048`; its writable callback
word is `0x892090`. The exact stock value is the two-instruction no-op at
`0x53bbc0` (`jr ra; move v0,zero`).

## 4. Resource-only capabilities

Copied resource trees proved that Stream, Wireless, and eBook can be omitted;
the remaining groups can be moved and resized; strings and icons can be
replaced; and the `lg_view` bounds move the actionable area with the visible
tile. `tools/r1ui.py` parses and renders every generated variant. A resource-only
change cannot turn the dormant Step descriptor into a CFW action, so one small
runtime routing hook is still required.

## 5. Launcher routing

The integration hook verifies the mapped descriptor name, writable callback
slot, and executable no-op instructions before replacing only the Step callback
pointer in process memory. It does not rewrite the proprietary executable on
disk. Unknown binaries or changed preimages are left untouched.

Music, System, and About keep their stock callbacks. CFW Wi-Fi and Bluetooth
requests return from the sidecar with a route code and then invoke the proven
stock Wireless-hub callback `0x53ae60`. Direct radio-page hooks were deliberately
avoided.

## 6. Launcher visibility architecture

`tools/patch_r1_launcher.py` generates all 41 safe masks in all three themes.
The mask bits are:

```text
01 Music    02 Stream    04 Wireless    08 eBook
10 System   20 CFW       40 About
```

A safe mask contains four through six tiles and always includes CFW. The
default `0x71` shows Music, System, CFW, and About. Every optional stock tile is
individually restorable after disabling another tile. Attempting to enable a
seventh tile leaves the configuration unchanged and reports `Maximum 6 launcher
tiles. Disable one first.` Persistent state is the single recoverable line
`/usr/data/r1-cfw/launcher.conf` (stored without an `0x` prefix):

```text
launcher_mask=71
```

At boot, `S90r1-cfw` validates the value and bind-mounts the corresponding
read-only, build-generated layout before `hiby_player` starts. Missing,
invalid, or legacy `7f` state falls back to `71`; the boot selector does not
rewrite persistent state. The sidecar canonicalizes invalid state when it is
next opened. Any bind failure leaves the stock layout. Launcher changes take
effect on the next userland restart because the stock player caches the parsed
launcher object tree.

## 7. Bounded launcher geometry

Four tiles use large custom rectangles within the 750-pixel viewport. Five and
six tiles use 246-pixel two-column rows and a 738-pixel content surface. Every
generated view keeps the tiles directly below `vg_launcher_apps_hiby`, uses the
stock-compatible `flag=scroll` click behavior, and fixes both vertical scroll
bounds at screen Y=50. No v0.1 layout exceeds the viewport, so reopening the
player always starts at the same fixed position.

A seven-tile scrolling layout was investigated but did not provide reliable
stock HGL drag/click dispatch. With the user's approval, v0.1 rejects `0x7f`
instead of shipping an unstable gesture path. Proper all-seven scrolling is an
explicit v0.2 follow-up.

## 8. CFW UI architecture

CFW 0.1 uses a hybrid design: generated external launcher resources, one guarded
in-memory callback-pointer hook, and the independent `/usr/bin/r1-cfw-ui` MIPS
sidecar. Business logic and rendering remain outside proprietary code.

## 9. Wi-Fi stock UI entry point

The sidecar returns the Wi-Fi route code to the launcher callback. The hook then
calls the original Wireless callback `0x53ae60`, opening
`hiby_wireless`/`vg_wireless_hiby`. The complete stock hub remains available
even when its launcher tile is hidden.

## 10. Bluetooth stock UI entry point

Bluetooth uses the same proven Wireless-hub gateway. This preserves stock
scanning, pairing, saved devices, and audio routing without reproducing them in
CFW code. Radio operation remains a hardware-only test.

## 11. Sidecar framebuffer and input lifecycle

The launcher callback runs synchronously. It identifies the `hyn_ts` evdev
device, transfers the existing exclusive grab, snapshots both 480x800
framebuffer pages and the active Y offset, and execs the sidecar with inherited
descriptors. The blocked player cannot consume the same touch stream. On normal
Back, route exit, exec failure, or child signal, the parent restores both pages,
restores the original pan, returns the touch grab, and resumes the stock
callback. The preload is optional and fail-open at boot.

## 12. QEMU results

The repository's qemu-user shim supplies the exact 480x800 double framebuffer,
HGL DMA copies, and 16-byte MIPS input events. Static layout parsing covers all
123 generated views. Candidate validation independently extracts and runs the
hashed SquashFS, then hashes it again before PASS. It writes its report and
screenshots below ignored `artifacts/ui/cfw-v0.1/`; publication stays blocked
until the complete 24-check PASS manifest and its verified 480x800 PNG evidence
match the exact candidate rootfs.

The launcher matrix covers the default `71` mask, six-tile `77`, rejection of a
seventh tile without a state change, drag without activation, every visible
six-tile route, an eBook-restoring swap to alternate six-tile `7d`, persistence,
and deterministic restart position. The mandatory matrix also forces one
sidecar `SIGABRT` and requires the player, restored framebuffer, and a
subsequent Music touch route to remain functional. QEMU does not emulate the
X1600 board, Wi-Fi radio, Bluetooth controller, DAC, NAND, or recovery updater.

## 13. Build validation

The v0.1 build verifies the complete stock UPT SHA-256 before unpacking, builds
in a disposable tree, asserts the 45 MiB main-rootfs limit, re-extracts the
candidate UPT, compares `xImage` byte-for-byte, reruns every patch/resource
verifier, and enforces an exact added/changed/removed rootfs allowlist. A pinned
source epoch plus UTC ISO/Rock Ridge timestamp normalization makes identical
inputs produce a byte-identical candidate. A candidate-specific QEMU PASS
manifest is required before atomic publication to `dist/`.

## 14. Remaining hardware-only risks

QEMU cannot prove physical framebuffer cache behavior, touch-grab transfer on
the installed kernel, radio configuration, power-management interactions, or
the recovery flash operation. The sidecar and launcher integration are therefore
experimental even when all offline tests pass.

## 15. Physical test procedure

This is a future manual hardware plan. The repository build/QEMU workflow stops
after producing a local file and does not flash a player or write an MTD device.

1. Keep the exact stock 1.6 `r1.upt` and confirm the recovery/update procedure.
2. Fully charge the R1 and copy the experimental image to a separate FAT32 card
   as `r1.upt`.
3. Install through the stock recovery UI without interrupting power.
4. Confirm Music, System, CFW, and About before changing launcher visibility.
5. Open and close CFW three times; verify touch and the stock framebuffer return.
6. Test SSH off/on, then Wi-Fi and Bluetooth navigation through CFW.
7. Restore Stream, Wireless, and eBook individually while keeping at most six
   tiles visible; restart userland and verify every resulting tile and Back.
8. If boot or input fails, use the already-proven stock recovery path; never
   write MTD devices manually.
