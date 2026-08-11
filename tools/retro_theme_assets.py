"""Resource transformation and original asset generation for Retro Handheld."""
from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image, ImageDraw

from retro_theme_common import (
    PALETTE,
    TEXT_SUFFIXES,
    hex_rgb,
    make_dotted_header,
    patch_layout_colors,
    patch_utf16_config,
    pixel_icon,
    recolor_image,
    rounded_asset,
    save_png,
)

def create_launcher_assets(retro: Path) -> None:
    launcher = retro / "litegui" / "launcher"
    tile_specs = {
        "music": (PALETTE["mint_soft"], PALETTE["mint"]),
        "stream_media": (PALETTE["cyan_soft"], PALETTE["cyan"]),
        "wireless": (PALETTE["surface_bright"], PALETTE["cyan"]),
        "book": (PALETTE["yellow_soft"], PALETTE["yellow"]),
        "sys_set": (PALETTE["surface"], PALETTE["disabled"]),
        "about": (PALETTE["violet_soft"], PALETTE["violet"]),
        "cfw": (PALETTE["cyan_soft"], PALETTE["cyan"]),
        "dac": (PALETTE["surface"], PALETTE["yellow"]),
    }
    icon_kind = {
        "music": "music",
        "stream_media": "stream",
        "wireless": "wireless",
        "book": "book",
        "sys_set": "system",
        "about": "about",
        "cfw": "cfw",
        "dac": "dac",
    }
    for name, (normal, focused) in tile_specs.items():
        save_png(
            rounded_asset((224, 230), normal, radius=12, border=PALETTE["border"], border_width=2),
            launcher / f"tile_{name}.png",
        )
        save_png(
            rounded_asset((224, 230), focused, radius=12, border=PALETTE["ink"], border_width=2),
            launcher / f"tile_{name}_s.png",
        )
        save_png(pixel_icon(icon_kind[name], color=PALETTE["ink"]), launcher / f"{name}.png")
        focus_color = PALETTE["surface_bright"] if name not in {"sys_set", "book"} else PALETTE["ink"]
        save_png(pixel_icon(icon_kind[name], color=focus_color), launcher / f"{name}_s.png")

    # Existing separators are rendered transparent because cards provide spacing/borders.
    save_png(Image.new("RGBA", (480, 1), (0, 0, 0, 0)), launcher / "hor_line.png")
    # Preserve source ver_line geometry if available, otherwise use 1x730.
    source_ver = launcher / "ver_line.png"
    ver_size = Image.open(source_ver).size if source_ver.exists() else (1, 730)
    save_png(Image.new("RGBA", ver_size, (0, 0, 0, 0)), source_ver)


