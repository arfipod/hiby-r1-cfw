from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools/patch_r1_branding.py"
MODULE_SPEC = importlib.util.spec_from_file_location("patch_r1_branding", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
branding = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = branding
MODULE_SPEC.loader.exec_module(branding)


def synthetic_about_resource() -> bytes:
    text = (
        '<?Python version="3.9.7" encoding="utf-16"?>\r\n'
        "<resources>\r\n"
        "  <model>HiBy R1</model>\r\n"
        "  <developer_mode>Developer mode</developer_mode>\r\n"
        "</resources>\r\n"
    )
    return b"\xff\xfe" + text.encode("utf-16-le")


def synthetic_config() -> bytes:
    return (
        '[\n'
        '    {"type":"slef","version":1},\n'
        '    {\n'
        '        "type":"product",\n'
        '        "company":"HiBy",\n'
        '        "device":"R1",\n'
        '        "version":"1.6",\n'
        '        "ota_name":"HiBy R1",\n'
        '        "usb_pid":"0x0101",\n'
        '        "dac_pid":"0x0004",\n'
        '        "vid":"0x32BB"\n'
        '    }\n'
        ']\n'
    ).encode("utf-8")


def synthetic_model_block(spec: object) -> str:
    nl = spec.newline
    return nl.join(
        (
            '\t\t\t"textview":{',
            f'\t\t\t\t"name":"{branding.MODEL_WIDGET_NAME}",',
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
    )


def synthetic_layout(spec: object) -> bytes:
    nl = spec.newline
    model = synthetic_model_block(spec)
    text = nl.join(
        (
            "{",
            '\t"viewgroup":{',
            '\t\t"viewgroup":{',
            model,
            '\t\t\t"textview":{',
            '\t\t\t\t"name":"about_dev_tv_developer",',
            '\t\t\t\t"visible":"hide",',
            '\t\t\t\t"textview":true',
            "\t\t\t},",
            '\t\t\t"name":"about_dev_vg_about",',
            '\t\t\t"viewgroup":true',
            "\t\t},",
            '\t\t"name":"vg_about_dev_hiby",',
            '\t\t"viewgroup":true',
            "\t}",
            "}",
            "",
        )
    )
    return text.encode("utf-8")


def make_synthetic_root(root: Path) -> None:
    config = root / "usr/resource/config.json"
    config.parent.mkdir(parents=True)
    config.write_bytes(synthetic_config())

    for spec in branding.LAYOUT_SPECS:
        path = root / spec.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(synthetic_layout(spec))

    for language in branding.LANGUAGES:
        path = root / "usr/resource/str" / language / "about_dev.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(synthetic_about_resource())


def file_snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class R1BrandingTests(unittest.TestCase):
    def test_complete_patch_preserves_formats_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            original_config = (root / "usr/resource/config.json").read_bytes()

            changed = branding.patch_rootfs(root)
            self.assertEqual(16, len(changed))

            config_path = root / "usr/resource/config.json"
            self.assertEqual(original_config, config_path.read_bytes())
            product = next(
                entry
                for entry in json.loads(config_path.read_text(encoding="utf-8"))
                if entry.get("type") == "product"
            )
            self.assertEqual("1.6", product["version"])
            self.assertEqual("HiBy", product["company"])
            self.assertEqual("R1", product["device"])
            self.assertEqual("HiBy R1", product["ota_name"])

            for language in branding.LANGUAGES:
                path = root / "usr/resource/str" / language / "about_dev.ini"
                raw = path.read_bytes()
                self.assertTrue(raw.startswith(b"\xff\xfe"))
                text = raw[2:].decode("utf-16-le")
                self.assertNotIn("\n", text.replace("\r\n", ""))
                self.assertEqual(1, text.count("<model>HiBy R1</model>"))
                self.assertEqual(
                    1,
                    text.count(
                        f"<{branding.CFW_RESOURCE_KEY}>{branding.CFW_LABEL}"
                        f"</{branding.CFW_RESOURCE_KEY}>"
                    ),
                )

            for spec in branding.LAYOUT_SPECS:
                path = root / spec.relative_path
                raw = path.read_bytes()
                text = raw.decode("utf-8")
                self.assertEqual(spec.newline, branding._newline_style(text, path=path))
                self.assertEqual(
                    1, text.count(f'"name":"{branding.MODEL_WIDGET_NAME}"')
                )
                self.assertEqual(
                    1, text.count(f'"name":"{branding.CFW_WIDGET_NAME}"')
                )
                stock_model = branding._stock_model_block(spec)
                overlay = branding._cfw_overlay_block(spec)
                self.assertEqual(1, text.count(stock_model))
                self.assertEqual(1, text.count(overlay))
                self.assertIn(
                    stock_model + spec.newline + overlay,
                    text,
                )
                self.assertNotIn('"visible":"hide"', stock_model)
                self.assertIn(f'"text":"{branding.CFW_RESOURCE_KEY}"', text)
                self.assertIn('"y":650', overlay)
                self.assertIn('"h":30', overlay)
                self.assertNotIn('"bg_color"', overlay)

            first_snapshot = file_snapshot(root)
            self.assertEqual([], branding.patch_rootfs(root))
            self.assertEqual(first_snapshot, file_snapshot(root))
            self.assertEqual([], branding.patch_rootfs(root, check=True))

    def test_check_mode_rejects_unbranded_tree_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            before = file_snapshot(root)

            with self.assertRaisesRegex(
                branding.BrandingError, "branding is not fully applied"
            ):
                branding.patch_rootfs(root, check=True)

            self.assertEqual(before, file_snapshot(root))

    def test_unexpected_locale_fails_closed_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            bad = root / "usr/resource/str/ukrainian/about_dev.ini"
            raw = bad.read_bytes()
            bad.write_bytes(
                raw.replace(
                    "HiBy R1".encode("utf-16-le"),
                    "Unknown".encode("utf-16-le"),
                )
            )
            before = file_snapshot(root)

            with self.assertRaisesRegex(branding.BrandingError, "unexpected <model>"):
                branding.patch_rootfs(root)

            self.assertEqual(before, file_snapshot(root))

    def test_product_identity_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            config = root / "usr/resource/config.json"
            config.write_bytes(config.read_bytes().replace(b'"device":"R1"', b'"device":"R2"'))
            before = file_snapshot(root)

            with self.assertRaisesRegex(branding.BrandingError, "product.device"):
                branding.patch_rootfs(root)

            self.assertEqual(before, file_snapshot(root))

    def test_conflicting_existing_overlay_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            branding.patch_rootfs(root)
            layout = root / branding.LAYOUT_SPECS[0].relative_path
            layout.write_bytes(
                layout.read_bytes().replace(
                    b'"text":"cfw_label"', b'"text":"wrong_label"'
                )
            )
            before_hashes = {
                path: hashlib.sha256(data).digest()
                for path, data in file_snapshot(root).items()
            }

            with self.assertRaisesRegex(branding.BrandingError, "conflicting CFW overlay"):
                branding.patch_rootfs(root)

            after_hashes = {
                path: hashlib.sha256(data).digest()
                for path, data in file_snapshot(root).items()
            }
            self.assertEqual(before_hashes, after_hashes)

    def test_modified_stock_model_block_fails_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            layout = root / branding.LAYOUT_SPECS[0].relative_path
            layout.write_bytes(
                layout.read_bytes().replace(b'"y":167', b'"y":168', 1)
            )
            before = file_snapshot(root)

            with self.assertRaisesRegex(
                branding.BrandingError, "stock model widget structure changed"
            ):
                branding.patch_rootfs(root)

            self.assertEqual(before, file_snapshot(root))

    def test_hidden_runtime_model_in_branded_layout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_synthetic_root(root)
            branding.patch_rootfs(root)
            layout = root / branding.LAYOUT_SPECS[0].relative_path
            layout.write_bytes(
                layout.read_bytes().replace(
                    b'\t\t\t\t"name":"about_dev_tv_model",\n',
                    b'\t\t\t\t"name":"about_dev_tv_model",\n'
                    b'\t\t\t\t"visible":"hide",\n',
                    1,
                )
            )
            before = file_snapshot(root)

            with self.assertRaisesRegex(
                branding.BrandingError, "conflicting CFW overlay widget"
            ):
                branding.patch_rootfs(root)

            self.assertEqual(before, file_snapshot(root))


if __name__ == "__main__":
    unittest.main()
