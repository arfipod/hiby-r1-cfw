"""Packaging, previews, validation, and deterministic ZIP output."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from retro_theme_common import (
    PALETTE,
    TEXT_SUFFIXES,
    ValidationResult,
    copy_tree,
    hex_rgb,
    make_dotted_header,
    rounded_asset,
    sha256_file,
    sha256_tree,
)
from retro_theme_assets import (
    create_component_assets,
    create_launcher_assets,
    create_navigation_assets,
    create_player_assets,
    create_topbar_assets,
    make_toggle,
    patch_retro_files,
    recolor_all_pngs,
)

def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def create_previews(package_root: Path, retro: Path) -> None:
    preview = package_root / "previews"
    preview.mkdir(parents=True, exist_ok=True)
    font_small = _font(18)
    font_medium = _font(24)
    font_large = _font(30)

    # Launcher preview using generated assets.
    canvas = Image.new("RGB", (480, 800), hex_rgb(PALETTE["canvas"]))
    canvas.paste(make_dotted_header((480, 50)).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 14), "VOL 27", font=font_small, fill=PALETTE["surface_bright"])
    draw.text((205, 14), "12:00", font=font_small, fill=PALETTE["surface_bright"])
    draw.text((390, 14), "56%", font=font_small, fill=PALETTE["surface_bright"])
    specs = [
        ("music", "MUSIC", (8, 58), (464, 220)),
        ("sys_set", "SYSTEM", (8, 286), (228, 220)),
        ("cfw", "CFW", (244, 286), (228, 220)),
        ("about", "ABOUT", (8, 514), (464, 220)),
    ]
    for name, label, (x, y), (w, h) in specs:
        fill = {
            "music": PALETTE["mint_soft"], "sys_set": PALETTE["surface"],
            "cfw": PALETTE["cyan_soft"], "about": PALETTE["violet_soft"],
        }[name]
        draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=12, fill=fill, outline=PALETTE["border"], width=2)
        icon_name = name if name != "sys_set" else "sys_set"
        icon_path = retro / "litegui" / "launcher" / f"{icon_name}.png"
        icon = Image.open(icon_path).convert("RGBA")
        icon.thumbnail((110, 110), Image.Resampling.NEAREST)
        canvas.paste(icon, (x + (w - icon.width) // 2, y + 26), icon)
        bbox = draw.textbbox((0, 0), label, font=font_large)
        draw.text((x + (w - (bbox[2] - bbox[0])) // 2, y + h - 62), label, font=font_large, fill=PALETTE["ink"])
    canvas.save(preview / "launcher.png", optimize=True)

    # Settings/list preview.
    canvas = Image.new("RGB", (480, 800), hex_rgb(PALETTE["canvas"]))
    canvas.paste(make_dotted_header((480, 50)).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 15), "27", font=font_small, fill=PALETTE["surface_bright"])
    draw.text((210, 15), "12:00", font=font_small, fill=PALETTE["surface_bright"])
    draw.text((400, 15), "56%", font=font_small, fill=PALETTE["surface_bright"])
    draw.text((20, 68), "< SYSTEM", font=font_large, fill=PALETTE["ink"])
    rows = [
        ("SLEEP SHUTDOWN", "OFF", None),
        ("BATTERY PERCENTAGE", None, True),
        ("IN-LINE REMOTE", None, False),
        ("LED INDICATORS", None, True),
        ("BUTTONS WITH SCREEN OFF", None, True),
    ]
    y = 120
    for label, value, toggle in rows:
        draw.rounded_rectangle((16, y, 464, y + 112), radius=10, fill=PALETTE["surface_bright"], outline=PALETTE["border"], width=1)
        draw.text((32, y + 25), label, font=font_medium, fill=PALETTE["ink"])
        if value:
            draw.text((32, y + 65), value, font=font_small, fill=PALETTE["muted"])
        if toggle is not None:
            t = make_toggle((60, 36), toggle)
            canvas.paste(t, (385, y + 38), t)
        y += 124
    canvas.save(preview / "settings.png", optimize=True)

    # MSEB/slider preview.
    canvas = Image.new("RGB", (480, 800), hex_rgb(PALETTE["canvas"]))
    canvas.paste(make_dotted_header((480, 50)).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 68), "< MSEB", font=font_large, fill=PALETTE["ink"])
    labels = [
        ("OVERALL TEMPERATURE", "COOL / BRIGHT", "WARM / DARK"),
        ("BASS EXTENSION", "LIGHT", "DEEP"),
        ("BASS TEXTURE", "FAST", "THUMPY"),
        ("NOTE THICKNESS", "CRISP", "THICK"),
        ("VOCALS", "RECESSED", "FORWARD"),
        ("FEMALE OVERTONES", "DETOX", "VIVID"),
    ]
    y = 130
    for title, left, right in labels:
        draw.text((26, y), title, font=font_small, fill=PALETTE["ink"])
        line_y = y + 38
        draw.line((30, line_y, 450, line_y), fill=PALETTE["ink"], width=2)
        for x in range(30, 451, 52):
            draw.line((x, line_y, x, line_y + 7), fill=PALETTE["muted"], width=1)
        knob_x = 240
        draw.ellipse((knob_x - 8, line_y - 8, knob_x + 8, line_y + 8), fill=PALETTE["cyan"], outline=PALETTE["ink"], width=2)
        draw.text((26, line_y + 12), left, font=font_small, fill=PALETTE["muted"])
        rb = draw.textbbox((0, 0), right, font=font_small)
        draw.text((454 - (rb[2] - rb[0]), line_y + 12), right, font=font_small, fill=PALETTE["muted"])
        y += 104
    canvas.save(preview / "sliders.png", optimize=True)


def create_package_metadata(package_root: Path, source_light: Path, retro: Path) -> None:
    palette_path = package_root / "palette.json"
    palette_path.write_text(json.dumps(PALETTE, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest_entries = []
    for path in sorted(p for p in retro.rglob("*") if p.is_file()):
        entry = {
            "path": path.relative_to(retro).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if path.suffix.lower() == ".png":
            with Image.open(path) as image:
                entry["width"], entry["height"] = image.size
                entry["mode"] = image.mode
        manifest_entries.append(entry)
    manifest = {
        "format": 1,
        "theme_id": "retro-handheld",
        "display_name": "Retro Handheld",
        "target": "HiBy R1 firmware 1.6 resource theme",
        "base": "locally extracted light theme",
        "source_tree_sha256": sha256_tree(source_light),
        "theme_tree_sha256": sha256_tree(retro),
        "files": manifest_entries,
        "notes": [
            "The stock font is intentionally not replaced because the shared font set is global and multilingual.",
            "QR codes, certificates, boot animation, and screensavers are preserved to avoid breaking functionality.",
            "The package includes original launcher/card/control assets and transformed vendor resources derived locally from the supplied firmware extraction.",
        ],
    }
    (package_root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    readme = """# Retro Handheld theme for HiBy R1

