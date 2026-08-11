#!/usr/bin/env python3
"""Build a deterministic Retro Handheld theme from extracted HiBy R1 themes.

The command requires locally extracted stock resources. It never downloads,
embeds, or redistributes vendor resources by itself.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from retro_theme_integration import build


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_root",
        type=Path,
        help="directory containing the extracted light/ and dark/ themes",
    )
    parser.add_argument(
        "output_root",
        type=Path,
        help="directory to create for the Retro Handheld package",
    )
    parser.add_argument(
        "--zip",
        dest="output_zip",
        type=Path,
        help="optional deterministic ZIP output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = build(
        args.source_root.resolve(),
        args.output_root.resolve(),
        args.output_zip.resolve() if args.output_zip else None,
    )
    print(
        json.dumps(
            {
                "source_files": result.source_files,
                "output_files": result.output_files,
                "png_files": result.png_files,
                "changed_source_files": result.changed_files,
                "added_files": list(result.added_files),
                "zip_sha256": result.zip_sha256,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
