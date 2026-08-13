from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import patch_r1_launcher as stock_launcher
import patch_r1_retro_launcher as retro_launcher
import patch_r1_retro_theme as retro_theme
import retro_theme_integration as integration


class RetroThemeIntegrationTests(unittest.TestCase):
    def test_firmware_launcher_assets_include_narrow_and_wide_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            retro = Path(temporary) / "retro"
            (retro / "litegui/launcher").mkdir(parents=True)
            integration.add_integration_assets(retro)
            self.assertEqual(
                "theme=retro-handheld\nformat=1\n",
                (retro / ".r1-theme").read_text(encoding="ascii"),
            )
            for name in (
                "music",
                "stream_media",
                "wireless",
                "book",
                "sys_set",
                "about",
                "cfw",
                "dac",
            ):
                for focused in (False, True):
                    suffix = "_s" if focused else ""
                    path = retro / f"litegui/launcher/tile_{name}_wide{suffix}.png"
                    with Image.open(path) as image:
                        self.assertEqual((464, 230), image.size)
                        image.verify()

    def test_retro_launcher_preserves_masks_and_uses_card_geometry(self) -> None:
        self.assertEqual(41, len(stock_launcher.SAFE_MASKS))
        compact = retro_launcher.render_layout(0x71).decode("utf-8")
        self.assertIn("launcher\\\\tile_music_wide.png", compact)
        self.assertIn("launcher\\\\tile_sys_set.png", compact)
        self.assertIn("launcher\\\\tile_cfw.png", compact)
        self.assertIn("launcher\\\\tile_about_wide.png", compact)
        self.assertIn('"name":"launcher_apps_vg_step"', compact)
        self.assertIn('"scroll_min_x":-480', compact)

        six_tiles = retro_launcher.render_layout(0x77).decode("utf-8")
        self.assertNotIn("_wide.png", six_tiles)
        self.assertIn("launcher\\\\tile_music.png", six_tiles)
        self.assertIn('"w":480', six_tiles)
        self.assertIn('"hglview_h":750', six_tiles)

    def test_strict_rootfs_paths_include_the_complete_generated_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            retro = Path(temporary) / "retro"
            (retro / "layout").mkdir(parents=True)
            (retro / "litegui").mkdir()
            (retro / "layout/a.view").write_text("{}\n", encoding="ascii")
            (retro / "litegui/a.png").write_bytes(b"png")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                retro_theme.print_expected_paths(retro)
            self.assertEqual(
                [
                    "usr/resource/r1-cfw/themes",
                    "usr/resource/r1-cfw/themes/retro",
                    "usr/resource/r1-cfw/themes/retro/layout",
                    "usr/resource/r1-cfw/themes/retro/layout/a.view",
                    "usr/resource/r1-cfw/themes/retro/litegui",
                    "usr/resource/r1-cfw/themes/retro/litegui/a.png",
                ],
                output.getvalue().splitlines(),
            )

    def test_release_pipeline_generates_and_independently_verifies_theme(self) -> None:
        source = (TOOLS / "build-cfw-0.1.sh").read_text(encoding="utf-8")
        self.assertIn('"$retro_theme_tool" generate', source)
        self.assertIn('"$retro_theme_tool" verify', source)
        self.assertIn('"$retro_launcher_tool" generate', source)
        self.assertIn('"$retro_launcher_tool" verify', source)
        self.assertIn("for theme in theme1 theme2 midi-theme1 retro", source)
        self.assertIn("expected-paths", source)
        self.assertLess(
            source.index('patch_r1_launcher.py" generate'),
            source.index('"$retro_theme_tool" generate'),
        )


if __name__ == "__main__":
    unittest.main()