This package mirrors the extracted stock theme structure:

```text
retro/
├── layout/
└── litegui/
```

It was generated from the locally supplied `light` theme. The design uses a warm
off-white canvas, dark pixel-like iconography, a dotted charcoal status bar,
compact cards, mint/cyan active states, yellow edit accents, coral warnings, and
violet special states.

## Important integration boundary

The package contains locally transformed vendor resources and is intended for
personal firmware development. Do not commit the generated `retro/` tree to a
public repository. Commit the deterministic generator instead and regenerate
from a user-provided firmware extraction during the local build.

The stock shared font has not been replaced. It is global, multilingual, and is
not safely theme-local. The CFW sidecar can continue using its embedded bitmap
font, while the stock player receives the retro colors, cards, icons, controls,
status bar, lists, dialogs, sliders, and launcher assets.

## Codex integration target

Treat `retro/` as a third theme source alongside the extracted light and dark
trees. Mount/copy its `layout` and `litegui` directories through the same safe,
reversible theme-selection mechanism used by the repository. Invalid or missing
files must fall back to the stock theme.

See `manifest.json` for hashes and dimensions, `palette.json` for design tokens,
and `previews/` for deterministic visual references.
"""
    (package_root / "README.md").write_text(readme, encoding="utf-8", newline="\n")


def validate_theme(source_light: Path, retro: Path) -> ValidationResult:
    source_files = {p.relative_to(source_light): p for p in source_light.rglob("*") if p.is_file()}
    output_files = {p.relative_to(retro): p for p in retro.rglob("*") if p.is_file()}
    missing = sorted(set(source_files) - set(output_files))
    if missing:
        raise RuntimeError(f"retro theme is missing {len(missing)} source files: {missing[:10]}")

    changed = 0
    png_count = 0
    for rel, source in source_files.items():
        output = output_files[rel]
        if source.suffix.lower() == ".png":
            png_count += 1
            with Image.open(source) as a, Image.open(output) as b:
                if a.size != b.size:
                    raise RuntimeError(f"PNG geometry changed for {rel}: {a.size} -> {b.size}")
                b.verify()
        if sha256_file(source) != sha256_file(output):
            changed += 1

    for rel, output in output_files.items():
        if output.suffix.lower() == ".png":
            with Image.open(output) as image:
                image.verify()
        elif output.suffix.lower() in TEXT_SUFFIXES:
            output.read_text(encoding="utf-8")

    # Core integration files must contain the generated hooks/assets.
    launcher = (retro / "layout" / "launcher" / "hiby_launcher_apps.view").read_text(encoding="utf-8")
    for name in ("retro_tile_music", "retro_tile_sys_set", "retro_tile_about"):
        if name not in launcher:
            raise RuntimeError(f"launcher injection missing: {name}")
    topbar = (retro / "layout" / "topbar" / "topbar.view").read_text(encoding="utf-8")
    if "retro_topbar_background" not in topbar:
        raise RuntimeError("topbar pattern injection missing")

    added = tuple(sorted(rel.as_posix() for rel in set(output_files) - set(source_files)))
    return ValidationResult(
        source_files=len(source_files),
        output_files=len(output_files),
        png_files=sum(1 for p in output_files if p.suffix.lower() == ".png"),
        added_files=added,
        changed_files=changed,
    )


def deterministic_zip(source_dir: Path, output_zip: Path) -> str:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()
    timestamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(source_dir).as_posix()
            info = zipfile.ZipInfo(rel, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return sha256_file(output_zip)


def build(source_root: Path, output_root: Path, output_zip: Path | None) -> ValidationResult:
    source_light = source_root / "light"
    if not source_light.is_dir():
        raise SystemExit(f"missing extracted light theme: {source_light}")

    package_root = output_root
    retro = package_root / "retro"
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)
    copy_tree(source_light, retro)

    patch_retro_files(retro)
    recolor_all_pngs(retro)
    create_launcher_assets(retro)
    create_topbar_assets(retro)
    create_component_assets(retro)
    create_player_assets(retro)
    create_navigation_assets(retro)
    create_previews(package_root, retro)
    create_package_metadata(package_root, source_light, retro)

    result = validate_theme(source_light, retro)
    zip_hash = deterministic_zip(package_root, output_zip) if output_zip else None
    return ValidationResult(
        source_files=result.source_files,
        output_files=result.output_files,
        png_files=result.png_files,
        added_files=result.added_files,
        changed_files=result.changed_files,
        zip_sha256=zip_hash,
    )
