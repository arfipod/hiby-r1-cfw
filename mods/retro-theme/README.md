# Retro Handheld theme source

This directory defines the design tokens and integration boundary for the
**Retro Handheld** HiBy R1 theme.

The generated theme is deliberately absent from Git. It is derived from the
proprietary stock `light` resource tree and must be produced locally with:

```bash
python3 tools/build_retro_theme.py \
  work/extracted-themes \
  work/retro-handheld-theme \
  --zip work/retro-handheld-theme.zip
```

The input directory must contain:

```text
work/extracted-themes/
├── light/
│   ├── layout/
│   └── litegui/
└── dark/                 # optional reference; not required by the generator
```

The output contains a `retro/` directory with the same `layout/` and `litegui/`
shape as the stock theme, plus a manifest, palette, and deterministic preview
images.

## Visual language

- warm off-white canvas and cards;
- near-black text and original pixel-style icons;
- dark dotted status bar;
- mint for enabled/primary states;
- cyan for navigation and sliders;
- yellow for edit/configuration;
- coral for warnings/destructive actions;
- violet for special/about states;
- flat controls, thin borders, modest corner radii, and almost no shadow.

## Safety and licensing boundary

Do not commit generated theme resources. The generator copies and transforms
assets from a user-provided firmware extraction. The repository should only
contain the deterministic transformation code and original generated primitives.

The stock shared font is intentionally not replaced. It is global and
multilingual rather than theme-local. The CFW sidecar can continue to use its
embedded bitmap renderer while the stock UI receives the retro palette, cards,
icons, toggles, sliders, controls, status bar, and launcher treatment.
