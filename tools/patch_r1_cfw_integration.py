#!/usr/bin/env python3
"""Install and verify the fail-open CFW hook in the stock player wrapper."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


WRAPPER_PATH = Path("usr/bin/hiby_player.sh")
STOCK_SHA256 = "1ed03a80239032c6d363e8bdc9b6485dacac086e2bfe61403866a7c40fd25857"
STOCK_LAUNCH = b"#/usr/bin/hiby_player &>/dev/null\n/usr/bin/hiby_player\n"
CFW_LAUNCH = (
    b"#/usr/bin/hiby_player &>/dev/null\n"
    b"if [ -r /usr/lib/libr1-cfw-hook.so ]; then\n"
    b"    LD_PRELOAD=/usr/lib/libr1-cfw-hook.so${LD_PRELOAD:+:$LD_PRELOAD} "
    b"/usr/bin/hiby_player\n"
    b"else\n"
    b"    /usr/bin/hiby_player\n"
    b"fi\n"
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(data: bytes) -> bytes:
    """Return the one accepted deterministic wrapper transformation."""

    if digest(data) == STOCK_SHA256:
        if data.count(STOCK_LAUNCH) != 1:
            raise RuntimeError("stock wrapper launch preimage is not unique")
        return data.replace(STOCK_LAUNCH, CFW_LAUNCH)
    if data.count(CFW_LAUNCH) == 1 and STOCK_LAUNCH not in data:
        return data
    raise RuntimeError(
        "unsupported hiby_player.sh input: expected exact HiBy R1 1.6 stock wrapper"
    )


PATCHED_SHA256 = "66abc5725c9ae0fd893562caf1de66719a6f8141465280a8d5b261c09ee2bc01"


def apply(root: Path, *, check: bool) -> str:
    path = root / WRAPPER_PATH
    if not path.is_file():
        raise RuntimeError(f"missing player wrapper: {path}")
    original = path.read_bytes()
    patched = transform(original)
    if check and original != patched:
        raise RuntimeError(f"CFW hook is not installed in {path}")
    if not check and original != patched:
        path.write_bytes(patched)
    result = digest(patched)
    if result != PATCHED_SHA256:
        raise RuntimeError(f"unexpected patched wrapper SHA-256: {result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="extracted rootfs")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = apply(args.root.resolve(), check=args.check)
    action = "verified" if args.check else "installed"
    print(f"{action} CFW player integration: sha256={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
