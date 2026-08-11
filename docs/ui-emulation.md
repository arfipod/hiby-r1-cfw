# HiBy R1 UI preview and qemu-user interaction lab

## Scope

There are two complementary UI tools in this repository:

1. `r1ui.py` parses the stock LiteGUI layouts and renders their real external
   assets. It is fast, deterministic, and suitable for CI, but it does not run
   the proprietary application logic.
2. `run-r1-ui-qemu.sh` runs the stock `hiby_player` MIPS executable under
   QEMU user mode with emulator-only display, DMA, input, and IPC adapters. It
   exercises the real application and is the behavioral test lab.

Neither tool is a full Ingenic X1600 virtual machine. Upstream QEMU has no
X1600/Halley6 board model, and a Malta machine does not provide the R1 clock,
interrupt, timer, NAND, LCD, touch, audio, PMIC, or wireless peripherals.

## Static stock-resource preview

The vendor layout files resemble JSON but frequently repeat keys such as
`imageview`, `textview`, and `viewgroup`. Repeated keys represent sibling
widgets, so a conventional JSON parser would silently discard most of the UI.
`r1ui.py` preserves every ordered key/value pair and tolerates only the known
surplus closing brace present in three stock dialog files.

Validate the complete resource tree:

```bash
python3 tools/r1ui.py lint work/r1-1.6/rootfs
```

Render one layout with its stock images, translations, and inferred touch
rectangles:

```bash
python3 tools/r1ui.py render work/r1-1.6/rootfs \
  theme1/hiby_net_settings.view \
  --output work/gui-preview/net-settings.png \
  --metadata work/gui-preview/net-settings.json \
  --touch-overlay
```

Open the interactive browser inspector:

```bash
python3 tools/r1ui.py serve work/r1-1.6/rootfs --port 8765
```

The preview resolves images inside `/usr/resource/litegui`, UTF-16LE stock
translations, colors including `0xAARRGGBB`, nested coordinates, HGL viewport
offsets, hidden/runtime-controlled nodes, and scenario overrides. It can also
quantize a render to RGB565 precision with `--rgb565`.

Static previews are appropriate for splash/resource work, layout geometry,
theme comparisons, translation changes, and CI regressions. They cannot prove
runtime navigation, animation timing, database behavior, audio, networking, or
hardware-service integration.

## Running the real proprietary UI

Bootstrap the pinned local QEMU/PRoot/Zig tools once, then start the player:

```bash
tools/bootstrap-ssh-toolchain.sh
tools/run-r1-ui-qemu.sh
```

The runner starts from the extracted stock SquashFS, keeps writable state under
`work/gui-qemu/runtime`, and acquires an exclusive lock for that directory.
Use a different runtime for an intentional parallel session:

```bash
R1_QEMU_UI_RUNTIME=/tmp/r1-ui-second tools/run-r1-ui-qemu.sh
```

`/usr/data/user.ini` is not text; it is a 2,768-byte firmware structure. Before
launch, the runner uses `userdata.py` to make a separate prepared copy, stamps
the firmware 1.6 schema, selects English, clears the language-onboarding flag,
and disables idle/sleep shutdown. Every other byte is preserved. The same tool
can inspect or prepare an explicit disposable profile:

```bash
python3 tools/r1-qemu-ui/userdata.py inspect work/gui-qemu/runtime/usr-data/user.ini
python3 tools/r1-qemu-ui/userdata.py prepare STOCK_USER_INI OUTPUT_USER_INI
```

In another terminal, start the local browser bridge:

```bash
python3 tools/r1-qemu-ui/bridge.py serve \
  work/gui-qemu/runtime/framebuffer.raw \
  work/gui-qemu/runtime/touch/event0 \
  --state work/gui-qemu/runtime/frame-state.bin
```

Open `http://127.0.0.1:8766/`. The displayed 480×800 frame comes from the real
stock renderer, and mouse/pointer gestures are encoded as the R1's 16-byte
MIPS-o32 evdev records.

The bridge also supports reproducible screenshots and individual taps:

```bash
python3 tools/r1-qemu-ui/bridge.py capture \
  work/gui-qemu/runtime/framebuffer.raw work/gui-preview/current.png \
  --state work/gui-qemu/runtime/frame-state.bin

python3 tools/r1-qemu-ui/bridge.py touch \
  work/gui-qemu/runtime/touch/event0 240 397 --phase tap
```

Review the curated, non-destructive navigation plan without touching a live
process:

```bash
python3 tools/r1-qemu-ui/navigate.py smoke --dry-run \
  --artifacts-root artifacts/ui --run-id review-plan
```

Run that plan while the emulator is at the six-tile launcher:

```bash
python3 tools/r1-qemu-ui/navigate.py smoke \
  --artifacts-root artifacts/ui
```

