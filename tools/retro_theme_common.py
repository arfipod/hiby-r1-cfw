"""Shared design tokens and deterministic image primitives for Retro Handheld."""
from __future__ import annotations

import colorsys
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

PALETTE = {
    "canvas": "#F0EEE7",
    "surface": "#E5E2DA",
    "surface_bright": "#FAF8F2",
    "frame": "#FFFFFF",
    "header": "#494949",
    "header_dot": "#686868",
    "ink": "#171A1A",
    "muted": "#777777",
    "border": "#97938B",
    "disabled": "#CACACA",
    "mint": "#20D898",
    "mint_soft": "#B5E8D0",
    "cyan": "#02AAEB",
    "cyan_soft": "#B8E6F4",
    "yellow": "#FECA40",
    "yellow_soft": "#F7DEA0",
    "coral": "#EE474F",
    "coral_soft": "#F4B3B6",
    "violet": "#9A70F0",
    "violet_soft": "#D8CAF2",
}

LAYOUT_COLOR_MAP = {
    "0x32526c": "0x171a1a",
    "0xe4f5fe": "0xf0eee7",
    "0x1062f2": "0x02aaeb",
    "0x1061f7": "0x02aaeb",
    "0x1963f0": "0x02aaeb",
    "0x48e2e4": "0x20d898",
    "0x45e2e3": "0x20d898",
    "0xd5e4f1": "0xe5e2da",
    "0xdeeaf4": "0xe8e5dd",
    "0xe1ecf5": "0xebe8e1",
    "0xdef3ff": "0xf2efe8",
    "0xfafcfd": "0xfaf8f2",
    "0xa7b0bf": "0x777777",
    "0x92a4b3": "0x8b8881",
    "0xa8b7cd": "0x8b8881",
    "0x1b2426": "0x171a1a",
    "0x1d1d1d": "0x171a1a",
    "0x2d2d2d": "0x171a1a",
    "0x2e303c": "0x171a1a",
    "0x214e3c": "0x20a77a",
    "0x182a1d": "0x171a1a",
    "0xbd9b3a": "0xd9a61f",
    "0xa4a1b0": "0x8d8998",
}

TEXT_SUFFIXES = {".view", ".dlg", ".listview", ".json"}
PROTECTED_IMAGE_PARTS = (
    "/instructions/",
    "/certificate/",
    "/boot_animation/",
    "/screensavers/",
)


@dataclass(frozen=True)
class ValidationResult:
    source_files: int
    output_files: int
    png_files: int
    added_files: tuple[str, ...]
    changed_files: int
    zip_sha256: str | None = None


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def replace_case_insensitive(text: str, old: str, new: str) -> str:
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


