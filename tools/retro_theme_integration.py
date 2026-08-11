"""Firmware-integration additions for the deterministic Retro Handheld theme."""
from __future__ import annotations

from pathlib import Path

from retro_theme_common import PALETTE, ValidationResult, rounded_asset, save_png
from retro_theme_package import (
    build as build_base_theme,
    create_package_metadata,
    deterministic_zip,
    validate_theme,
)


_TILE_COLORS = {
    "music": (PALETTE["mint_soft"], PALETTE["mint"]),
    "stream_media": (PALETTE["cyan_soft"], PALETTE["cyan"]),
    "wireless": (PALETTE["surface_bright"], PALETTE["cyan"]),
    "book": (PALETTE["yellow_soft"], PALETTE["yellow"]),
    "sys_set": (PALETTE["surface"], PALETTE["disabled"]),
    "about": (PALETTE["violet_soft"], PALETTE["violet"]),
    "cfw": (PALETTE["cyan_soft"], PALETTE["cyan"]),
    "dac": (PALETTE["surface"], PALETTE["yellow"]),
}


def add_integration_assets(retro: Path) -> None:
    """Add original assets needed by generated wide launcher variants."""

    launcher = retro / "litegui" / "launcher"
    if not launcher.is_dir():
        raise RuntimeError(f"retro launcher assets are missing: {launcher}")
    for name, (normal, focused) in _TILE_COLORS.items():
        save_png(
            rounded_asset(
                (464, 230),
                normal,
                radius=12,
                border=PALETTE["border"],
                border_width=2,
            ),
            launcher / f"tile_{name}_wide.png",
        )
        save_png(
            rounded_asset(
                (464, 230),
                focused,
                radius=12,
                border=PALETTE["ink"],
                border_width=2,
            ),
            launcher / f"tile_{name}_wide_s.png",
        )
    (retro / ".r1-theme").write_text(
        "theme=retro-handheld\nformat=1\n",
        encoding="ascii",
        newline="\n",
    )


def build(
    source_root: Path,
    output_root: Path,
    output_zip: Path | None,
) -> ValidationResult:
    """Build the complete importable package, including firmware assets."""

    build_base_theme(source_root, output_root, None)
    source_light = source_root / "light"
    retro = output_root / "retro"
    add_integration_assets(retro)
    create_package_metadata(output_root, source_light, retro)
    result = validate_theme(source_light, retro)
    zip_hash = deterministic_zip(output_root, output_zip) if output_zip else None
    return ValidationResult(
        source_files=result.source_files,
        output_files=result.output_files,
        png_files=result.png_files,
        added_files=result.added_files,
        changed_files=result.changed_files,
        zip_sha256=zip_hash,
    )
