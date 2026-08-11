# Retro Handheld theme

## Goal

Retro Handheld is a reversible third visual theme for the HiBy R1. It combines
portable-console, PDA, homebrew, and early-2000s embedded-UI cues with modern
legibility. It is not a firmware image and it does not modify the kernel,
recovery system, bootloader, MTD layout, or updater.

## Generator

`tools/build_retro_theme.py` takes a locally extracted stock light-theme tree and
creates a complete `retro/` resource tree. The build is deterministic: identical
inputs produce a byte-identical ZIP.

```bash
python3 tools/build_retro_theme.py \
  /path/to/hiby-r1-themes \
  work/retro-handheld-theme \
  --zip work/retro-handheld-theme.zip
```

The source root must contain `light/layout` and `light/litegui`. The generator:

1. copies the complete light-theme structure;
2. maps stock cold-blue colors to the Retro Handheld token palette;
3. creates original launcher cards and pixel-style launcher/CFW icons;
4. injects a dotted charcoal top-bar background;
5. rebuilds common cards, toggles, slider tracks, player controls, and navigation
   affordances;
6. preserves QR codes, certificates, boot animation, and screensavers;
7. verifies that every stock file remains present and every PNG keeps its original
   geometry;
8. writes hashes, previews, and a deterministic ZIP.

## Expected output

```text
retro-handheld-theme/
├── retro/
│   ├── layout/
│   └── litegui/
├── previews/
├── manifest.json
├── palette.json
└── README.md
```

The `retro/` subtree is suitable as the input to a future theme installer or
bind-mount selector. It also includes `launcher/cfw.png`, `cfw_s.png`, and CFW
card assets for integration with the existing generated launcher.

## Repository integration

The safe integration model should be:

```text
user-selected theme state in /usr/data/r1-cfw
  -> validate complete generated Retro resource set
  -> bind-mount Retro layout/litegui paths before hiby_player starts
  -> remove all Retro binds on any partial failure
  -> expose the stock selected theme as the fallback
```

Do not copy a partial set. Theme application should be atomic from the player's
point of view and should require only a normal player/userland restart.

The launcher generator should consume the original `cfw` and `tile_cfw` assets
from the generated Retro pack when producing Retro launcher masks. Existing tile
names, callback mappings, touch rectangles, and mask rules must remain unchanged.

## Known limitation

The stock UI font remains the vendor font. The shared font directory is global,
contains multilingual coverage, and is not safely replaceable as a theme-local
asset. This limitation does not apply to the independent CFW sidecar, which can
use its own embedded bitmap font.

## Validation boundary

Host validation can prove deterministic output, resource completeness, PNG
geometry, static layout parsing, QEMU rendering, and safe fallback behavior. It
cannot prove physical LCD color response, touch feel, DAC, Wi-Fi, Bluetooth,
NAND, recovery, or updater behavior. Building this theme must never flash a
physical player.
