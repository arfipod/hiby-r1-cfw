# Codex handoff: integrate Retro Handheld as a third theme

The deterministic resource generator is complete. Do not redesign the theme
from scratch and do not commit generated vendor-derived resources.

## Inputs

The user has a local extraction shaped as:

```text
hiby-r1-themes/
├── light/
│   ├── layout/
│   └── litegui/
├── dark/
│   ├── layout/
│   └── litegui/
└── shared/
```

Generate the third theme with:

```bash
python3 tools/build_retro_theme.py \
  /path/to/hiby-r1-themes \
  work/retro-handheld-theme \
  --zip work/retro-handheld-theme.zip
```

The resulting importable source tree is:

```text
work/retro-handheld-theme/retro/
├── layout/
└── litegui/
```

## Integration task

1. Add `stock-light`, `stock-dark`, and `retro-handheld` to the current CFW
   appearance/theme state without changing unrelated launcher state.
2. Persist the selected theme atomically below `/usr/data/r1-cfw`.
3. Validate a complete Retro layout/litegui tree before applying any mount.
4. Apply all theme mounts as one logical transaction; unwind every Retro mount
   on any partial failure.
5. Fall back to the currently selected stock theme for missing, malformed, or
   unsupported state.
6. Keep the proprietary `hiby_player`, kernel, recovery, and bootloader unchanged.
7. Reuse the generated Retro `launcher/cfw*.png` and tile assets when building
   every supported launcher mask.
8. Preserve all callback names, touch rectangles, scrolling behavior, and stock
   Wireless routes.
9. Add an Appearance page to the independent CFW sidecar. It may adopt the Retro
   renderer immediately, while stock-player resources may require a normal
   player/userland restart.
10. Extend QEMU/static validation to cover Stock -> Retro -> Stock and a forced
    incomplete-theme fallback.

## Acceptance checks

- every source file from the stock light theme remains present in the generated
  Retro tree;
- every inherited PNG preserves its original geometry;
- generated ZIP output is byte-reproducible;
- no generated theme tree, stock font, firmware image, or extracted proprietary
  resource is committed;
- existing SSH, launcher visibility, Wi-Fi/Bluetooth routes, build evidence,
  rootfs size limit, and release gates remain intact;
- no automated step flashes a physical device.

The shared stock font intentionally remains unchanged. It is global and
multilingual rather than theme-local. Do not replace it merely to obtain a
pixel-font appearance inside proprietary stock screens.
