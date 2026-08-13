from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools/patch_r1_launcher.py"
MODULE_SPEC = importlib.util.spec_from_file_location("patch_r1_launcher", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
launcher = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = launcher
MODULE_SPEC.loader.exec_module(launcher)

R1UI_SPEC = importlib.util.spec_from_file_location("launcher_test_r1ui", REPO_ROOT / "tools/r1ui.py")
assert R1UI_SPEC and R1UI_SPEC.loader
r1ui = importlib.util.module_from_spec(R1UI_SPEC)
sys.modules[R1UI_SPEC.name] = r1ui
R1UI_SPEC.loader.exec_module(r1ui)


def synthetic_launcher_string(language: str) -> bytes:
    text = (
        '<?Python version="3.9.7" encoding="utf-16"?>\r\n'
        "<resources>\r\n"
        f"  <music>{language} music</music>\r\n"
        "</resources>\r\n"
    )
    return b"\xff\xfe" + text.encode("utf-16-le")


def make_rootfs(root: Path) -> tuple[object, ...]:
    patched_themes = []
    for theme in launcher.THEMES:
        raw = f"synthetic stock {theme.output_name}\n".encode()
        path = root / theme.stock_relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        patched_themes.append(
            dataclasses.replace(theme, stock_sha256=hashlib.sha256(raw).hexdigest())
        )
    for language in launcher.LANGUAGES:
        path = root / "usr/resource/str" / language / "launcher.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(synthetic_launcher_string(language))
    return tuple(patched_themes)


def parse_layout(path: Path) -> object:
    document = r1ui.parse_layout(path)
    errors = [item.message for item in document.diagnostics if item.level == "error"]
    if errors:
        raise AssertionError(errors)
    return document.root.children[0]


def groups(root: object) -> list[object]:
    return [
        child
        for child in root.children
        if child.kind == "viewgroup" and child.name.startswith("launcher_apps_vg_")
    ]


class R1LauncherTests(unittest.TestCase):
    def test_exact_stock_hashes_and_safe_mask_set(self) -> None:
        self.assertEqual(
            {
                "theme1": "31929831ac794d9b8193d75873e1a43da099e09597a051ac9df4a1bc599c56b0",
                "theme2": "8f401dfdfa94214e284d461b10596428d882ec741e4e914dfca232acfefb2c40",
                "midi-theme1": "dd643063fc8cf37a73cb64fd40a69a9279a3d4e5e48dae02cc18ebe6d1cd0c69",
            },
            {theme.output_name: theme.stock_sha256 for theme in launcher.THEMES},
        )
        self.assertEqual(0x71, launcher.DEFAULT_MASK)
        self.assertEqual(41, len(launcher.SAFE_MASKS))
        self.assertEqual(len(launcher.SAFE_MASKS), len(set(launcher.SAFE_MASKS)))
        self.assertNotIn(launcher.ALL_TILE_BITS, launcher.SAFE_MASKS)
        for mask in launcher.SAFE_MASKS:
            self.assertTrue(mask & launcher.CFW_BIT)
            self.assertIn(mask.bit_count(), range(4, 7))

        six_tile_masks = [mask for mask in launcher.SAFE_MASKS if mask.bit_count() == 6]
        optional_bits = {tile.bit for tile in launcher.TILES if tile.bit != launcher.CFW_BIT}
        self.assertEqual(6, len(six_tile_masks))
        self.assertEqual(
            optional_bits,
            {launcher.ALL_TILE_BITS ^ mask for mask in six_tile_masks},
        )
        for tile in launcher.TILES:
            self.assertTrue(any(mask & tile.bit for mask in launcher.SAFE_MASKS))
            if tile.bit != launcher.CFW_BIT:
                self.assertTrue(any(not mask & tile.bit for mask in launcher.SAFE_MASKS))

    def test_generate_is_deterministic_and_preserves_stock_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_themes = make_rootfs(root)
            stock_before = {
                theme.stock_relative_path: (root / theme.stock_relative_path).read_bytes()
                for theme in test_themes
            }
            with mock.patch.object(launcher, "THEMES", test_themes):
                changed = launcher.generate_rootfs(root)
                self.assertEqual(123 + 6 + len(launcher.LANGUAGES), len(changed))
                first_snapshot = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual((), launcher.generate_rootfs(root))
                self.assertEqual(
                    first_snapshot,
                    {
                        path.relative_to(root): path.read_bytes()
                        for path in root.rglob("*")
                        if path.is_file()
                    },
                )
                launcher.verify_rootfs(root)
                for directory in launcher._generated_directories(root):
                    self.assertEqual(
                        launcher.GENERATED_DIRECTORY_MODE,
                        stat.S_IMODE(directory.stat().st_mode),
                    )
                generated_names = {
                    path.name
                    for path in (
                        root / "usr/resource/r1-cfw/launcher/theme1"
                    ).iterdir()
                }
                self.assertIn("3f.view", generated_names)
                self.assertIn("7e.view", generated_names)
                self.assertNotIn("7f.view", generated_names)
                self.assertNotIn("3F.view", generated_names)
                self.assertNotIn("7E.view", generated_names)

            for relative, original in stock_before.items():
                self.assertEqual(original, (root / relative).read_bytes())
            for language in launcher.LANGUAGES:
                raw = (root / "usr/resource/str" / language / "launcher.ini").read_bytes()
                self.assertTrue(raw.startswith(b"\xff\xfe"))
                text = raw[2:].decode("utf-16-le")
                self.assertEqual(1, text.count(launcher.CFW_STRING_LINE))
                self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_default_special_geometry_hides_stock_secondary_tiles(self) -> None:
        expected_names = [
            "launcher_apps_vg_player",
            "launcher_apps_vg_sysset",
            "launcher_apps_vg_step",
            "launcher_apps_vg_about",
        ]
        expected_geometry = [
            (0, 0, 480, 246),
            (0, 246, 240, 246),
            (240, 246, 240, 246),
            (0, 492, 480, 258),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for theme in launcher.THEMES:
                path = base / f"{theme.output_name}.view"
                path.write_bytes(launcher.render_layout(launcher.DEFAULT_MASK, theme))
                root = parse_layout(path)
                actual_groups = groups(root)
                self.assertEqual(expected_names, [group.name for group in actual_groups])
                self.assertEqual(
                    expected_geometry,
                    [
                        tuple(group.properties[key] for key in ("x", "y", "w", "h"))
                        for group in actual_groups
                    ],
                )
                self.assertEqual(750, root.properties["h"])
                self.assertEqual(50, root.properties["scroll_min_y"])
                self.assertEqual("scroll", root.properties["flag"])
                step = actual_groups[2]
                self.assertEqual("launcher_apps_vg_step", step.name)
                self.assertEqual("launcher_apps_iv_step", step.children[0].name)
                self.assertEqual("launcher_apps_tv_step", step.children[1].name)
                self.assertEqual(
                    "launcher\\cfw.png", step.children[0].properties["img_path"]
                )
                self.assertEqual(
                    "launcher\\cfw_s.png",
                    step.children[0].properties["img_focus_path"],
                )
                self.assertEqual("launcher.ini", step.children[1].properties["ini"])
                self.assertEqual("cfw", step.children[1].properties["text"])

    def test_all_safe_layouts_use_direct_nonoverflow_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for mask in launcher.SAFE_MASKS:
                expected_tiles = launcher.selected_tiles(mask)
                expected_geometry, content_height = launcher._tile_geometry(
                    len(expected_tiles)
                )
                for theme in launcher.THEMES:
                    path = base / f"{theme.output_name}-{mask:02X}.view"
                    rendered = launcher.render_layout(mask, theme)
                    self.assertEqual(rendered, launcher.render_layout(mask, theme))
                    path.write_bytes(rendered)
                    root = parse_layout(path)
                    actual_groups = groups(root)
                    self.assertEqual("vg_launcher_apps_hiby", root.name)
                    self.assertEqual(
                        [tile.group_name for tile in expected_tiles],
                        [group.name for group in actual_groups],
                    )
                    self.assertEqual(
                        expected_geometry,
                        tuple(
                            tuple(group.properties[key] for key in ("x", "y", "w", "h"))
                            for group in actual_groups
                        ),
                    )
                    self.assertEqual(content_height, root.properties["h"])
                    self.assertLessEqual(content_height, 750)
                    if len(expected_tiles) in (5, 6):
                        self.assertEqual(3 * 246, content_height)
                    self.assertEqual(750, root.properties["hglview_h"])
                    self.assertEqual(50, root.properties["scroll_max_y"])
                    self.assertEqual(50, root.properties["scroll_min_y"])
                    self.assertEqual(0, root.properties["scroll_max_x"])
                    self.assertEqual(-480, root.properties["scroll_min_x"])
                    self.assertEqual("scroll", root.properties["flag"])
                    for key in ("content_x", "content_y", "content_w", "content_h"):
                        self.assertNotIn(key, root.properties)

                    text = rendered.decode("utf-8")
                    for tile in launcher.TILES:
                        expected_count = 1 if mask & tile.bit else 0
                        self.assertEqual(
                            expected_count,
                            text.count(f'"name":"{tile.group_name}"'),
                        )

    def test_invalid_stock_or_string_fails_before_writing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_themes = make_rootfs(root)
            bad_stock = root / test_themes[0].stock_relative_path
            bad_stock.write_bytes(b"unexpected")
            with mock.patch.object(launcher, "THEMES", test_themes):
                with self.assertRaisesRegex(launcher.LauncherError, "SHA-256 mismatch"):
                    launcher.generate_rootfs(root)
            self.assertFalse((root / "usr/resource/r1-cfw").exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_themes = make_rootfs(root)
            bad_string = root / "usr/resource/str/english/launcher.ini"
            bad_string.write_bytes(
                bad_string.read_bytes().replace(
                    "</resources>".encode("utf-16-le"),
                    "<cfw>Wrong</cfw>\r\n</resources>".encode("utf-16-le"),
                )
            )
            with mock.patch.object(launcher, "THEMES", test_themes):
                with self.assertRaisesRegex(launcher.LauncherError, "noncanonical"):
                    launcher.generate_rootfs(root)
            self.assertFalse((root / "usr/resource/r1-cfw").exists())
            for language in launcher.LANGUAGES[1:]:
                text = (
                    root / "usr/resource/str" / language / "launcher.ini"
                ).read_bytes()[2:].decode("utf-16-le")
                self.assertNotIn("<cfw>", text)

    def test_verify_detects_missing_mutated_and_extra_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_themes = make_rootfs(root)
            with mock.patch.object(launcher, "THEMES", test_themes):
                launcher.generate_rootfs(root)
                target = root / "usr/resource/r1-cfw/launcher/theme1/71.view"
                target.write_bytes(target.read_bytes() + b"corrupt")
                with self.assertRaisesRegex(launcher.LauncherError, "resource mismatch"):
                    launcher.verify_rootfs(root)
                launcher.generate_rootfs(root)
                target.unlink()
                with self.assertRaisesRegex(launcher.LauncherError, "missing"):
                    launcher.verify_rootfs(root)
                launcher.generate_rootfs(root)
                extra = target.parent / "unsafe.view"
                extra.write_text("unexpected", encoding="utf-8")
                with self.assertRaisesRegex(launcher.LauncherError, "extra"):
                    launcher.verify_rootfs(root)
                with self.assertRaisesRegex(launcher.LauncherError, "unexpected"):
                    launcher.generate_rootfs(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_themes = make_rootfs(root)
            with mock.patch.object(launcher, "THEMES", test_themes):
                launcher.generate_rootfs(root)
                target = root / "usr/resource/r1-cfw/launcher/theme1/71.view"
                target.chmod(0o600)
                with self.assertRaisesRegex(launcher.LauncherError, "resource mismatch"):
                    launcher.verify_rootfs(root)
                launcher.generate_rootfs(root)
                target.parent.chmod(0o700)
                with self.assertRaisesRegex(launcher.LauncherError, "mode mismatch"):
                    launcher.verify_rootfs(root)

    def test_mask_validation_and_config_recovery(self) -> None:
        for mask in (0x00, 0x20, 0x70, 0x51, 0x7F, 0xFF):
            with self.assertRaises(launcher.LauncherError):
                launcher.validate_mask(mask)
        for mask in launcher.SAFE_MASKS:
            launcher.validate_mask(mask)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            missing = base / "missing.conf"
            self.assertEqual(0x71, launcher.select_config(missing))
            valid = base / "launcher.conf"
            valid.write_text("# persisted by r1-cfw-ui\nlauncher_mask=0x7e\n", encoding="utf-8")
            self.assertEqual(0x7E, launcher.select_config(valid))
            valid.write_text("73\n", encoding="utf-8")
            self.assertEqual(0x73, launcher.select_config(valid))
            for invalid in (
                "launcher_mask=20\n",
                "launcher_mask=70\n",
                "launcher_mask=7F\n",
                "launcher_mask=GG\n",
                "unknown=7F\n",
                "launcher_mask=73\nlauncher_mask=7E\n",
            ):
                valid.write_text(invalid, encoding="utf-8")
                self.assertEqual(0x71, launcher.select_config(valid), invalid)

    def test_select_config_cli_prints_normalized_two_digit_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "launcher.conf"
            config.write_text("launcher_mask=7e\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", [str(MODULE_PATH), "select-config", str(config)]):
                with contextlib.redirect_stdout(stdout):
                    launcher.main()
            self.assertEqual("7e\n", stdout.getvalue())

    def test_generated_icons_are_original_deterministic_theme_sized_pngs(self) -> None:
        expected = {
            "theme1": (140, 140),
            "theme2": (140, 140),
            "midi-theme1": (224, 242),
        }
        for theme in launcher.THEMES:
            normal = launcher.render_cfw_icon(theme, focused=False)
            focused = launcher.render_cfw_icon(theme, focused=True)
            self.assertEqual(normal, launcher.render_cfw_icon(theme, focused=False))
            self.assertNotEqual(normal, focused)
            self.assertTrue(normal.startswith(b"\x89PNG\r\n\x1a\n"))
            width = int.from_bytes(normal[16:20], "big")
            height = int.from_bytes(normal[20:24], "big")
            self.assertEqual(expected[theme.output_name], (width, height))

    def test_masks_and_variant_names_are_lowercase_hex(self) -> None:
        self.assertEqual("3f", launcher.mask_name(0x3F))
        self.assertEqual("7e", launcher.mask_name(0x7E))
        with self.assertRaises(launcher.LauncherError):
            launcher.mask_name(0x7F)
        names = {f"{launcher.mask_name(mask)}.view" for mask in launcher.SAFE_MASKS}
        self.assertEqual(41, len(names))
        self.assertNotIn("7f.view", names)
        for name in names:
            self.assertRegex(name, r"^[0-9a-f]{2}\.view$")


if __name__ == "__main__":
    unittest.main()
