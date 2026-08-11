# Retro Handheld theme

## Overview

Retro Handheld is a reversible third visual theme for the HiBy R1 CFW. It
combines portable-console, PDA, homebrew, and early-2000s embedded-UI cues with
modern legibility. The repository stores only deterministic transformation code
and original primitives; generated vendor-derived resources remain local.

The CFW Appearance page exposes four choices:

- **System default**: preserve the stock firmware's own Light/Dark selection;
- **Light**: force the stock light resources;
- **Dark**: force the stock dark resources;
- **Retro Handheld**: activate the generated Retro resource tree.

The canonical persistent state is:

```text
/usr/data/r1-cfw/theme.conf
```

with exactly one of:

```text
theme=stock
theme=light
theme=dark
theme=retro
```

State is written atomically. Missing or malformed state falls back to `stock`.
The independent CFW sidecar previews Retro immediately; stock-player resources
apply after the next normal player/userland restart.

## Generation

For a standalone importable package, provide locally extracted themes shaped as:

```text
hiby-r1-themes/
├── light/
│   ├── layout/
│   └── litegui/
└── dark/
    ├── layout/
    └── litegui/
```

Then run:

```bash
python3 tools/build_retro_theme.py \
  /path/to/hiby-r1-themes \
  work/retro-handheld-theme \
  --zip work/retro-handheld-theme.zip
```

The firmware release pipeline performs the same generation directly from the
independently extracted stock 1.6 rootfs. It installs only the resulting
`retro/layout` and `retro/litegui` tree below:

```text
/usr/resource/r1-cfw/themes/retro
```

It then generates all 41 supported Retro launcher masks under:

```text
/usr/resource/r1-cfw/launcher/retro
```

## Runtime application

`S90r1-cfw` applies theme and launcher mounts as one fail-open transaction before
`hiby_player` starts. It validates every required source and selected launcher
variant before mounting anything. A partial mount failure unwinds all launcher
file mounts first and all parent theme directory mounts second, exposing the
stock SquashFS resources.

Light and Dark reuse the stock resource trees. Retro is mounted over both stock
theme slots, so it remains active regardless of the vendor theme index. MIDI
keeps its stock resource tree and its independently generated launcher variant.

## Validation

The build pipeline:

1. regenerates Retro from the patched stock resource tree;
2. installs the complete generated tree;
3. generates every safe Retro launcher mask;
4. packs and independently re-extracts the candidate SquashFS;
5. regenerates Retro again from the independently extracted candidate stock
   resources;
6. byte-compares all files, modes, directories, and symlinks;
7. verifies every Retro launcher variant and original asset geometry;
8. includes every generated path in the strict rootfs allowlist;
9. enforces the existing main-rootfs partition ceiling and release evidence
   gate.

Unit tests also cover canonical theme persistence, malformed-state recovery,
Appearance navigation, immediate Retro sidecar rendering, stock/light/dark/retro
mount plans, incomplete-theme fallback, wide/narrow launcher cards, and release
pipeline wiring.

## Known limitation

The shared vendor font remains unchanged. It is global and multilingual rather
than theme-local. Replacing it only for Retro would require a riskier global font
change. The independent CFW sidecar continues to use its embedded bitmap font.

## Safety boundary

Theme generation and validation never flash a physical player, write MTD,
modify the kernel, recovery system, or bootloader, or invoke the device updater.
QEMU/static validation does not prove physical LCD response, touch feel, DAC,
Wi-Fi, Bluetooth, NAND, or recovery behavior.
