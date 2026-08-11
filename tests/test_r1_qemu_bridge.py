from __future__ import annotations

import importlib.util
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "r1_qemu_bridge", REPO_ROOT / "tools/r1-qemu-ui/bridge.py"
)
assert MODULE_SPEC and MODULE_SPEC.loader
bridge = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = bridge
MODULE_SPEC.loader.exec_module(bridge)


class R1QemuBridgeTests(unittest.TestCase):
    def test_rgb565_active_pixels_with_1920_byte_stride(self) -> None:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            framebuffer = Path(temporary) / "framebuffer.raw"
            line = bytearray(bridge.FRAME_STRIDE)
            line[0:2] = struct.pack("<H", 0xF800)  # red
            line[2:4] = struct.pack("<H", 0x07E0)  # green
            line[4:6] = struct.pack("<H", 0x001F)  # blue
            framebuffer.write_bytes(bytes(line) * bridge.HEIGHT * 2)
            image = bridge.decode_framebuffer(framebuffer)
            self.assertEqual((255, 0, 0), image.getpixel((0, 0)))
            self.assertEqual((0, 255, 0), image.getpixel((1, 0)))
            self.assertEqual((0, 0, 255), image.getpixel((2, 0)))
            self.assertEqual((0, 0, 0), image.getpixel((479, 799)))

    def test_frame_state_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.bin"
            state.write_bytes(bridge.FRAME_STATE.pack(bridge.FRAME_STATE_MAGIC, 1, 800, 42))
            self.assertEqual((800, 42), bridge.active_yoffset(state))
            state.write_bytes(bridge.FRAME_STATE.pack(0, 1, 800, 42))
            self.assertEqual((0, 0), bridge.active_yoffset(state))

    def test_mips_o32_touch_stream(self) -> None:
        down = bridge.touch_events(123, 456, "down")
        move = bridge.touch_events(124, 457, "move")
        up = bridge.touch_events(124, 457, "up")
        self.assertEqual(8 * bridge.INPUT_EVENT.size, len(down))
        self.assertEqual(7 * bridge.INPUT_EVENT.size, len(move))
        self.assertEqual(9 * bridge.INPUT_EVENT.size, len(up))
        events = [
            bridge.INPUT_EVENT.unpack_from(down, offset)
            for offset in range(0, len(down), bridge.INPUT_EVENT.size)
        ]
        self.assertIn((0, 0, bridge.EV_ABS, bridge.ABS_MT_POSITION_X, 123), events)
        self.assertIn((0, 0, bridge.EV_ABS, bridge.ABS_MT_POSITION_Y, 456), events)
        self.assertEqual((0, 0, bridge.EV_SYN, bridge.SYN_REPORT, 0), events[-1])
        release = [
            bridge.INPUT_EVENT.unpack_from(up, offset)
            for offset in range(0, len(up), bridge.INPUT_EVENT.size)
        ]
        self.assertEqual(
            (0, 0, bridge.EV_ABS, bridge.ABS_MT_TRACKING_ID, -1),
            release[0],
        )
        self.assertIn((0, 0, bridge.EV_KEY, bridge.BTN_TOUCH, 0), release)
        self.assertEqual((0, 0, bridge.EV_SYN, bridge.SYN_REPORT, 0), release[-1])

    def test_touch_is_broadcast_to_every_guest_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "event0"
            endpoints = [Path(f"{base}.0"), Path(f"{base}.1")]
            for endpoint in endpoints:
                os.mkfifo(endpoint)
            readers = [os.open(endpoint, os.O_RDONLY | os.O_NONBLOCK) for endpoint in endpoints]
            try:
                expected = bridge.touch_events(123, 456, "down")
                self.assertEqual(
                    len(expected) * len(endpoints),
                    bridge.inject_touch(base, 123, 456, "down"),
                )
                self.assertEqual([expected, expected], [os.read(fd, 4096) for fd in readers])
            finally:
                for descriptor in readers:
                    os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
