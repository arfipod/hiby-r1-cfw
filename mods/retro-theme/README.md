# Retro Handheld theme source

This directory documents the design and licensing boundary for the integrated
**Retro Handheld** HiBy R1 theme.

Generated theme resources are deliberately absent from Git because they are
derived from the proprietary stock Light resource tree. The repository contains
only deterministic transformation code, original pixel-style icons/components,
the reversible runtime selector, and tests.

## Standalone package

```bash
python3 tools/build_retro_theme.py \
  work/extracted-themes \
  work/retro-handheld-theme \
  --zip work/retro-handheld-theme.zip
```

The input must contain `light/{layout,litegui}` and
`dark/{layout,litegui}`. The output contains the importable `retro/` resource
tree, deterministic previews, palette, hashes, a theme marker, and both narrow
and wide launcher cards.

## Firmware integration

`tools/build-cfw-0.1.sh` generates Retro locally from the exact stock firmware,
installs it below `/usr/resource/r1-cfw/themes/retro`, generates all launcher
masks, and independently regenerates and compares the result after candidate
re-extraction. `S90r1-cfw` applies Stock, Light, Dark, or Retro as a single
fail-open bind-mount transaction.

The CFW sidecar stores the canonical selection in
`/usr/data/r1-cfw/theme.conf` and exposes it through About → Appearance.

## Visual language

- warm off-white canvas and cards;
- near-black text and original pixel-style icons;
- dark dotted status bar;
- mint for enabled/primary states;
- cyan for navigation and sliders;
- yellow for editing/configuration;
- coral for warnings/destructive actions;
- violet for special/about states;
- flat controls, thin borders, modest corner radii, and almost no shadow.

The stock shared font is intentionally retained because it is global and
multilingual. The independent CFW sidecar uses its own embedded bitmap renderer.
No theme build or test flashes hardware.
