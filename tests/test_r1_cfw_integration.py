from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "patch_r1_cfw_integration", REPO_ROOT / "tools/patch_r1_cfw_integration.py"
)
assert SPEC and SPEC.loader
integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integration)


class CFWIntegrationTests(unittest.TestCase):
    def stock_wrapper(self) -> bytes:
        path = REPO_ROOT / "work/r1-1.6/rootfs-full" / integration.WRAPPER_PATH
        if not path.is_file():
            self.skipTest("local proprietary stock wrapper is unavailable")
        data = path.read_bytes()
        self.assertEqual(integration.STOCK_SHA256, hashlib.sha256(data).hexdigest())
        return data

    def test_transform_is_deterministic_and_idempotent(self) -> None:
        stock = self.stock_wrapper()
        first = integration.transform(stock)
        self.assertEqual(first, integration.transform(first))
        self.assertIn(b"libr1-cfw-hook.so", first)
        self.assertNotEqual(stock, first)

    def test_transform_rejects_unknown_wrapper(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            integration.transform(b"#!/bin/sh\n/usr/bin/hiby_player\n")

    def test_apply_and_check_use_only_the_wrapper(self) -> None:
        stock = self.stock_wrapper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapper = root / integration.WRAPPER_PATH
            wrapper.parent.mkdir(parents=True)
            wrapper.write_bytes(stock)
            first = integration.apply(root, check=False)
            self.assertEqual(first, integration.apply(root, check=True))


if __name__ == "__main__":
    unittest.main()