def patch_layout_colors(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    for old, new in LAYOUT_COLOR_MAP.items():
        text = replace_case_insensitive(text, old, new)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_utf16_config(path: Path) -> None:
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    text = raw.decode(encoding)
    replacements = {
        "<lrc_color>0xffffff</lrc_color>": "<lrc_color>0xfaf8f2</lrc_color>",
        "<lrc_focus_color>0x1062f2</lrc_focus_color>": "<lrc_focus_color>0x02aaeb</lrc_focus_color>",
        "<book_bg_color_0>0xc8dced</book_bg_color_0>": "<book_bg_color_0>0xe5e2da</book_bg_color_0>",
        "<book_bg_color_1>0xcce8cf</book_bg_color_1>": "<book_bg_color_1>0xb5e8d0</book_bg_color_1>",
        "<book_bg_color_2>0xffffff</book_bg_color_2>": "<book_bg_color_2>0xfaf8f2</book_bg_color_2>",
        "<book_bg_color_3>0xf0e7aa</book_bg_color_3>": "<book_bg_color_3>0xf7dea0</book_bg_color_3>",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if encoding == "utf-16":
        path.write_bytes(text.encode("utf-16"))
    else:
        path.write_text(text, encoding="utf-8", newline="\n")


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(round(x * (1.0 - amount) + y * amount) for x, y in zip(a, b))


def recolor_pixel(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    exact = {
        (50, 82, 108): hex_rgb(PALETTE["ink"]),
        (228, 245, 254): hex_rgb(PALETTE["canvas"]),
        (16, 98, 242): hex_rgb(PALETTE["cyan"]),
        (0, 159, 246): hex_rgb(PALETTE["cyan"]),
        (72, 226, 228): hex_rgb(PALETTE["mint"]),
        (69, 226, 227): hex_rgb(PALETTE["mint"]),
        (213, 228, 241): hex_rgb(PALETTE["surface"]),
        (222, 234, 244): hex_rgb(PALETTE["surface"]),
        (225, 236, 245): hex_rgb(PALETTE["surface_bright"]),
        (167, 176, 191): hex_rgb(PALETTE["muted"]),
        (27, 36, 38): hex_rgb(PALETTE["ink"]),
    }
    if rgb in exact:
        return exact[rgb]

    r, g, b = rgb
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue = h * 360.0

    if s < 0.10 and luminance > 0.96:
        return hex_rgb(PALETTE["surface_bright"])
    if s < 0.12 and luminance > 0.82:
        target = hex_rgb(PALETTE["surface"])
        return _mix(target, (255, 255, 255), min(1.0, (luminance - 0.82) / 0.18) * 0.65)
    if s < 0.14 and luminance < 0.28:
        return _mix(hex_rgb(PALETTE["ink"]), rgb, 0.15)
    if s < 0.14 and 0.28 <= luminance <= 0.75:
        return _mix(hex_rgb(PALETTE["muted"]), rgb, 0.20)

    if s > 0.35:
        if hue < 18 or hue >= 345:
            base = hex_rgb(PALETTE["coral"])
        elif hue < 75:
            base = hex_rgb(PALETTE["yellow"])
        elif hue < 175:
            base = hex_rgb(PALETTE["mint"])
        elif hue < 250:
            base = hex_rgb(PALETTE["cyan"])
        elif hue < 335:
            base = hex_rgb(PALETTE["violet"])
        else:
            base = hex_rgb(PALETTE["coral"])
        if v > 0.88:
            return _mix(base, (255, 255, 255), (v - 0.88) / 0.12 * 0.45)
        if v < 0.50:
            return _mix(base, hex_rgb(PALETTE["ink"]), (0.50 - v) / 0.50 * 0.50)
        return base

    return rgb


_COLOR_LUT: ImageFilter.Color3DLUT | None = None


def color_lut() -> ImageFilter.Color3DLUT:
    global _COLOR_LUT
    if _COLOR_LUT is None:
        def transform(red: float, green: float, blue: float) -> tuple[float, float, float]:
            mapped = recolor_pixel((round(red * 255), round(green * 255), round(blue * 255)))
            return tuple(component / 255.0 for component in mapped)
        _COLOR_LUT = ImageFilter.Color3DLUT.generate(33, transform)
    return _COLOR_LUT


def recolor_image(path: Path, relative: str) -> None:
    normalized = "/" + relative.replace("\\", "/")
    if any(part in normalized for part in PROTECTED_IMAGE_PARTS):
        return
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    recolored = image.convert("RGB").filter(color_lut()).convert("RGBA")
    recolored.putalpha(alpha)
    recolored.save(path, optimize=True)


def rounded_asset(
    size: tuple[int, int],
    fill: str,
    *,
    radius: int,
    border: str | None = None,
    border_width: int = 1,
    transparent: bool = True,
) -> Image.Image:
    background = (0, 0, 0, 0) if transparent else hex_rgb(PALETTE["canvas"]) + (255,)
    image = Image.new("RGBA", size, background)
    draw = ImageDraw.Draw(image)
    box = (border_width, border_width, size[0] - 1 - border_width, size[1] - 1 - border_width)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=border_width)
    return image


def pixel_icon(kind: str, *, color: str, size: int = 140) -> Image.Image:
    logical = 35
    image = Image.new("RGBA", (logical, logical), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    c = color

    if kind == "music":
        draw.rectangle((17, 7, 20, 24), fill=c)
        draw.rectangle((20, 7, 29, 10), fill=c)
        draw.rectangle((26, 9, 29, 21), fill=c)
        draw.ellipse((12, 21, 20, 29), fill=c)
        draw.ellipse((22, 18, 30, 26), fill=c)
    elif kind == "stream":
        draw.rounded_rectangle((8, 11, 27, 25), radius=2, outline=c, width=2)
        draw.rectangle((11, 15, 23, 22), outline=c, width=1)
        draw.line((12, 10, 18, 5), fill=c, width=2)
        draw.arc((4, 8, 16, 28), start=270, end=90, fill=c, width=2)
        draw.arc((19, 8, 31, 28), start=90, end=270, fill=c, width=2)
    elif kind == "wireless":
        draw.arc((4, 5, 31, 31), start=205, end=335, fill=c, width=3)
        draw.arc((9, 10, 26, 27), start=205, end=335, fill=c, width=3)
        draw.arc((14, 15, 21, 22), start=205, end=335, fill=c, width=3)
        draw.ellipse((16, 24, 19, 27), fill=c)
    elif kind == "book":
        draw.polygon([(5, 9), (16, 11), (16, 28), (5, 25)], outline=c, fill=None)
        draw.polygon([(30, 9), (19, 11), (19, 28), (30, 25)], outline=c, fill=None)
        draw.line((17, 11, 17, 28), fill=c, width=2)
        draw.line((18, 11, 18, 28), fill=c, width=1)
    elif kind == "system":
        draw.rectangle((13, 5, 21, 29), fill=c)
        draw.rectangle((5, 13, 29, 21), fill=c)
        draw.rectangle((8, 8, 26, 26), fill=c)
        draw.rectangle((11, 11, 23, 23), fill=c)
        draw.rectangle((15, 15, 19, 19), fill=(0, 0, 0, 0))
    elif kind == "about":
        draw.rounded_rectangle((8, 7, 27, 28), radius=3, fill=c)
        hole = "#FAF8F2" if color.lower() != "#faf8f2" else PALETTE["ink"]
        draw.rectangle((16, 12, 19, 15), fill=hole)
        draw.rectangle((16, 18, 19, 24), fill=hole)
    elif kind == "cfw":
        draw.rounded_rectangle((5, 7, 30, 28), radius=2, outline=c, width=2)
        draw.line((10, 13, 14, 17), fill=c, width=2)
        draw.line((14, 17, 10, 21), fill=c, width=2)
        draw.line((18, 22, 25, 22), fill=c, width=2)
    elif kind == "dac":
        draw.rectangle((7, 8, 28, 27), outline=c, width=2)
        for index, height in enumerate((6, 12, 18, 10, 15)):
            x = 10 + index * 4
            draw.rectangle((x, 25 - height, x + 1, 25), fill=c)
    else:
        draw.rectangle((10, 10, 24, 24), outline=c, width=2)

    return image.resize((size, size), Image.Resampling.NEAREST)


def save_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)


def make_dotted_header(size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGBA", size, hex_rgb(PALETTE["header"]) + (255,))
    draw = ImageDraw.Draw(image)
    dot = hex_rgb(PALETTE["header_dot"]) + (255,)
    for y in range(4, size[1], 8):
        for x in range(4, size[0], 8):
            draw.rectangle((x, y, x + 1, y + 1), fill=dot)
    return image
