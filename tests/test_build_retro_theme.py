from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "build_retro_theme.py"
SPEC = importlib.util.spec_from_file_location("build_retro_theme", MODULE_PATH)
assert SPEC and SPEC.loader
retro = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = retro
SPEC.loader.exec_module(retro)


class RetroThemeGeneratorTests(unittest.TestCase):
    def test_semantic_stock_colors_map_to_retro_tokens(self) -> None:
        self.assertEqual(retro.hex_rgb(retro.PALETTE["ink"]), retro.recolor_pixel((50, 82, 108)))
        self.assertEqual(retro.hex_rgb(retro.PALETTE["canvas"]), retro.recolor_pixel((228, 245, 254)))
        self.assertEqual(retro.hex_rgb(retro.PALETTE["cyan"]), retro.recolor_pixel((16, 98, 242)))
        self.assertEqual(retro.hex_rgb(retro.PALETTE["mint"]), retro.recolor_pixel((72, 226, 228)))

    def test_dotted_header_is_deterministic(self) -> None:
        first = retro.make_dotted_header((480, 50))
        second = retro.make_dotted_header((480, 50))
        self.assertEqual((480, 50), first.size)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertNotEqual(first.getpixel((0, 0)), first.getpixel((4, 4)))

    def test_toggle_states_have_same_geometry_but_different_pixels(self) -> None:
        enabled = retro.make_toggle((36, 36), True)
        disabled = retro.make_toggle((36, 36), False)
        self.assertEqual(enabled.size, disabled.size)
        self.assertNotEqual(enabled.tobytes(), disabled.tobytes())

    def test_original_launcher_icons_are_transparent_rgba(self) -> None:
        for name in ("music", "stream", "wireless", "book", "system", "about", "cfw"):
            with self.subTest(name=name):
                icon = retro.pixel_icon(name, color=retro.PALETTE["ink"])
                self.assertEqual((140, 140), icon.size)
                self.assertEqual("RGBA", icon.mode)
                self.assertLess(icon.getchannel("A").getextrema()[0], 255)
                self.assertGreater(icon.getchannel("A").getextrema()[1], 0)

    def test_layout_color_patch_preserves_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.view"
            path.write_text(
                '{"textview":{"color":"0x32526c","focus_color":"0x1062f2","textview":true}}',
                encoding="utf-8",
            )
            retro.patch_layout_colors(path)
            result = path.read_text(encoding="utf-8")
            self.assertIn('"color":"0x171a1a"', result)
            self.assertIn('"focus_color":"0x02aaeb"', result)
            self.assertIn('"textview":true', result)

    def test_utf16_config_remains_utf16(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.ini"
            path.write_bytes(
                "<lrc_focus_color>0x1062f2</lrc_focus_color>".encode("utf-16")
            )
            retro.patch_utf16_config(path)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith((b"\xff\xfe", b"\xfe\xff")))
            self.assertIn("0x02aaeb", raw.decode("utf-16"))

    def test_deterministic_zip_is_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_text("alpha\n", encoding="utf-8")
            (source / "b.bin").write_bytes(b"\x00\x01\x02")
            first = root / "first.zip"
            second = root / "second.zip"
            first_hash = retro.deterministic_zip(source, first)
            second_hash = retro.deterministic_zip(source, second)
            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_generated_card_preserves_requested_geometry(self) -> None:
        card = retro.rounded_asset(
            (448, 124),
            retro.PALETTE["surface_bright"],
            radius=12,
            border=retro.PALETTE["border"],
        )
        self.assertEqual((448, 124), card.size)
        self.assertEqual("RGBA", card.mode)


if __name__ == "__main__":
    unittest.main()