The navigator recognizes the launcher before sending input, requires a content
frame change after each action, and records immutable PNG frames, JSONL input
events, `manifest.json`, and `coverage.json`. It covers each launcher branch,
the isolated Explorer/MP3 route, and the first five read-only System pages. It
never selects restore, firmware/database update, delete/remove, SD format, or
factory actions. An unknown dialog is captured and stops the run rather than
guessing which button is safe.

The default run creates a private network namespace. Setting
`R1_QEMU_ALLOW_NETWORK=1` deliberately removes that isolation and should only
be used for a specific networking experiment.

## Emulator adapter design

The MIPS `LD_PRELOAD` shim is compiled only for the host-side lab. It must not
be included in a custom firmware image. It supplies these compatibility layers:

- `/dev/fb0`: 480×800, 480×1600 virtual height, 32-bit fbdev geometry,
  1,920-byte stride, and two 3,072,000-byte pages in total;
- `/dev/sa_hgl_dma`: the stock 6 MiB HGL arena and its exact 16-byte row-copy
  descriptor, copied into the fake framebuffer;
- `FBIOPAN_DISPLAY`, `FBIOBLANK`, and the stock rotation ioctl;
- one private FIFO for every touchscreen open, preserving evdev broadcast
  behavior rather than splitting a gesture between readers;
- the `hyn_ts` capability/name ioctls and 16-byte MIPS-o32 `input_event` ABI;
- a narrow SIGBUS compatibility handler for the unaligned MIPS FR=0
  `LDC1`/`SDC1` instructions that the R1 kernel fixes up but qemu-user forwards
  as `BUS_ADRALN`;
- a constrained Unix-socket `sys_server` stub that acknowledges only the
  player's known MMC mount/unmount messages and never invokes `mount` or a
  subprocess.

The player also runs stock hardware setup scripts. Missing mixer, USB gadget,
sysfs, and Wi-Fi messages are expected in the application-level harness and do
not indicate that the display adapter failed.

## Simulated microSD and test music

If `test-audio.mp3` exists at the repository root, the runner preserves the
source and copies it into the disposable guest SD tree as:

```text
/data/mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3
```

The runner exposes an emulator-only `/dev/mmcblk0p1` sentinel. The real player
then sends its normal request:

```text
MOUNT:MOUNT:/dev/mmcblk0p1 /data/mnt/sd_0
```

The safe stub acknowledges that request because the host directory is already
bound at the requested guest path. No privileged host mount is performed.

The stock player does not automatically scan a newly presented card in this
harness. Use Music → Explorer to inspect the file directly, or start the normal
music scan from the UI before testing the database-based All/Album/Artist
views. The host currently has no R1 tinyalsa device, so metadata, navigation,
selection, and UI state can be tested before claiming real DAC playback.

## Diagnostics and safety boundaries

Disposable diagnostics live beside the runtime:

| File | Purpose |
|---|---|
| `framebuffer.raw` | double-buffer framebuffer backing |
| `frame-state.bin` | active page and frame sequence |
| `hgl-dma.raw` | 6 MiB stock-renderer arena |
| `input-ioctl.bin` | input discovery requests |
| `input-events.bin` | events actually read by the player |
| `crash-state.bin` | compact MIPS fault context |
| `sys-server.log` | validated mount IPC requests |

The runner never modifies `r1.upt`, the extracted stock analysis tree, or the
source MP3. Firmware images, runtime trees, screenshots, and test audio remain
outside version control. Hardware-dependent pages may be inspected, but tests
must not report Wi-Fi, Bluetooth, USB, charging, DAC output, or firmware-update
behavior as validated without a physical R1.

## Related implementation evidence

- [QEMU MIPS system emulator targets](https://www.qemu.org/docs/master/system/target-mips.html)
- [QEMU user-mode documentation](https://www.qemu.org/docs/master/user/main.html)
- [`hiby_os_crack` R1 QEMU notes](https://github.com/hiby-modding/hiby_os_crack/blob/main/r1/qemu/README.md)
- [`hiby_os_crack` board-emulation guide](https://github.com/hiby-modding/hiby_os_crack/blob/main/docs/qemu-board-emulation-guide.md)
- [Linux/MIPS unaligned-access handler](https://github.com/torvalds/linux/blob/master/arch/mips/kernel/unaligned.c)
- [HiBy R1 framebuffer hook architecture](https://github.com/yetisoldier/Hiby-R1-Audiobook-Mod/blob/main/docs/modding/hook_architecture.md)
- [HiBy R1 evdev touch injector](https://github.com/yetisoldier/Hiby-R1-Audiobook-Mod/blob/main/tools/adb_inject_touch_event.py)
