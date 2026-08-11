from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location("r1ui", REPO_ROOT / "tools/r1ui.py")
assert MODULE_SPEC and MODULE_SPEC.loader
r1ui = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = r1ui
MODULE_SPEC.loader.exec_module(r1ui)


SYNTHETIC_LAYOUT = r"""
{
  "viewgroup": {
    "viewgroup": {
      "imageview": {
        "name": "relative_touch",
        "img_path": "icons\\red.png",
        "x": 2, "y": 3,
        "touch_x": 0, "touch_y": 0, "touch_w": 20, "touch_h": 20,
        "imageview": true
      },
      "textview": {
        "name": "translated",
        "ini": "screen.ini", "text": "hello",
        "size": 12, "x": 20, "y": 0, "w": 60, "h": 20,
        "textview": true
      },
      "name": "first_group", "x": 10, "y": 20, "w": 100, "h": 40,
      "viewgroup": true
    },
    "viewgroup": {
      "imageview": {
        "name": "outer_touch",
        "touch_x": 70, "touch_y": 80, "touch_w": 30, "touch_h": 20,
        "imageview": true
      },
      "name": "second_group", "x": 0, "y": 80, "w": 100, "h": 20,
      "viewgroup": true
    },
    "name": "screen", "type": "hgl_view",
    "x": 0, "y": 0, "w": 480, "h": 200,
    "hglview_x": 0, "hglview_y": 50, "hglview_w": 480, "hglview_h": 200,
    "viewgroup": true, "add_layout": true
  }
}
"""


class R1UiTests(unittest.TestCase):
    def make_resources(self, base: Path) -> r1ui.ResourceTree:
        resource = base / "usr/resource"
        (resource / "layout/theme1").mkdir(parents=True)
        (resource / "litegui/theme1/icons").mkdir(parents=True)
        (resource / "str/english").mkdir(parents=True)
        (resource / "fonts").mkdir(parents=True)
        (resource / "layout/theme1/screen.view").write_text(
            SYNTHETIC_LAYOUT, encoding="utf-8"
        )
        (resource / "str/english/screen.ini").write_text(
            '<?Python version="3.9" encoding="utf-16"?>\n'
            "<resources><hello>Hello R1</hello></resources>\n",
            encoding="utf-16",
        )
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(
            resource / "litegui/theme1/icons/red.png"
        )
        return r1ui.ResourceTree(base)

    def test_duplicate_widget_keys_are_preserved(self) -> None:
        pairs, diagnostics = r1ui.parse_pairs(SYNTHETIC_LAYOUT)
        self.assertFalse(diagnostics)
        with tempfile.TemporaryDirectory() as temporary:
            resources = self.make_resources(Path(temporary))
            document = r1ui.parse_layout(resources.resolve_layout("screen.view"))
            root = document.root.children[0]
            self.assertEqual(["first_group", "second_group"], [node.name for node in root.children])

    def test_known_surplus_brace_is_tolerated_but_other_junk_is_not(self) -> None:
        value, diagnostics = r1ui.parse_pairs('{"viewgroup": {}}\n}')
        self.assertIsInstance(value, r1ui.PairsObject)
        self.assertEqual(1, len(diagnostics))
        with self.assertRaisesRegex(RuntimeError, "trailing data"):
            r1ui.parse_pairs('{"viewgroup": {}} nope')

    def test_argb_color_order(self) -> None:
        self.assertEqual((0, 0, 0, 0x33), r1ui.parse_color("0x33000000"))
        self.assertEqual((0x32, 0x52, 0x6C, 255), r1ui.parse_color("0x32526c"))

    def test_translation_render_geometry_and_touch_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resources = self.make_resources(Path(temporary))
            renderer = r1ui.Renderer(resources)
            image = renderer.render([resources.resolve_layout("screen.view")])

            # viewport y=50 + group y=20 + image y=3; x=group 10 + image 2
            self.assertEqual((255, 0, 0, 255), image.getpixel((12, 73)))
            self.assertEqual("Hello R1", renderer._translation("screen.ini", "hello"))
            regions = {region.name: region for region in renderer.regions}
            self.assertEqual((10, 70), (regions["relative_touch"].x, regions["relative_touch"].y))
            self.assertEqual("parent-relative-inferred", regions["relative_touch"].coordinate_model)
            self.assertEqual((70, 130), (regions["outer_touch"].x, regions["outer_touch"].y))
            self.assertEqual("outer-hgl", regions["outer_touch"].coordinate_model)

    def test_asset_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resources = self.make_resources(Path(temporary))
            with self.assertRaisesRegex(RuntimeError, "unsafe asset"):
                resources.resolve_asset("..\\secret", resources.asset_root / "theme1")


if __name__ == "__main__":
    unittest.main()
