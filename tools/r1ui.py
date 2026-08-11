#!/usr/bin/env python3
"""Preview and inspect HiBy R1 LiteGUI resources.

The vendor layout files look like JSON, but repeated object keys are part of
their data model.  This module therefore preserves ordered key/value pairs and
never feeds a layout through a normal Python dictionary while parsing it.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections import Counter
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCREEN_WIDTH = 480
SCREEN_HEIGHT = 800
LAYOUT_SUFFIXES = {".view", ".dlg", ".listview", ".json"}
WIDGET_KEYS = {"viewgroup", "imageview", "textview", "progress_bar", "numview"}
IMAGE_PROPERTY_RE = re.compile(
    r"^(?:background_path|img_(?:focus_)?path(?:_?\d+)?|focus_path)$"
)


class PairsObject(list[tuple[str, Any]]):
    """A JSON object whose duplicate keys and source order are retained."""


@dataclass
class Diagnostic:
    level: str
    message: str
    source: str = ""
    node: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "message": self.message,
            "source": self.source,
            "node": self.node,
        }


@dataclass
class Node:
    kind: str
    node_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)

    @property
    def name(self) -> str:
        value = self.properties.get("name")
        return value if isinstance(value, str) else self.node_id


@dataclass
class LayoutDocument:
    source: Path
    root: Node
    diagnostics: list[Diagnostic]


@dataclass
class RenderRegion:
    name: str
    node_id: str
    kind: str
    x: int
    y: int
    w: int
    h: int
    coordinate_model: str = "outer-hgl"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "node_id": self.node_id,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "coordinate_model": self.coordinate_model,
        }


def _pairs_hook(values: list[tuple[str, Any]]) -> PairsObject:
    return PairsObject(values)


def parse_pairs(text: str, source: str = "<memory>") -> tuple[PairsObject, list[Diagnostic]]:
    """Parse a vendor layout, accepting only its known surplus-brace defect."""

    diagnostics: list[Diagnostic] = []
    decoder = json.JSONDecoder(object_pairs_hook=_pairs_hook)
    start = len(text) - len(text.lstrip("\ufeff \t\r\n"))
    try:
        value, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{source}: invalid layout JSON: {error}") from error
    if not isinstance(value, PairsObject):
        raise RuntimeError(f"{source}: top-level layout value is not an object")

    trailing = text[end:].strip()
    if trailing:
        if set(trailing) == {"}"}:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    f"ignored {len(trailing)} surplus closing brace(s)",
                    source,
                )
            )
        else:
            raise RuntimeError(f"{source}: unexpected trailing data: {trailing[:40]!r}")
    return value, diagnostics


def _plain(value: Any) -> Any:
    if isinstance(value, PairsObject):
        # This is only used for non-widget metadata. Preserve duplicates as a
        # list rather than silently choosing a winner.
        return [(key, _plain(item)) for key, item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _build_node(
    kind: str,
    value: PairsObject,
    node_id: str,
    source: str,
    diagnostics: list[Diagnostic],
) -> Node:
    node = Node(kind=kind, node_id=node_id)
    child_counts: Counter[str] = Counter()
    property_counts: Counter[str] = Counter()
    for key, item in value:
        if key in WIDGET_KEYS and isinstance(item, PairsObject):
            index = child_counts[key]
            child_counts[key] += 1
            child_id = f"{node_id}/{key}[{index}]"
            node.children.append(
                _build_node(key, item, child_id, source, diagnostics)
            )
            continue
        property_counts[key] += 1
        if property_counts[key] > 1:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    f"duplicate non-widget property {key!r}; last value is used",
                    source,
                    node_id,
                )
            )
        node.properties[key] = _plain(item)
    return node


def parse_layout(path: Path) -> LayoutDocument:
    text = path.read_text(encoding="utf-8-sig", errors="strict")
    pairs, diagnostics = parse_pairs(text, str(path))
    root = Node(kind="document", node_id="root")
    counts: Counter[str] = Counter()
    for key, item in pairs:
        if key in WIDGET_KEYS and isinstance(item, PairsObject):
            index = counts[key]
            counts[key] += 1
            root.children.append(
                _build_node(
                    key,
                    item,
                    f"{key}[{index}]",
                    str(path),
                    diagnostics,
                )
            )
        elif key == "layout" and isinstance(item, PairsObject):
            # main.json files describe dynamic composition rather than a
            # directly paintable widget, but keeping the node makes linting and
            # inspection complete.
            root.children.append(
                _build_node("layout", item, "layout[0]", str(path), diagnostics)
            )
        else:
            diagnostics.append(
                Diagnostic("warning", f"unknown top-level key {key!r}", str(path))
            )
    return LayoutDocument(path, root, diagnostics)


class ResourceTree:
    def __init__(self, root: Path):
        root = root.resolve()
        candidates = (root / "usr/resource", root / "resource", root)
        for candidate in candidates:
            if (candidate / "layout").is_dir() and (candidate / "litegui").is_dir():
                self.resource = candidate.resolve()
                break
        else:
            raise RuntimeError(
                f"cannot find usr/resource/layout and usr/resource/litegui below {root}"
            )
        self.layout_root = self.resource / "layout"
        self.asset_root = self.resource / "litegui"
        self.string_root = self.resource / "str"
        self.font_root = self.resource / "fonts"

    def layouts(self) -> list[Path]:
        return sorted(
            path
            for path in self.layout_root.rglob("*")
            if path.is_file() and path.suffix in LAYOUT_SUFFIXES
        )

    def relative_layout(self, path: Path) -> str:
        return path.resolve().relative_to(self.layout_root.resolve()).as_posix()

    def resolve_layout(self, value: str, theme: str = "theme1") -> Path:
        raw = value.replace("\\", "/").lstrip("/")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"unsafe layout path: {value}")
        candidates = [self.layout_root / relative]
        if not raw.startswith(("theme1/", "theme2/", "midi/")):
            candidates.insert(0, self.layout_root / theme / relative)
        for candidate in candidates:
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.layout_root.resolve())
            except ValueError:
                continue
            if resolved.is_file() and resolved.suffix in LAYOUT_SUFFIXES:
                return resolved
        raise RuntimeError(f"layout not found: {value}")

    def asset_base_for_layout(self, path: Path, theme_override: str | None = None) -> Path:
        if theme_override:
            return self.asset_root / theme_override
        relative = path.resolve().relative_to(self.layout_root.resolve())
        if len(relative.parts) >= 2 and relative.parts[0] == "midi":
            return self.asset_root / "midi" / relative.parts[1]
        return self.asset_root / relative.parts[0]

    def resolve_asset(self, logical: str, base: Path) -> Path:
        normalized = logical.replace("\\", "/")
        normalized = re.sub(r"^[A-Za-z]:/+", "", normalized)
        normalized = normalized.removeprefix("/usr/resource/litegui/")
        if normalized.startswith("theme1/") or normalized.startswith("theme2/"):
            candidate = self.asset_root / normalized
        elif normalized.startswith("midi/"):
            candidate = self.asset_root / normalized
        else:
            relative = PurePosixPath(normalized.lstrip("/"))
            if ".." in relative.parts:
                raise RuntimeError(f"unsafe asset path: {logical}")
            candidate = base / relative
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.asset_root.resolve())
        except ValueError as error:
            raise RuntimeError(f"unsafe asset path: {logical}") from error
        return resolved


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return default
    return default


def parse_color(value: Any, default: tuple[int, int, int, int] = (0, 0, 0, 0)) -> tuple[int, int, int, int]:
    if not isinstance(value, str):
        return default
    raw = value.strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    try:
        number = int(raw, 16)
    except ValueError:
        return default
    if len(raw) > 6:
        return (
            (number >> 16) & 0xFF,
            (number >> 8) & 0xFF,
            number & 0xFF,
            (number >> 24) & 0xFF,
        )
    return ((number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF, 255)


def intersect_boxes(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    return (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )


class Renderer:
    def __init__(
        self,
        resources: ResourceTree,
        *,
        language: str = "english",
        scenario: dict[str, Any] | None = None,
        touch_overlay: bool = False,
        rgb565: bool = False,
        background: tuple[int, int, int, int] = (238, 243, 247, 255),
        theme_override: str | None = None,
    ):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as error:
            raise RuntimeError("Pillow is required for render and serve") from error
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.ImageFont = ImageFont
        self.resources = resources
        self.language = language
        self.scenario = scenario or {}
        self.touch_overlay = touch_overlay
        self.rgb565 = rgb565
        self.background = background
        self.theme_override = theme_override
        self.image = Image.new("RGBA", (SCREEN_WIDTH, SCREEN_HEIGHT), background)
        self.regions: list[RenderRegion] = []
        self.diagnostics: list[Diagnostic] = []
        self._translations: dict[tuple[str, str], dict[str, str]] = {}
        self._fonts: dict[int, Any] = {}

    def _state(self, node: Node) -> dict[str, Any]:
        states = self.scenario.get("nodes", {})
        if not isinstance(states, dict):
            return {}
        by_id = states.get(node.node_id)
        by_name = states.get(node.name)
        result: dict[str, Any] = {}
        if isinstance(by_id, dict):
            result.update(by_id)
        if isinstance(by_name, dict):
            result.update(by_name)
        return result

    def _scroll(self, node: Node) -> tuple[int, int]:
        values = self.scenario.get("scroll", {})
        if not isinstance(values, dict):
            return (0, 0)
        value = values.get(node.node_id, values.get(node.name, {}))
        if isinstance(value, dict):
            return (_int(value.get("x")), _int(value.get("y")))
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return (_int(value[0]), _int(value[1]))
        return (0, 0)

    def _visible(self, node: Node, state: dict[str, Any]) -> bool:
        if "visible" in state:
            return bool(state["visible"])
        return str(node.properties.get("visible", "show")).lower() != "hide"

    def _asset_choice(self, node: Node, state: dict[str, Any]) -> str | None:
        properties = node.properties
        focused = bool(state.get("focused", False))
        variant = _int(state.get("image_variant", state.get("variant", 0)))

        def numbered(prefix: str) -> str | None:
            keys = (
                f"{prefix}_{variant}",
                f"{prefix}{variant}",
                f"{prefix}_{variant:02d}",
                f"{prefix}{variant:02d}",
            )
            for key in keys:
                value = properties.get(key)
                if isinstance(value, str):
                    return value
            return None

        if focused:
            value = numbered("img_focus_path")
            if value:
                return value
            value = properties.get("img_focus_path", properties.get("focus_path"))
            if isinstance(value, str):
                return value
        value = numbered("img_path")
        if value:
            return value
        value = properties.get("img_path")
        return value if isinstance(value, str) else None

    def _paste_asset(
        self,
        logical: str,
        base: Path,
        position: tuple[int, int],
        clip: tuple[int, int, int, int],
        node: Node,
        *,
        crop_fraction: float | None = None,
    ) -> tuple[int, int] | None:
        if not logical or logical.endswith(("/", "\\")):
            return None
        try:
            path = self.resources.resolve_asset(logical, base)
        except RuntimeError as error:
            self.diagnostics.append(Diagnostic("error", str(error), node=node.node_id))
            return None
        if not path.is_file():
            self.diagnostics.append(
                Diagnostic("warning", f"missing asset: {logical}", str(path), node.node_id)
            )
            return None
        try:
            asset = self.Image.open(path).convert("RGBA")
        except Exception as error:  # Pillow reports format-specific exceptions.
            self.diagnostics.append(
                Diagnostic("warning", f"cannot decode asset {logical}: {error}", str(path), node.node_id)
            )
            return None
        if crop_fraction is not None:
            width = max(0, min(asset.width, round(asset.width * crop_fraction)))
            if width == 0:
                return (asset.width, asset.height)
            asset = asset.crop((0, 0, width, asset.height))
        x, y = position
        target = (x, y, x + asset.width, y + asset.height)
        visible = intersect_boxes(target, clip)
        if visible[2] <= visible[0] or visible[3] <= visible[1]:
            return (asset.width, asset.height)
        crop = asset.crop(
            (
                visible[0] - x,
                visible[1] - y,
                visible[2] - x,
                visible[3] - y,
            )
        )
        self.image.alpha_composite(crop, (visible[0], visible[1]))
        return (asset.width, asset.height)

    def _fill(
        self,
        box: tuple[int, int, int, int],
        color: tuple[int, int, int, int],
        clip: tuple[int, int, int, int],
        radius: int = 0,
    ) -> None:
        visible = intersect_boxes(box, clip)
        if visible[2] <= visible[0] or visible[3] <= visible[1] or color[3] == 0:
            return
        layer = self.Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        draw = self.ImageDraw.Draw(layer)
        if radius > 0:
            draw.rounded_rectangle(box, radius=radius, fill=color)
        else:
            draw.rectangle(box, fill=color)
        crop = layer.crop(visible)
        self.image.alpha_composite(crop, (visible[0], visible[1]))

    def _translation(self, ini_name: str, key: str) -> str | None:
        cache_key = (self.language, ini_name)
        if cache_key not in self._translations:
            path = self.resources.string_root / self.language / ini_name
            values: dict[str, str] = {}
            if path.is_file():
                try:
                    text = path.read_bytes().decode("utf-16")
                    root = ElementTree.fromstring(text)
                    for element in root:
                        values[element.tag] = html.unescape("".join(element.itertext()).strip())
                except (UnicodeError, OSError, ElementTree.ParseError) as error:
                    self.diagnostics.append(
                        Diagnostic("warning", f"cannot read translation {path}: {error}")
                    )
            else:
                self.diagnostics.append(Diagnostic("warning", f"missing translation: {path}"))
            self._translations[cache_key] = values
        return self._translations[cache_key].get(key)

    def _font(self, size: int) -> Any:
        size = max(6, size)
        if size not in self._fonts:
            path = self.resources.font_root / "default.otf"
            try:
                self._fonts[size] = self.ImageFont.truetype(str(path), size=size)
            except OSError:
                self._fonts[size] = self.ImageFont.load_default()
        return self._fonts[size]

    def _draw_text(
        self,
        node: Node,
        state: dict[str, Any],
        origin: tuple[int, int],
        clip: tuple[int, int, int, int],
    ) -> None:
        properties = node.properties
        if "text" in state:
            value = str(state["text"])
        else:
            raw = properties.get("text", "")
            value = str(raw) if raw is not None else ""
            ini = properties.get("ini")
            if isinstance(ini, str) and value:
                value = self._translation(ini, value) or value
        if not value:
            return
        x, y = origin
        width = _int(properties.get("w"), SCREEN_WIDTH)
        height = _int(properties.get("h"), _int(properties.get("size"), 18) + 4)
        if width <= 0 or height <= 0:
            return
        color_key = "focus_color" if state.get("focused") else "color"
        color = parse_color(properties.get(color_key, properties.get("color", "0x000000")))
        font = self._font(_int(properties.get("size"), 18))
        mode = str(properties.get("type", "left")).lower()
        draw = self.ImageDraw.Draw(self.image)
        bbox = draw.textbbox((0, 0), value, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if "right" in mode:
            tx = x + width - text_width
        elif "center" in mode:
            tx = x + (width - text_width) // 2
        else:
            tx = x
        if "bottom" in mode:
            ty = y + height - text_height - bbox[1]
        elif "v_center" in mode or "center" in mode:
            ty = y + (height - text_height) // 2 - bbox[1]
        else:
            ty = y - bbox[1]
        layer = self.Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        self.ImageDraw.Draw(layer).text((tx, ty), value, font=font, fill=color)
        visible = intersect_boxes((x, y, x + width, y + height), clip)
        if visible[2] > visible[0] and visible[3] > visible[1]:
            self.image.alpha_composite(layer.crop(visible), (visible[0], visible[1]))

    def _draw_numview(
        self,
        node: Node,
        state: dict[str, Any],
        origin: tuple[int, int],
        clip: tuple[int, int, int, int],
        base: Path,
    ) -> None:
        prefix = node.properties.get("img_path")
        if not isinstance(prefix, str):
            return
        value = str(state.get("value", ""))
        names = {":": "colon", ".": "dot", "%": "percent", "/": "slash", "-": "-"}
        x, y = origin
        for character in value:
            stem = names.get(character, character)
            dimensions = self._paste_asset(f"{prefix}{stem}.png", base, (x, y), clip, node)
            if dimensions:
                x += dimensions[0]

    def _draw_progress(
        self,
        node: Node,
        state: dict[str, Any],
        origin: tuple[int, int],
        clip: tuple[int, int, int, int],
        base: Path,
    ) -> None:
        value = state.get("value", 0.5)
        try:
            fraction = float(value)
        except (TypeError, ValueError):
            fraction = 0.5
        if fraction > 1:
            fraction /= 100.0
        fraction = max(0.0, min(1.0, fraction))
        background = node.properties.get("img_path_0")
        progress = node.properties.get("img_focus_path_0")
        cursor = node.properties.get("img_path_1")
        if isinstance(background, str):
            self._paste_asset(background, base, origin, clip, node)
        if isinstance(progress, str):
            self._paste_asset(progress, base, origin, clip, node, crop_fraction=fraction)
        if isinstance(cursor, str):
            width = max(0, _int(node.properties.get("w")))
            self._paste_asset(
                cursor,
                base,
                (origin[0] + round(width * fraction), origin[1]),
                clip,
                node,
            )

    def _render_node(
        self,
        node: Node,
        parent_origin: tuple[int, int],
        hgl_origin: tuple[int, int],
        clip: tuple[int, int, int, int],
        asset_base: Path,
        parent_box: tuple[int, int, int, int] = (0, 0, SCREEN_WIDTH, SCREEN_HEIGHT),
    ) -> None:
        state = self._state(node)
        if not self._visible(node, state):
            return
        properties = node.properties
        is_outer_hgl = "hglview_x" in properties and "hglview_y" in properties
        if is_outer_hgl:
            viewport_x = _int(properties.get("hglview_x"))
            viewport_y = _int(properties.get("hglview_y"))
            viewport_w = _int(properties.get("hglview_w"), _int(properties.get("w")))
            viewport_h = _int(properties.get("hglview_h"), _int(properties.get("h")))
            scroll_x, scroll_y = self._scroll(node)
            origin = (viewport_x + scroll_x, viewport_y + scroll_y)
            hgl_origin = origin
            clip = intersect_boxes(
                clip,
                (viewport_x, viewport_y, viewport_x + viewport_w, viewport_y + viewport_h),
            )
        else:
            origin = (
                parent_origin[0] + _int(properties.get("x")),
                parent_origin[1] + _int(properties.get("y")),
            )

        width = _int(properties.get("w"))
        height = _int(properties.get("h"))
        background_color = properties.get("bg_color")
        if background_color is None and node.kind == "viewgroup" and not is_outer_hgl:
            background_color = properties.get("color")
        if background_color is not None and width > 0 and height > 0:
            self._fill(
                (origin[0], origin[1], origin[0] + width, origin[1] + height),
                parse_color(background_color),
                clip,
                _int(properties.get("radiu")),
            )

        group_background = properties.get("background_path")
        if node.kind == "viewgroup" and not isinstance(group_background, str):
            group_background = properties.get("img_path")
        if isinstance(group_background, str):
            self._paste_asset(group_background, asset_base, origin, clip, node)

        if node.kind == "imageview":
            image_path = self._asset_choice(node, state)
            if image_path:
                self._paste_asset(image_path, asset_base, origin, clip, node)
        elif node.kind == "textview":
            self._draw_text(node, state, origin, clip)
        elif node.kind == "numview":
            self._draw_numview(node, state, origin, clip, asset_base)
        elif node.kind == "progress_bar":
            self._draw_progress(node, state, origin, clip, asset_base)

        if all(key in properties for key in ("touch_x", "touch_y", "touch_w", "touch_h")):
            touch_x = _int(properties["touch_x"])
            touch_y = _int(properties["touch_y"])
            touch_w = _int(properties["touch_w"])
            touch_h = _int(properties["touch_h"])
            outer_candidate = (
                hgl_origin[0] + touch_x,
                hgl_origin[1] + touch_y,
                hgl_origin[0] + touch_x + touch_w,
                hgl_origin[1] + touch_y + touch_h,
            )
            parent_candidate = (
                parent_origin[0] + touch_x,
                parent_origin[1] + touch_y,
                parent_origin[0] + touch_x + touch_w,
                parent_origin[1] + touch_y + touch_h,
            )

            def overlap_area(box: tuple[int, int, int, int]) -> int:
                overlap = intersect_boxes(box, parent_box)
                return max(0, overlap[2] - overlap[0]) * max(0, overlap[3] - overlap[1])

            # Most files store hitboxes in outer-HGL content coordinates. A
            # smaller set (notably nested network rows) stores them relative to
            # the immediate parent. Select the interpretation that overlaps the
            # visual parent most, and expose that inference in metadata.
            coordinate_model = "outer-hgl"
            selected = outer_candidate
            if overlap_area(parent_candidate) > overlap_area(outer_candidate):
                coordinate_model = "parent-relative-inferred"
                selected = parent_candidate
            region = RenderRegion(
                name=node.name,
                node_id=node.node_id,
                kind=node.kind,
                x=selected[0],
                y=selected[1],
                w=touch_w,
                h=touch_h,
                coordinate_model=coordinate_model,
            )
            self.regions.append(region)

        if is_outer_hgl:
            child_parent_box = clip
        elif width > 0 and height > 0:
            child_parent_box = (origin[0], origin[1], origin[0] + width, origin[1] + height)
        else:
            child_parent_box = parent_box
        for child in node.children:
            self._render_node(
                child,
                origin,
                hgl_origin,
                clip,
                asset_base,
                child_parent_box,
            )

    def render(self, paths: Iterable[Path]) -> Any:
        top_level: list[tuple[int, int, Node, Path, list[Diagnostic]]] = []
        order = 0
        for path in paths:
            document = parse_layout(path)
            self.diagnostics.extend(document.diagnostics)
            for node in document.root.children:
                top_level.append(
                    (_int(node.properties.get("zorder")), order, node, path, document.diagnostics)
                )
                order += 1
        for _zorder, _order, node, path, _diagnostics in sorted(top_level):
            asset_base = self.resources.asset_base_for_layout(path, self.theme_override)
            self._render_node(
                node,
                (0, 0),
                (0, 0),
                (0, 0, SCREEN_WIDTH, SCREEN_HEIGHT),
                asset_base,
            )
        if self.touch_overlay:
            draw = self.ImageDraw.Draw(self.image)
            for region in self.regions:
                draw.rectangle(
                    (region.x, region.y, region.x + region.w, region.y + region.h),
                    outline=(255, 43, 116, 230),
                    width=2,
                )
                draw.text((region.x + 2, region.y + 2), region.name, fill=(255, 43, 116, 255))
        if self.rgb565:
            red = [((value >> 3) << 3) for value in range(256)]
            green = [((value >> 2) << 2) for value in range(256)]
            blue = red
            rgb = self.image.convert("RGB")
            channels = rgb.split()
            self.image = self.Image.merge(
                "RGB", (channels[0].point(red), channels[1].point(green), channels[2].point(blue))
            ).convert("RGBA")
        return self.image

    def metadata(self) -> dict[str, Any]:
        return {
            "width": SCREEN_WIDTH,
            "height": SCREEN_HEIGHT,
            "regions": [region.as_dict() for region in self.regions],
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


def load_scenario(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("scenario must contain a JSON object")
    return value


def scenario_layouts(
    resources: ResourceTree,
    layout: str | None,
    scenario: dict[str, Any],
    theme: str,
) -> list[Path]:
    values: list[str] = []
    compose = scenario.get("compose")
    if isinstance(compose, list):
        values.extend(str(value) for value in compose)
    scenario_layout = scenario.get("layout")
    if isinstance(scenario_layout, str):
        values.append(scenario_layout)
    if layout:
        values.append(layout)
    if not values:
        raise RuntimeError("provide LAYOUT or a scenario containing layout/compose")
    return [resources.resolve_layout(value, theme) for value in values]


def render_request(
    resources: ResourceTree,
    layout: str,
    *,
    language: str,
    theme: str,
    touch_overlay: bool,
    rgb565: bool = False,
    scenario: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    renderer = Renderer(
        resources,
        language=language,
        scenario=scenario,
        touch_overlay=touch_overlay,
        rgb565=rgb565,
        theme_override=theme,
    )
    image = renderer.render([resources.resolve_layout(layout, theme)])
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue(), renderer.metadata()


def command_render(args: argparse.Namespace) -> None:
    resources = ResourceTree(args.rootfs)
    scenario = load_scenario(args.scenario)
    language = str(scenario.get("language", args.language))
    theme = str(scenario.get("theme", args.theme))
    paths = scenario_layouts(resources, args.layout, scenario, theme)
    renderer = Renderer(
        resources,
        language=language,
        scenario=scenario,
        touch_overlay=args.touch_overlay,
        rgb565=args.rgb565,
        theme_override=theme,
    )
    image = renderer.render(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="PNG", optimize=False)
    if args.metadata:
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        args.metadata.write_text(
            json.dumps(renderer.metadata(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(f"rendered {len(paths)} layout(s): {args.output}")
    print(f"touch regions: {len(renderer.regions)}")
    warning_count = sum(item.level == "warning" for item in renderer.diagnostics)
    if warning_count:
        print(f"render warnings: {warning_count}")


def command_lint(args: argparse.Namespace) -> None:
    resources = ResourceTree(args.rootfs)
    diagnostics: list[Diagnostic] = []
    parsed = 0
    nodes = 0
    image_references = 0
    missing_assets = 0
    failed = 0
    for path in resources.layouts():
        try:
            document = parse_layout(path)
        except RuntimeError as error:
            diagnostics.append(Diagnostic("error", str(error), str(path)))
            failed += 1
            continue
        parsed += 1
        diagnostics.extend(document.diagnostics)
        asset_base = resources.asset_base_for_layout(path)
        stack = list(document.root.children)
        while stack:
            node = stack.pop()
            nodes += 1
            stack.extend(node.children)
            for key, value in node.properties.items():
                if not IMAGE_PROPERTY_RE.match(key) or not isinstance(value, str):
                    continue
                if value.endswith(("/", "\\")):
                    continue
                image_references += 1
                try:
                    asset = resources.resolve_asset(value, asset_base)
                except RuntimeError as error:
                    diagnostics.append(Diagnostic("error", str(error), str(path), node.node_id))
                    failed += 1
                    continue
                if not asset.is_file():
                    missing_assets += 1
                    diagnostics.append(
                        Diagnostic("warning", f"missing asset: {value}", str(path), node.node_id)
                    )
    summary = {
        "layouts": parsed,
        "parse_failures": failed,
        "nodes": nodes,
        "image_references": image_references,
        "missing_assets": missing_assets,
        "warnings": sum(item.level == "warning" for item in diagnostics),
        "errors": sum(item.level == "error" for item in diagnostics),
        "diagnostics": [item.as_dict() for item in diagnostics],
    }
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(
            f"layouts={parsed} nodes={nodes} image_refs={image_references} "
            f"missing_assets={missing_assets} warnings={summary['warnings']} "
            f"errors={summary['errors']}"
        )
        if args.verbose:
            for item in diagnostics:
                location = f" {item.source}" if item.source else ""
                node = f" [{item.node}]" if item.node else ""
                print(f"{item.level}:{location}{node}: {item.message}")
    if summary["errors"]:
        raise RuntimeError("layout lint found errors")


PREVIEW_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HiBy R1 UI preview</title>
<style>
  :root { color-scheme: dark; font: 14px system-ui, sans-serif; }
  body { margin: 0; background: #11151b; color: #e8edf3; }
  header { position: sticky; top: 0; z-index: 2; display: flex; gap: .7rem;
    align-items: center; flex-wrap: wrap; padding: .8rem; background: #1a2029; }
  select, input, button { color: inherit; background: #27313d; border: 1px solid #435165;
    border-radius: 5px; padding: .4rem; }
  main { display: grid; grid-template-columns: minmax(480px, 1fr) minmax(260px, 420px);
    gap: 1rem; padding: 1rem; }
  #screen-wrap { width: 480px; height: 800px; margin: auto; position: relative;
    background: #000; box-shadow: 0 8px 35px #000a; touch-action: none; }
  #screen { display: block; width: 480px; height: 800px; image-rendering: auto; }
  aside { white-space: pre-wrap; overflow-wrap: anywhere; }
  .muted { color: #9eabb9; }
  @media (max-width: 850px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <strong>HiBy R1 LiteGUI preview</strong>
  <select id="layout"></select>
  <label>Language <input id="language" value="english" size="10"></label>
  <label>Theme <select id="theme"><option>theme1</option><option>theme2</option></select></label>
  <label><input id="touch" type="checkbox"> touch overlay</label>
  <button id="reload">Reload</button>
</header>
<main>
  <div id="screen-wrap"><img id="screen" alt="R1 screen preview"></div>
  <aside><h2>Pointer / touch inspector</h2><div id="pointer" class="muted">Click the screen.</div>
  <h2>Diagnostics</h2><div id="diagnostics" class="muted">None.</div>
  <p class="muted">This viewer resolves real stock layouts, assets and touch hitboxes. It does
  not execute proprietary navigation logic; the QEMU bridge is a separate experimental path.</p></aside>
</main>
<script>
const layout = document.querySelector('#layout');
const screen = document.querySelector('#screen');
const language = document.querySelector('#language');
const theme = document.querySelector('#theme');
const touch = document.querySelector('#touch');
const pointer = document.querySelector('#pointer');
const diagnostics = document.querySelector('#diagnostics');
let metadata = {regions: [], diagnostics: []};
function query() {
  const p = new URLSearchParams({layout: layout.value, language: language.value,
    theme: theme.value, touch: touch.checked ? '1' : '0', nonce: Date.now()});
  return p.toString();
}
async function refresh() {
  if (!layout.value) return;
  const q = query();
  screen.src = '/render?' + q;
  const response = await fetch('/metadata?' + q);
  metadata = await response.json();
  diagnostics.textContent = metadata.diagnostics.length ?
    metadata.diagnostics.map(x => `${x.level}: ${x.message}`).join('\n') : 'None.';
}
async function start() {
  const values = await (await fetch('/layouts')).json();
  for (const value of values) {
    const option = document.createElement('option'); option.value = value; option.textContent = value;
    layout.append(option);
  }
  const preferred = values.find(x => x.endsWith('/hiby_net_settings.view')) || values[0];
  layout.value = preferred || '';
  refresh();
}
for (const element of [layout, language, theme, touch]) element.addEventListener('change', refresh);
document.querySelector('#reload').addEventListener('click', refresh);
screen.addEventListener('pointerdown', event => {
  const rect = screen.getBoundingClientRect();
  const x = Math.floor((event.clientX - rect.left) * 480 / rect.width);
  const y = Math.floor((event.clientY - rect.top) * 800 / rect.height);
  const hits = metadata.regions.filter(r => x >= r.x && y >= r.y && x < r.x+r.w && y < r.y+r.h);
  pointer.textContent = `touch x=${x}, y=${y}\n` +
    (hits.length ? hits.map(r => `${r.name} (${r.kind})`).join('\n') : 'no declared touch region');
});
start();
</script>
</body></html>
"""


