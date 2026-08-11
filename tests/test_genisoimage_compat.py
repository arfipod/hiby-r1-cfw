from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

import sys
sys.path.insert(0, str(TOOLS))

import genisoimage_compat as compat


class GenisoimageCompatibilityTests(unittest.TestCase):
    def test_argument_filter_preserves_output_and_extracts_epoch(self) -> None:
        filtered, epoch, output = compat.split_arguments(
            [
                "-creation-date",
                "1767003664",
                "-quiet",
                "-o",
                "/tmp/output.iso",
                "/tmp/tree",
            ]
        )
        self.assertEqual(1767003664, epoch)
        self.assertEqual(Path("/tmp/output.iso"), output)
        self.assertEqual(
            ["-quiet", "-o", "/tmp/output.iso", "/tmp/tree"], filtered
        )

    def test_primary_and_supplementary_volume_dates_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.iso"
            image = bytearray(20 * compat.ISO_SECTOR_SIZE)
            for sector, descriptor_type in ((16, 1), (17, 2), (18, 255)):
                offset = sector * compat.ISO_SECTOR_SIZE
                image[offset] = descriptor_type
                image[offset + 1 : offset + 6] = b"CD001"
                image[offset + 6] = 1
            path.write_bytes(image)
            normalized = compat.normalize_volume_descriptors(path, 1767003664)
            self.assertEqual(2, normalized)
            expected = compat.iso_long_timestamp(1767003664)
            result = path.read_bytes()
            for sector in (16, 17):
                base = sector * compat.ISO_SECTOR_SIZE
                for relative in compat.VOLUME_DATE_OFFSETS:
                    self.assertEqual(
                        expected,
                        result[base + relative : base + relative + len(expected)],
                    )

    def test_invalid_creation_date_contract_is_rejected(self) -> None:
        with self.assertRaises(compat.CompatibilityError):
            compat.split_arguments(["-creation-date", "nope", "tree"])
        with self.assertRaises(compat.CompatibilityError):
            compat.split_arguments(["-creation-date", "1", "tree"])


if __name__ == "__main__":
    unittest.main()
