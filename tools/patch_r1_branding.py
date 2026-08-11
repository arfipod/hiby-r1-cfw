#!/usr/bin/env python3
"""Apply the deterministic HiBy R1 CFW marker to an extracted rootfs.

The stock ``about_dev_tv_model`` is both populated at runtime and owns the
five-tap Developer Mode gesture.  This tool therefore leaves that widget and
the product identity byte-for-byte unchanged.  It adds a separate static label
near the bottom of the About page, outside every stock control's hit region.
The label has no background color and reads the exact CFW marker from every
locale's ``about_dev.ini``.

Only an extracted root filesystem is modified.  No vendor resource is stored
in this repository.  All required inputs are validated before any file is
written, and already branded inputs are accepted unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


CFW_LABEL = "HiByR1 1,6 CFW"
MODEL_WIDGET_NAME = "about_dev_tv_model"
CFW_WIDGET_NAME = "about_dev_tv_cfw_label"
CFW_RESOURCE_KEY = "cfw_label"

LANGUAGES = (
    "english",
    "french",
    "german",
    "italy",
    "japanese",
    "korean",
    "poland",
    "russian",
    "simplified_chinese",
    "spain",
    "thai",
    "traditional_chinese",
    "ukrainian",
)


@dataclass(frozen=True)
class LayoutSpec:
    relative_path: Path
    newline: str
    foreground: str


LAYOUT_SPECS = (
    LayoutSpec(
        Path("usr/resource/layout/theme1/hiby_about_dev.view"),
        "\n",
        "0x32526c",
    ),
    LayoutSpec(
        Path("usr/resource/layout/theme2/hiby_about_dev.view"),
        "\n",
        "0xffffff",
    ),
    LayoutSpec(
        Path("usr/resource/layout/midi/theme1/hiby_about_dev.view"),
        "\r\n",
        "0xffffff",
    ),
)

EXPECTED_PRODUCT_IDENTITY = {
    "company": "HiBy",
    "device": "R1",
    "ota_name": "HiBy R1",
}


class BrandingError(RuntimeError):
    """The rootfs is absent, unexpected, partially corrupted, or conflicting."""


@dataclass(frozen=True)
class FilePatch:
    path: Path
    original: bytes
    updated: bytes

    @property
    def changed(self) -> bool:
        return self.original != self.updated


def _require_regular_file(rootfs: Path, relative_path: Path) -> Path:
    path = rootfs / relative_path
    if path.is_symlink() or not path.is_file():
        raise BrandingError(f"required regular file is missing: {relative_path}")
    return path


def _newline_style(text: str, *, path: Path) -> str:
    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf:
        raise BrandingError(f"unsupported CR or mixed line endings: {path}")
    has_crlf = "\r\n" in text
    has_lf = "\n" in without_crlf
    if has_crlf and has_lf:
        raise BrandingError(f"mixed CRLF/LF line endings: {path}")
    if has_crlf:
        return "\r\n"
    if has_lf:
        return "\n"
    raise BrandingError(f"expected a multi-line text file: {path}")


def _replace_single_tag(
    text: str,
    *,
    tag: str,
    allowed_values: set[str],
    replacement: str,
    path: Path,
) -> str:
    pattern = re.compile(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", re.DOTALL)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise BrandingError(f"expected exactly one <{tag}> element: {path}")
    current = matches[0].group(1)
    if current not in allowed_values:
        raise BrandingError(
            f"unexpected <{tag}> value in {path}: {current!r}"
        )
    match = matches[0]
    return text[: match.start(1)] + replacement + text[match.end(1) :]


def _patch_about_resource(path: Path) -> FilePatch:
    raw = path.read_bytes()
    if not raw.startswith(b"\xff\xfe"):
        raise BrandingError(f"expected UTF-16LE BOM: {path}")
    if raw[2:4] == b"\xff\xfe":
        raise BrandingError(f"duplicate UTF-16LE BOM: {path}")
    try:
        text = raw[2:].decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise BrandingError(f"invalid UTF-16LE resource: {path}: {error}") from error

    newline = _newline_style(text, path=path)
    if newline != "\r\n":
        raise BrandingError(f"stock About resource must use CRLF: {path}")
    if text.count("<resources>") != 1 or text.count("</resources>") != 1:
        raise BrandingError(f"unexpected About resource root: {path}")

    # Validate the gesture-owning model resource but deliberately keep it stock.
    updated = _replace_single_tag(
        text,
        tag="model",
        allowed_values={"HiBy R1"},
        replacement="HiBy R1",
        path=path,
    )

    cfw_pattern = re.compile(
        rf"<{CFW_RESOURCE_KEY}>(.*?)</{CFW_RESOURCE_KEY}>", re.DOTALL
    )
    cfw_matches = list(cfw_pattern.finditer(updated))
    if len(cfw_matches) > 1:
        raise BrandingError(
            f"expected at most one <{CFW_RESOURCE_KEY}> element: {path}"
        )
    if cfw_matches:
        if cfw_matches[0].group(1) != CFW_LABEL:
            raise BrandingError(
                f"conflicting <{CFW_RESOURCE_KEY}> value in {path}: "
                f"{cfw_matches[0].group(1)!r}"
            )
    else:
        closing = "</resources>"
        closing_at = updated.find(closing)
        if closing_at < 0 or not updated[:closing_at].endswith(newline):
            raise BrandingError(f"unexpected </resources> placement: {path}")
        insertion = (
            f"  <{CFW_RESOURCE_KEY}>{CFW_LABEL}</{CFW_RESOURCE_KEY}>{newline}"
        )
        updated = updated[:closing_at] + insertion + updated[closing_at:]

    encoded = b"\xff\xfe" + updated.encode("utf-16-le")
    return FilePatch(path=path, original=raw, updated=encoded)


def _product_entry(document: object, *, path: Path) -> dict[str, object]:
    if not isinstance(document, list):
        raise BrandingError(f"config root must be an array: {path}")
    products = [
        entry
        for entry in document
        if isinstance(entry, dict) and entry.get("type") == "product"
    ]
    if len(products) != 1:
        raise BrandingError(f"expected exactly one product entry: {path}")
    return products[0]


def _validate_product(product: dict[str, object], *, path: Path) -> None:
    for key, expected in EXPECTED_PRODUCT_IDENTITY.items():
        actual = product.get(key)
        if actual != expected:
            raise BrandingError(
                f"unexpected product.{key} in {path}: {actual!r}; "
                f"expected {expected!r}"
            )


def _patch_config(path: Path) -> FilePatch:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        raise BrandingError(f"config.json must be BOM-less UTF-8: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BrandingError(f"invalid UTF-8 config: {path}: {error}") from error
    if _newline_style(text, path=path) != "\n":
        raise BrandingError(f"stock config.json must use LF: {path}")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise BrandingError(f"invalid config.json: {path}: {error}") from error

    product = _product_entry(document, path=path)
    _validate_product(product, path=path)
    current_version = product.get("version")
    if current_version != "1.6":
        raise BrandingError(
            f"unexpected product.version in {path}: {current_version!r}"
        )
    return FilePatch(path=path, original=raw, updated=raw)


def _stock_model_block(spec: LayoutSpec) -> str:
    nl = spec.newline
    lines = (
        '\t\t\t"textview":{',
        f'\t\t\t\t"name":"{MODEL_WIDGET_NAME}",',
        '\t\t\t\t"size":24,',
        f'\t\t\t\t"color":"{spec.foreground}",',
        '\t\t\t\t"type":"static|h_center",',
        '\t\t\t\t"ini":"about_dev.ini",',
        '\t\t\t\t"text":"model",',
        '\t\t\t\t"x":0,',
        '\t\t\t\t"y":167,',
        '\t\t\t\t"w":480,',
        '\t\t\t\t"h":90,',
        '\t\t\t\t"textview":true',
        '\t\t\t},',
    )
    return nl.join(lines)


def _cfw_overlay_block(spec: LayoutSpec) -> str:
    nl = spec.newline
    lines = (
        '\t\t\t"textview":{',
        f'\t\t\t\t"name":"{CFW_WIDGET_NAME}",',
        '\t\t\t\t"size":20,',
        f'\t\t\t\t"color":"{spec.foreground}",',
        '\t\t\t\t"type":"static|h_center",',
        '\t\t\t\t"ini":"about_dev.ini",',
        f'\t\t\t\t"text":"{CFW_RESOURCE_KEY}",',
        '\t\t\t\t"x":0,',
        '\t\t\t\t"y":650,',
        '\t\t\t\t"w":480,',
        '\t\t\t\t"h":30,',
        '\t\t\t\t"textview":true',
        '\t\t\t},',
    )
    return nl.join(lines)


def _patch_layout(path: Path, spec: LayoutSpec) -> FilePatch:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise BrandingError(f"layout must be BOM-less UTF-8: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BrandingError(f"invalid UTF-8 layout: {path}: {error}") from error
    if _newline_style(text, path=path) != spec.newline:
        rendered = "CRLF" if spec.newline == "\r\n" else "LF"
        raise BrandingError(f"layout must preserve stock {rendered} endings: {path}")

    stock_model_block = _stock_model_block(spec)
    if text.count(f'"name":"{MODEL_WIDGET_NAME}"') != 1:
        raise BrandingError(f"expected one stock model widget: {path}")

    overlay_block = _cfw_overlay_block(spec)
    overlay_count = text.count(f'"name":"{CFW_WIDGET_NAME}"')
    if overlay_count > 1:
        raise BrandingError(f"duplicate CFW overlay widget: {path}")
    expected_pair = stock_model_block + spec.newline + overlay_block
    if overlay_count == 1:
        if (
            text.count(stock_model_block) != 1
            or text.count(overlay_block) != 1
            or text.count(expected_pair) != 1
        ):
            raise BrandingError(f"conflicting CFW overlay widget: {path}")
        updated = text
    else:
        if f'"text":"{CFW_RESOURCE_KEY}"' in text:
            raise BrandingError(f"orphan CFW resource binding in layout: {path}")
        if text.count(stock_model_block) != 1:
            raise BrandingError(f"stock model widget structure changed: {path}")
        updated = text.replace(stock_model_block, expected_pair, 1)

    return FilePatch(path=path, original=raw, updated=updated.encode("utf-8"))


def plan_rootfs(rootfs: Path) -> list[FilePatch]:
    """Validate every required input and return the complete in-memory patch."""

    rootfs = Path(rootfs)
    if not rootfs.is_dir():
        raise BrandingError(f"rootfs directory does not exist: {rootfs}")

    patches: list[FilePatch] = []
    config_path = _require_regular_file(
        rootfs, Path("usr/resource/config.json")
    )
    patches.append(_patch_config(config_path))

    for spec in LAYOUT_SPECS:
        layout_path = _require_regular_file(rootfs, spec.relative_path)
        patches.append(_patch_layout(layout_path, spec))

    for language in LANGUAGES:
        relative = Path("usr/resource/str") / language / "about_dev.ini"
        resource_path = _require_regular_file(rootfs, relative)
        patches.append(_patch_about_resource(resource_path))

    return patches


def patch_rootfs(rootfs: Path, *, check: bool = False) -> list[Path]:
    """Apply branding, or verify it with ``check=True``.

    Planning reads and validates all 17 files before the first write.  Direct
    writes intentionally retain the modes of the extracted vendor files.
    """

    patches = plan_rootfs(rootfs)
    changed = [patch for patch in patches if patch.changed]
    if check:
        if changed:
            relative = [str(patch.path.relative_to(rootfs)) for patch in changed]
            raise BrandingError(
                "branding is not fully applied; files would change: "
                + ", ".join(relative)
            )
        return []

    # Catch a concurrent edit between validation and mutation.
    for patch in patches:
        if patch.path.read_bytes() != patch.original:
            raise BrandingError(f"file changed while branding was planned: {patch.path}")
    for patch in changed:
        patch.path.write_bytes(patch.updated)
    return [patch.path for patch in changed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "rootfs",
        type=Path,
        help="extracted R1 rootfs containing usr/resource/config.json",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that branding is already complete without writing",
    )
    args = parser.parse_args(argv)

    try:
        changed = patch_rootfs(args.rootfs, check=args.check)
    except BrandingError as error:
        print(f"branding error: {error}", file=sys.stderr)
        return 2

    if args.check:
        print(f"branding verified: {args.rootfs}")
    elif changed:
        for path in changed:
            print(f"branded: {path.relative_to(args.rootfs)}")
    else:
        print(f"already branded: {args.rootfs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