def command_serve(args: argparse.Namespace) -> None:
    resources = ResourceTree(args.rootfs)
    layout_values = [resources.relative_layout(path) for path in resources.layouts()]
    cache: dict[tuple[str, str, str, bool], tuple[bytes, dict[str, Any]]] = {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, content: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _parameters(self) -> tuple[str, str, str, bool]:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            layout = query.get("layout", [""])[0]
            language = query.get("language", ["english"])[0]
            theme = query.get("theme", ["theme1"])[0]
            touch = query.get("touch", ["0"])[0] == "1"
            if layout not in layout_values:
                raise RuntimeError("unknown layout")
            if theme not in {"theme1", "theme2", "midi/theme1"}:
                raise RuntimeError("unknown theme")
            return (layout, language, theme, touch)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            route = urllib.parse.urlparse(self.path).path
            try:
                if route == "/":
                    self._send(PREVIEW_HTML.encode(), "text/html; charset=utf-8")
                    return
                if route == "/layouts":
                    self._send(json.dumps(layout_values).encode(), "application/json")
                    return
                if route in {"/render", "/metadata"}:
                    key = self._parameters()
                    # Always regenerate: the preview is intended for editing
                    # assets and layouts in place. The small cache only shares
                    # work between the paired image/metadata requests.
                    if key not in cache:
                        cache.clear()
                        cache[key] = render_request(
                            resources,
                            key[0],
                            language=key[1],
                            theme=key[2],
                            touch_overlay=key[3],
                        )
                    png, metadata = cache[key]
                    if route == "/render":
                        self._send(png, "image/png")
                    else:
                        self._send(
                            json.dumps(metadata, ensure_ascii=False).encode(),
                            "application/json; charset=utf-8",
                        )
                    return
                self._send(b"not found\n", "text/plain", HTTPStatus.NOT_FOUND)
            except Exception as error:
                self._send(
                    (str(error) + "\n").encode(),
                    "text/plain; charset=utf-8",
                    HTTPStatus.BAD_REQUEST,
                )

        def log_message(self, format: str, *values: Any) -> None:
            if args.verbose:
                super().log_message(format, *values)

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    host, port = server.server_address[:2]
    visible_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"HiBy R1 UI preview: http://{visible_host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint = subparsers.add_parser("lint", help="validate all stock layout files and asset references")
    lint.add_argument("rootfs", type=Path)
    lint.add_argument("--json", action="store_true", help="emit a machine-readable report")
    lint.add_argument("--verbose", action="store_true")
    lint.set_defaults(function=command_lint)

    render = subparsers.add_parser("render", help="render one or more layouts to a PNG")
    render.add_argument("rootfs", type=Path)
    render.add_argument("layout", nargs="?", help="layout path relative to usr/resource/layout")
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--metadata", type=Path, help="write touch regions and diagnostics as JSON")
    render.add_argument("--scenario", type=Path)
    render.add_argument("--theme", default="theme1", choices=("theme1", "theme2", "midi/theme1"))
    render.add_argument("--language", default="english")
    render.add_argument("--touch-overlay", action="store_true")
    render.add_argument("--rgb565", action="store_true", help="quantize the preview to RGB565 precision")
    render.set_defaults(function=command_render)

    serve = subparsers.add_parser("serve", help="start the interactive browser preview")
    serve.add_argument("rootfs", type=Path)
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765, help="use 0 to choose a free port")
    serve.add_argument("--verbose", action="store_true")
    serve.set_defaults(function=command_serve)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.function(args)
    except RuntimeError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