def inject_launcher_cards(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    entries = {
        "launcher_apps_iv_player": "music",
        "launcher_apps_iv_stream_media": "stream_media",
        "launcher_apps_iv_wireless": "wireless",
        "launcher_apps_iv_ebook": "book",
        "launcher_apps_iv_sysset": "sys_set",
        "launcher_apps_iv_about": "about",
    }
    for widget, asset in entries.items():
        marker = f'"name":"{widget}",'
        index = text.find(marker)
        if index < 0:
            raise RuntimeError(f"launcher widget not found: {widget}")
        image_start = text.rfind('"imageview":{', 0, index)
        if image_start < 0:
            raise RuntimeError(f"imageview start not found for {widget}")
        indent_start = text.rfind("\n", 0, image_start) + 1
        indent = text[indent_start:image_start]
        background = (
            f'"imageview":{{\n'
            f'{indent}\t"name":"retro_tile_{asset}",\n'
            f'{indent}\t"img_path":"launcher\\\\tile_{asset}.png",\n'
            f'{indent}\t"img_focus_path":"launcher\\\\tile_{asset}_s.png",\n'
            f'{indent}\t"x":8,\n'
            f'{indent}\t"y":8,\n'
            f'{indent}\t"color_mode":"color_565",\n'
            f'{indent}\t"imageview":true\n'
            f'{indent}}},\n{indent}'
        )
        text = text[:image_start] + background + text[image_start:]
    path.write_text(text, encoding="utf-8", newline="\n")


def inject_topbar_pattern(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = '"name":"topbar_iv_speaker",'
    index = text.find(marker)
    if index < 0:
        raise RuntimeError("topbar speaker widget not found")
    image_start = text.rfind('"imageview":{', 0, index)
    indent_start = text.rfind("\n", 0, image_start) + 1
    indent = text[indent_start:image_start]
    background = (
        f'"imageview":{{\n'
        f'{indent}\t"name":"retro_topbar_background",\n'
        f'{indent}\t"img_path":"topbar\\\\retro_bg.png",\n'
        f'{indent}\t"x":0,\n'
        f'{indent}\t"y":0,\n'
        f'{indent}\t"color_mode":"color_565",\n'
        f'{indent}\t"imageview":true\n'
        f'{indent}}},\n{indent}'
    )
    text = text[:image_start] + background + text[image_start:]
    # Ensure the base color is dark even if the image cannot be loaded.
    text = text.replace('"color":"0xf0eee7"', '"color":"0x494949"')
    path.write_text(text, encoding="utf-8", newline="\n")


def create_topbar_assets(retro: Path) -> None:
    topbar = retro / "litegui" / "topbar"
    save_png(make_dotted_header((480, 50)), topbar / "retro_bg.png")

    # Recolor all status glyphs to off-white, preserving semantic battery/alert colors.
    for path in topbar.glob("*.png"):
        if path.name == "retro_bg.png":
            continue
        image = Image.open(path).convert("RGBA")
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                r, g, b, a = pixels[x, y]
                if a == 0:
                    continue
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                hue = h * 360
                if 75 <= hue <= 165 and s > 0.25:
                    color = hex_rgb(PALETTE["mint"])
                elif (hue < 20 or hue > 340) and s > 0.25:
                    color = hex_rgb(PALETTE["coral"])
                else:
                    color = hex_rgb(PALETTE["surface_bright"])
                pixels[x, y] = (*color, a)
        image.save(path, optimize=True)

    player = retro / "litegui" / "playing_plane"
    if (player / "topbar_bg.png").exists():
        size = Image.open(player / "topbar_bg.png").size
        save_png(make_dotted_header(size), player / "topbar_bg.png")


def make_toggle(size: tuple[int, int], enabled: bool, *, compact: bool = False) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if compact:
        x0, y0, x1, y1 = 1, max(1, size[1] // 4), size[0] - 2, size[1] - max(2, size[1] // 4)
    else:
        margin = max(2, min(size) // 8)
        x0, y0, x1, y1 = margin, margin, size[0] - margin - 1, size[1] - margin - 1
    radius = max(2, (y1 - y0) // 2)
    fill = PALETTE["mint"] if enabled else PALETTE["disabled"]
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=fill, outline=PALETTE["ink"], width=1)
    diameter = max(3, y1 - y0 - 4)
    knob_x = x1 - diameter - 2 if enabled else x0 + 2
    knob_y = y0 + ((y1 - y0 + 1) - diameter) // 2
    draw.ellipse((knob_x, knob_y, knob_x + diameter, knob_y + diameter), fill=PALETTE["surface_bright"])
    return image


def create_component_assets(retro: Path) -> None:
    # List cards.
    touch = retro / "litegui" / "touch_list"
    card_specs = {
        "item_bg.png": (PALETTE["surface_bright"], PALETTE["border"]),
        "item_bg_focus.png": (PALETTE["mint_soft"], PALETTE["ink"]),
        "item_bg_s.png": (PALETTE["cyan_soft"], PALETTE["ink"]),
        "item_invalid_bg.png": (PALETTE["surface"], PALETTE["disabled"]),
        "little_item_bg.png": (PALETTE["surface_bright"], PALETTE["border"]),
        "middle_item_bg.png": (PALETTE["surface_bright"], PALETTE["border"]),
        "list_album_bg.png": (PALETTE["surface_bright"], PALETTE["border"]),
        "list_album_bg_s.png": (PALETTE["cyan_soft"], PALETTE["ink"]),
        "inputbox.png": (PALETTE["surface_bright"], PALETTE["ink"]),
    }
    for name, (fill, border) in card_specs.items():
        path = touch / name
        if not path.exists():
            continue
        size = Image.open(path).size
        radius = min(12, max(4, min(size) // 5))
        save_png(rounded_asset(size, fill, radius=radius, border=border, border_width=1), path)
    if (touch / "list_line.png").exists():
        size = Image.open(touch / "list_line.png").size
        save_png(Image.new("RGBA", size, (0, 0, 0, 0)), touch / "list_line.png")

    # Flat toggles.
    settings = retro / "litegui" / "settings"
    for name, enabled in (("on.png", True), ("off.png", False)):
        path = settings / name
        if path.exists():
            save_png(make_toggle(Image.open(path).size, enabled), path)

    tune = retro / "litegui" / "tune"
    for name, enabled in (("on.png", True), ("off.png", False), ("big_on.png", True), ("big_off.png", False)):
        path = tune / name
        if path.exists():
            save_png(make_toggle(Image.open(path).size, enabled, compact=name in {"on.png", "off.png"}), path)

    # Slider tracks and knobs.
    if (tune / "bg.png").exists():
        size = Image.open(tune / "bg.png").size
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        y = size[1] // 2
        draw.line((4, y, size[0] - 5, y), fill=PALETTE["ink"], width=2)
        for x in range(4, size[0] - 4, max(1, (size[0] - 8) // 8)):
            draw.line((x, y, x, y + 7), fill=PALETTE["muted"], width=1)
        save_png(image, tune / "bg.png")
    if (tune / "cursor.png").exists():
        size = Image.open(tune / "cursor.png").size
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        margin = max(2, min(size) // 6)
        draw.ellipse((margin, margin, size[0] - margin - 1, size[1] - margin - 1),
                     fill=PALETTE["cyan"], outline=PALETTE["ink"], width=2)
        save_png(image, tune / "cursor.png")

    eq = retro / "litegui" / "eq"
    for name, accent in (("slider.png", False), ("slider_s.png", True)):
        path = eq / name
        if not path.exists():
            continue
        size = Image.open(path).size
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        x = size[0] // 2
        draw.line((x, 2, x, size[1] - 3), fill=PALETTE["cyan"] if accent else PALETTE["ink"], width=max(2, size[0] // 5))
        save_png(image, path)


def _draw_pixel_control(kind: str, size: tuple[int, int], focused: bool = False) -> Image.Image:
    logical = 21 if max(size) >= 80 else 10
    icon = Image.new("RGBA", (logical, logical), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    ink = PALETTE["cyan"] if focused else PALETTE["ink"]
    if kind in {"play", "pause"} and max(size) >= 80:
        draw.ellipse((1, 1, logical - 2, logical - 2), fill=PALETTE["surface_bright"], outline=PALETTE["ink"], width=1)
    if kind == "play":
        draw.polygon([(8, 6), (8, 15), (15, 10)], fill=ink)
    elif kind == "pause":
        draw.rectangle((7, 6, 9, 15), fill=ink)
        draw.rectangle((12, 6, 14, 15), fill=ink)
    elif kind == "prev":
        draw.rectangle((2, 2, 3, 8), fill=ink)
        draw.polygon([(8, 2), (8, 8), (3, 5)], fill=ink)
    elif kind == "next":
        draw.rectangle((7, 2, 8, 8), fill=ink)
        draw.polygon([(2, 2), (2, 8), (7, 5)], fill=ink)
    elif kind == "more":
        for y in (2, 5, 8):
            draw.rectangle((4, y, 5, y + 1), fill=ink)
    elif kind == "heart":
        draw.rectangle((3, 3, 7, 7), fill=PALETTE["coral"])
        draw.rectangle((2, 4, 8, 6), fill=PALETTE["coral"])
        draw.polygon([(2, 5), (8, 5), (5, 9)], fill=PALETTE["coral"])
    return icon.resize(size, Image.Resampling.NEAREST)


def create_player_assets(retro: Path) -> None:
    player = retro / "litegui" / "playing_plane"
    if (player / "buttom.png").exists():
        size = Image.open(player / "buttom.png").size
        image = Image.new("RGBA", size, hex_rgb(PALETTE["canvas"]) + (255,))
        draw = ImageDraw.Draw(image)
        draw.line((0, 0, size[0], 0), fill=PALETTE["ink"], width=2)
        draw.rectangle((8, 8, size[0] - 9, size[1] - 9), outline=PALETTE["border"], width=1)
        save_png(image, player / "buttom.png")

    controls = {
        "btn_play.png": ("play", False),
        "btn_play_s.png": ("play", True),
        "btn_pause.png": ("pause", False),
        "btn_pause_s.png": ("pause", True),
        "btn_prev.png": ("prev", False),
        "btn_prev_f.png": ("prev", True),
        "btn_prev_s.png": ("prev", True),
        "btn_next.png": ("next", False),
        "btn_next_f.png": ("next", True),
        "btn_next_s.png": ("next", True),
        "ic_more.png": ("more", False),
        "ic_more_s.png": ("more", True),
        "collect_in.png": ("heart", False),
        "collect_out.png": ("heart", False),
        "collect_out_s.png": ("heart", True),
    }
    for name, (kind, focused) in controls.items():
        path = player / name
        if path.exists():
            save_png(_draw_pixel_control(kind, Image.open(path).size, focused), path)

    # Progress bar.
    for name, color in (("progress_bg.png", PALETTE["surface_bright"]), ("progress.png", PALETTE["cyan"])):
        path = player / name
        if path.exists():
            size = Image.open(path).size
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            y = size[1] // 2
            draw.rounded_rectangle((0, max(0, y - 3), size[0] - 1, min(size[1] - 1, y + 3)), radius=3,
                                   fill=color, outline=PALETTE["ink"] if name == "progress_bg.png" else None)
            save_png(image, path)
    for name in ("cursor.png", "cursor_focus.png"):
        path = player / name
        if path.exists():
            size = Image.open(path).size
            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            m = 2
            draw.ellipse((m, m, size[0] - m - 1, size[1] - m - 1), fill=PALETTE["cyan"], outline=PALETTE["ink"], width=2)
            save_png(image, path)

    # Original retro placeholder cover.
    cover = player / "default_cover_565.png"
    if cover.exists():
        size = Image.open(cover).size
        image = Image.new("RGB", size, hex_rgb(PALETTE["cyan_soft"]))
        draw = ImageDraw.Draw(image)
        step = max(8, size[0] // 40)
        for y in range(0, size[1], step):
            for x in range(0, size[0], step):
                if (x // step + y // step) % 2 == 0:
                    draw.rectangle((x, y, x + 1, y + 1), fill=hex_rgb(PALETTE["surface_bright"]))
        note = pixel_icon("music", color=PALETTE["ink"], size=min(size) // 2)
        image_rgba = image.convert("RGBA")
        image_rgba.alpha_composite(note, ((size[0] - note.width) // 2, (size[1] - note.height) // 2))
        save_png(image_rgba.convert("RGB"), cover)


def create_navigation_assets(retro: Path) -> None:
    sub = retro / "litegui" / "sub_back"
    for name, color in (("btn_back.png", PALETTE["ink"]), ("btn_back_s.png", PALETTE["cyan"]), ("btn_back_w.png", PALETTE["surface_bright"])):
        path = sub / name
        if not path.exists():
            continue
        size = Image.open(path).size
        logical = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
        draw = ImageDraw.Draw(logical)
        draw.line((7, 1, 2, 4), fill=color, width=2)
        draw.line((2, 4, 7, 7), fill=color, width=2)
        save_png(logical.resize(size, Image.Resampling.NEAREST), path)

    pull = retro / "litegui" / "pull_down"
    if (pull / "bg.png").exists():
        size = Image.open(pull / "bg.png").size
        save_png(rounded_asset(size, PALETTE["surface_bright"], radius=12, border=PALETTE["border"], border_width=1), pull / "bg.png")


def patch_retro_files(retro: Path) -> None:
    for path in retro.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            patch_layout_colors(path)
    config = retro / "litegui" / "config.ini"
    if config.exists():
        patch_utf16_config(config)

    inject_launcher_cards(retro / "layout" / "launcher" / "hiby_launcher_apps.view")
    inject_topbar_pattern(retro / "layout" / "topbar" / "topbar.view")


def recolor_all_pngs(retro: Path) -> None:
    for path in sorted(retro.rglob("*.png")):
        recolor_image(path, path.relative_to(retro).as_posix())
