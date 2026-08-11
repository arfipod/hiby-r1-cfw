#!/usr/bin/env python3
"""Add a native SSH switch to the HiBy R1 1.6 Developer Options page.

The stock page is generated inside ``hiby_player``; there is no standalone
Developer Options layout to extend.  This patch adds a third generated row and
routes only that row to ``/usr/bin/r1-ssh-control toggle``.  Its displayed
state is the presence of ``/usr/data/dropbear/enabled``.

The production commands are deliberately fail closed.  ``apply`` accepts only
the exact stock R1 1.6 binary and exact stock translation files.  ``verify``
accepts only the single deterministic patched result.  No fuzzy signatures or
cross-version patching are attempted.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


STOCK_BINARY_SHA256 = (
    "8398e1e1295e83b033bf7b8c39932fff3f620831f5a91682869554047b26f6b2"
)
# Filled from the exact stock binary plus BINARY_PATCHES below.  Keeping this
# as a whole-file digest makes verify mode detect changes outside our ranges.
PATCHED_BINARY_SHA256 = (
    "0d936a80b16f3cdfeeb7affd61d635233805b206f2b5be65f1f35e306762dbe5"
)
STOCK_BINARY_MODE = 0o775
TRANSLATION_MODE = 0o664

TEXT_LOAD_BASE = 0x00400000
CAVE_OFFSET = 0x35E100
CAVE_ADDRESS = TEXT_LOAD_BASE + CAVE_OFFSET
CAVE_SIZE = 0x200

KEY_ARRAY_ADDRESS = CAVE_ADDRESS + 0x000
SSH_KEY_ASCII_ADDRESS = CAVE_ADDRESS + 0x00C
SSH_KEY_WIDE_ADDRESS = CAVE_ADDRESS + 0x018
SSH_MARKER_ADDRESS = CAVE_ADDRESS + 0x030
SSH_TOGGLE_COMMAND_ADDRESS = CAVE_ADDRESS + 0x04C
KEY_SETUP_ADDRESS = CAVE_ADDRESS + 0x080
STATE_HOOK_ADDRESS = CAVE_ADDRESS + 0x0C0
CLICK_HOOK_ADDRESS = CAVE_ADDRESS + 0x120

STOCK_VOLUME_KEY_ADDRESS = 0x00784868
STOCK_SCREENSHOT_KEY_ADDRESS = 0x00784878
DEVELOPER_OPTIONS_INI_ADDRESS = 0x00784888
DEVELOPER_OPTIONS_VIEW_ADDRESS = 0x007842F4

WIDE_COMPARE_ADDRESS = 0x00410FE0
DEVELOPER_OPTIONS_REFRESH_ADDRESS = 0x00495080
ACCESS_PLT_ADDRESS = 0x00839F40
SYSTEM_PLT_ADDRESS = 0x0083AD80

ROW_LOOP_CONTINUE_ADDRESS = 0x004D1948
STATE_COMMON_ADDRESS = 0x004D1B0C
STATE_SCREENSHOT_ADDRESS = 0x004D1B7C
STATE_UNKNOWN_ADDRESS = 0x004D1BB8
CLICK_SCREENSHOT_ADDRESS = 0x004D1F44
CLICK_EPILOGUE_ADDRESS = 0x004D1F2C


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack_u32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "little")


def pack_words(*words: int) -> bytes:
    return b"".join(pack_u32(word) for word in words)


def ins_j(address: int) -> int:
    return (2 << 26) | ((address >> 2) & 0x03FFFFFF)


def ins_jal(address: int) -> int:
    return (3 << 26) | ((address >> 2) & 0x03FFFFFF)


def ins_lui(target: int, immediate: int) -> int:
    return (15 << 26) | ((target & 0x1F) << 16) | (immediate & 0xFFFF)


def ins_addiu(target: int, source: int, immediate: int) -> int:
    return (
        (9 << 26)
        | ((source & 0x1F) << 21)
        | ((target & 0x1F) << 16)
        | (immediate & 0xFFFF)
    )


def ins_sw(source: int, base: int, offset: int) -> int:
    return (
        (43 << 26)
        | ((base & 0x1F) << 21)
        | ((source & 0x1F) << 16)
        | (offset & 0xFFFF)
    )


def ins_beq(left: int, right: int, offset: int) -> int:
    return (
        (4 << 26)
        | ((left & 0x1F) << 21)
        | ((right & 0x1F) << 16)
        | (offset & 0xFFFF)
    )


def ins_bne(left: int, right: int, offset: int) -> int:
    return (
        (5 << 26)
        | ((left & 0x1F) << 21)
        | ((right & 0x1F) << 16)
        | (offset & 0xFFFF)
    )


def ins_sltiu(target: int, source: int, immediate: int) -> int:
    return (
        (11 << 26)
        | ((source & 0x1F) << 21)
        | ((target & 0x1F) << 16)
        | (immediate & 0xFFFF)
    )


def load_address_words(register: int, address: int) -> tuple[int, int]:
    """Return a sign-correct LUI/ADDIU pair for an absolute address."""

    high = (address + 0x8000) >> 16
    return ins_lui(register, high), ins_addiu(register, register, address & 0xFFFF)


def branch_distance(source_index: int, target_index: int) -> int:
    """Return a MIPS PC-relative branch displacement in instruction words."""

    return target_index - source_index - 1


def build_key_setup() -> bytes:
    # Registers are inherited from the stock generator: s0 is the current key,
    # s3 is one-past-the-end, and s2 is the translation INI filename.
    s0, s2, s3 = 16, 18, 19
    s0_hi, s0_lo = load_address_words(s0, KEY_ARRAY_ADDRESS)
    s2_hi, s2_lo = load_address_words(s2, DEVELOPER_OPTIONS_INI_ADDRESS)
    return pack_words(
        s0_hi,
        s0_lo,
        ins_addiu(s3, s0, 12),
        s2_hi,
        s2_lo,
        ins_j(ROW_LOOP_CONTINUE_ADDRESS),
        0,
    )


def build_state_hook() -> bytes:
    # This hook replaces the stock "unknown key" branch after screen_short.
    # For the two stock rows it rejoins the original code byte-for-byte.  For
    # ssh_server it maps marker presence to the stock off/on switch artwork.
    zero, v0, a0, a1, s1, s2 = 0, 2, 4, 5, 17, 18
    wide_hi, wide_lo = load_address_words(a1, SSH_KEY_WIDE_ADDRESS)
    marker_hi, marker_lo = load_address_words(a0, SSH_MARKER_ADDRESS)
    words = [
        ins_beq(v0, zero, branch_distance(0, 16)),
        0,
        ins_addiu(a0, s2, 0),
        wide_hi,
        wide_lo,
        ins_jal(WIDE_COMPARE_ADDRESS),
        0,
        ins_bne(v0, zero, branch_distance(7, 18)),
        0,
        marker_hi,
        marker_lo,
        ins_jal(ACCESS_PLT_ADDRESS),
        ins_addiu(a1, zero, 0),
        ins_sltiu(v0, v0, 1),
        ins_j(STATE_COMMON_ADDRESS),
        ins_sw(zero, s1, 0),
        ins_j(STATE_SCREENSHOT_ADDRESS),
        0,
        ins_j(STATE_UNKNOWN_ADDRESS),
        0,
    ]
    return pack_words(*words)


def build_click_hook() -> bytes:
    # The handler is synchronous only for the tiny marker toggle command.  The
    # monitor owned by r1-ssh-control performs network/service reconciliation.
    zero, v0, a0, a1, s0, s1 = 0, 2, 4, 5, 16, 17
    wide_hi, wide_lo = load_address_words(a1, SSH_KEY_WIDE_ADDRESS)
    command_hi, command_lo = load_address_words(a0, SSH_TOGGLE_COMMAND_ADDRESS)
    view_hi, view_lo = load_address_words(a1, DEVELOPER_OPTIONS_VIEW_ADDRESS)
    words = [
        ins_beq(v0, zero, branch_distance(0, 20)),
        0,
        ins_addiu(a0, s0, 0),
        wide_hi,
        wide_lo,
        ins_jal(WIDE_COMPARE_ADDRESS),
        0,
        ins_bne(v0, zero, branch_distance(7, 22)),
        0,
        command_hi,
        command_lo,
        ins_jal(SYSTEM_PLT_ADDRESS),
        0,
        ins_addiu(a0, s1, 0),
        view_hi,
        view_lo,
        ins_jal(DEVELOPER_OPTIONS_REFRESH_ADDRESS),
        0,
        ins_j(CLICK_EPILOGUE_ADDRESS),
        0,
        ins_j(CLICK_SCREENSHOT_ADDRESS),
        0,
        ins_j(CLICK_EPILOGUE_ADDRESS),
        0,
    ]
    return pack_words(*words)


def build_cave() -> bytes:
    cave = bytearray(CAVE_SIZE)
    claimed = bytearray(CAVE_SIZE)

    def place(address: int, payload: bytes, name: str) -> None:
        relative = address - CAVE_ADDRESS
        if relative < 0 or relative + len(payload) > len(cave):
            raise RuntimeError(f"{name} does not fit in the reserved code cave")
        if any(claimed[relative : relative + len(payload)]):
            raise RuntimeError(f"internal code-cave overlap at {name}")
        cave[relative : relative + len(payload)] = payload
        claimed[relative : relative + len(payload)] = b"\x01" * len(payload)

    place(
        KEY_ARRAY_ADDRESS,
        pack_words(
            STOCK_VOLUME_KEY_ADDRESS,
            STOCK_SCREENSHOT_KEY_ADDRESS,
            SSH_KEY_ASCII_ADDRESS,
        ),
        "key pointer array",
    )
    place(SSH_KEY_ASCII_ADDRESS, b"ssh_server\0", "ASCII key")
    place(
        SSH_KEY_WIDE_ADDRESS,
        "ssh_server\0".encode("utf-16le"),
        "UTF-16 key",
    )
    place(
        SSH_MARKER_ADDRESS,
        b"/usr/data/dropbear/enabled\0",
        "persistent marker path",
    )
    place(
        SSH_TOGGLE_COMMAND_ADDRESS,
        b"/usr/bin/r1-ssh-control toggle\0",
        "toggle command",
    )
    place(KEY_SETUP_ADDRESS, build_key_setup(), "row-key setup trampoline")
    place(STATE_HOOK_ADDRESS, build_state_hook(), "switch-state hook")
    place(CLICK_HOOK_ADDRESS, build_click_hook(), "click hook")
    return bytes(cave)


@dataclass(frozen=True)
class BinaryPatch:
    name: str
    offset: int
    original: bytes
    patched: bytes


BINARY_PATCHES = (
    BinaryPatch(
        "developer row-key array setup",
        0x0D193C,
        bytes.fromhex("2003b0272803b327"),
        pack_words(ins_j(KEY_SETUP_ADDRESS), 0),
    ),
    BinaryPatch(
        "developer switch-state fallback",
        0x0D1B74,
        bytes.fromhex("1000401400000000"),
        pack_words(ins_j(STATE_HOOK_ADDRESS), 0),
    ),
    BinaryPatch(
        "developer click fallback",
        0x0D1F24,
        bytes.fromhex("0700401000000000"),
        pack_words(ins_j(CLICK_HOOK_ADDRESS), 0),
    ),
    BinaryPatch(
        "SSH toggle code cave",
        CAVE_OFFSET,
        b"\0" * CAVE_SIZE,
        build_cave(),
    ),
)


def validate_patch_ranges() -> None:
    ordered = sorted(BINARY_PATCHES, key=lambda patch: patch.offset)
    for previous, current in zip(ordered, ordered[1:]):
        previous_end = previous.offset + len(previous.original)
        if previous_end > current.offset:
            raise RuntimeError(
                f"binary patch ranges overlap: {previous.name} and {current.name}"
            )
    for patch in ordered:
        if len(patch.original) != len(patch.patched):
            raise RuntimeError(f"size-changing binary patch: {patch.name}")


validate_patch_ranges()


def apply_binary_patch(data: bytes, *, enforce_hash: bool = True) -> bytes:
    if enforce_hash:
        actual = sha256_bytes(data)
        if actual != STOCK_BINARY_SHA256:
            raise RuntimeError(
                "hiby_player is not exact stock R1 1.6: "
                f"sha256={actual}, expected={STOCK_BINARY_SHA256}"
            )
    output = bytearray(data)
    for patch in BINARY_PATCHES:
        end = patch.offset + len(patch.original)
        if end > len(output):
            raise RuntimeError(f"hiby_player is too small for {patch.name}")
        actual = bytes(output[patch.offset:end])
        if actual != patch.original:
            raise RuntimeError(
                f"unexpected preimage for {patch.name} at 0x{patch.offset:x}"
            )
        output[patch.offset:end] = patch.patched
    result = bytes(output)
    if enforce_hash and sha256_bytes(result) != PATCHED_BINARY_SHA256:
        raise RuntimeError("internal error: patched hiby_player digest is not deterministic")
    return result


def verify_binary_patch(data: bytes, *, enforce_hash: bool = True) -> None:
    if enforce_hash:
        actual = sha256_bytes(data)
        if actual != PATCHED_BINARY_SHA256:
            raise RuntimeError(
                "hiby_player is not the exact SSH-toggle build: "
                f"sha256={actual}, expected={PATCHED_BINARY_SHA256}"
            )
    for patch in BINARY_PATCHES:
        end = patch.offset + len(patch.patched)
        if end > len(data) or data[patch.offset:end] != patch.patched:
            raise RuntimeError(
                f"patched bytes do not match for {patch.name} at 0x{patch.offset:x}"
            )


@dataclass(frozen=True)
class TranslationSpec:
    label: str
    stock_sha256: str
    patched_sha256: str


TRANSLATIONS = {
    "english": TranslationSpec(
        "SSH server",
        "d7dd69b1f829ce6772eab7243f98a88ab6d5cec5b676d50b190d52d872fbd2db",
        "e5c75dc38bd9738310e39198e188e6d0df1e23fddc6693728f69c31e1ee17dc7",
    ),
    "french": TranslationSpec(
        "Serveur SSH",
        "b20c3f124ca20947c438eab817887890ced82a432b963eea9b45c835a4b773f0",
        "f077bb81d1da7ac9f4988bfb640cdf9c6def4c235e80134324ff53361f4aa627",
    ),
    "german": TranslationSpec(
        "SSH-Server",
        "2852d8f9ec52f136ad9bf96043ef8d6aed9b42679be12cf18ee307d423e08177",
        "b87ea3e4abf5974b4027197fa0d7a8754777e299b1cd8e9d957c9183b8e6b032",
    ),
    "italy": TranslationSpec(
        "Server SSH",
        "2f5cba3bc5544752685720576ca8a08d076e7a23612b8c9cccee4e0fe6ed1afe",
        "55ea68ac8f99a5ca216bf564b89aa868997e26d7a076133aa8fdc4e6e4525cdd",
    ),
    "japanese": TranslationSpec(
        "SSHサーバー",
        "6da4e68302945db908f7421f6e835c4d7e35348bb98d06288f11b3a5f79592d8",
        "05608bbc6ec33a01fdf56bca2aa7695a58c0f8490b42b3eacaecdbe011e01fdc",
    ),
    "korean": TranslationSpec(
        "SSH 서버",
        "e4a3d6736abb8739271531c7cd4f55fd6e7644b24479f8b4d3f37bf4e07a437e",
        "1356e759edcd3c2e4e9a010f36950ce98a303aac305553fadd2bc4d559d0adca",
    ),
    "poland": TranslationSpec(
        "Serwer SSH",
        "b528c3f281a46ee964387e2be46d1236e23daa320321891ad03270e4b7924f00",
        "916ed750e379915941dff5523d2e3f82e017d04ca60e1190fb4d05593e3938a1",
    ),
    "russian": TranslationSpec(
        "SSH-сервер",
        "5a850e7be57cb28e09b37db6d0f4359aa60be4a806f876bb0948edeadb041ae1",
        "444ee07dc29a2de0b9f8d6d6fde8e4340055d6d4e349b82de31f60a9baa866ad",
    ),
    "simplified_chinese": TranslationSpec(
        "SSH 服务器",
        "995fe8895b170183e9916aca5a653234c800b4d1f6c90c5077c8f58823b32211",
        "0636528f7afd5022a1f73dde14625880cd23c0804e26329429b62c173e4f1de4",
    ),
    "spain": TranslationSpec(
        "Servidor SSH",
        "4afe975da22373506477b801c271517a2e3bf52ed6db48402a59b672a61944a6",
        "93e2c226ddd1521a48dbec026fd6c9d86ed18f180891054776f835c91dacc771",
    ),
    "thai": TranslationSpec(
        "เซิร์ฟเวอร์ SSH",
        "4451866451cc905be74e32eed0c361880b746a205b4713f356dd7e0c825752fb",
        "c7dbac542b16690c81f2da14c9b24fdcac5b1581e062bf5b4a74e89d843a575a",
    ),
    "traditional_chinese": TranslationSpec(
        "SSH 伺服器",
        "411cafb1c975bf727af1c91db6983af277a6717a18cad9627bdbe0f048386515",
        "44789c7be4595ae0f53e4c62c3c700d8fcec095e9bbf8e48bf3e2d1f1c942196",
    ),
    "ukrainian": TranslationSpec(
        "SSH-сервер",
        "b61b92473d01b2b6200bbb48d673d3a4a05f21839322f46c9e8f7d06c030f31c",
        "16642a9439d7e35e22475ed6ebfba88a4efbf2c2acd7b6849ca0a996feeca867",
    ),
}


def patch_translation(data: bytes, label: str) -> bytes:
    if not data.startswith(b"\xff\xfe"):
        raise RuntimeError("developer_options.ini is not UTF-16LE with a BOM")
    text = data[2:].decode("utf-16le")
    if "<ssh_server>" in text:
        raise RuntimeError("developer_options.ini already contains ssh_server")
    if text.count("</resources>") != 1:
        raise RuntimeError("developer_options.ini has an unexpected resources root")
    newline = "\r\n" if "\r\n" in text else "\n"
    entry = f"  <ssh_server>{label}</ssh_server>{newline}"
    patched = text.replace("</resources>", entry + "</resources>")
    return b"\xff\xfe" + patched.encode("utf-16le")


def translation_path(resource_root: Path, language: str) -> Path:
    return resource_root / "str" / language / "developer_options.ini"


def prepare_translation_patches(resource_root: Path) -> dict[Path, bytes]:
    prepared: dict[Path, bytes] = {}
    for language, spec in TRANSLATIONS.items():
        path = translation_path(resource_root, language)
        if not path.is_file():
            raise RuntimeError(f"missing stock translation: {path}")
        data = path.read_bytes()
        actual = sha256_bytes(data)
        if actual != spec.stock_sha256:
            raise RuntimeError(
                f"{language} developer_options.ini is not exact stock R1 1.6: "
                f"sha256={actual}, expected={spec.stock_sha256}"
            )
        patched = patch_translation(data, spec.label)
        if sha256_bytes(patched) != spec.patched_sha256:
            raise RuntimeError(f"internal error: {language} translation is not deterministic")
        prepared[path] = patched
    return prepared


def verify_translations(resource_root: Path) -> None:
    for language, spec in TRANSLATIONS.items():
        path = translation_path(resource_root, language)
        if not path.is_file():
            raise RuntimeError(f"missing patched translation: {path}")
        data = path.read_bytes()
        actual = sha256_bytes(data)
        if actual != spec.patched_sha256:
            raise RuntimeError(
                f"{language} developer_options.ini is not the exact patched file: "
                f"sha256={actual}, expected={spec.patched_sha256}"
            )
        text = data[2:].decode("utf-16le")
        expected = f"<ssh_server>{spec.label}</ssh_server>"
        if text.count(expected) != 1:
            raise RuntimeError(f"invalid ssh_server translation for {language}")


def atomic_replace(path: Path, data: bytes) -> None:
    metadata = path.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        os.utime(temporary, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def rootfs_paths(rootfs: Path) -> tuple[Path, Path]:
    return rootfs / "usr/bin/hiby_player", rootfs / "usr/resource"


def validate_modes(binary: Path, resource_root: Path) -> None:
    binary_mode = stat.S_IMODE(binary.stat().st_mode)
    if binary_mode != STOCK_BINARY_MODE:
        raise RuntimeError(
            f"unexpected hiby_player mode {binary_mode:o}; expected {STOCK_BINARY_MODE:o}"
        )
    for language in TRANSLATIONS:
        path = translation_path(resource_root, language)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != TRANSLATION_MODE:
            raise RuntimeError(
                f"unexpected mode {mode:o} for {path}; expected {TRANSLATION_MODE:o}"
            )


def apply_rootfs(rootfs: Path) -> None:
    binary, resource_root = rootfs_paths(rootfs)
    if not binary.is_file():
        raise RuntimeError(f"missing hiby_player: {binary}")
    validate_modes(binary, resource_root)

    # Validate and prepare every output before replacing any file.
    patched_binary = apply_binary_patch(binary.read_bytes())
    translations = prepare_translation_patches(resource_root)

    atomic_replace(binary, patched_binary)
    for path, data in translations.items():
        atomic_replace(path, data)
    verify_rootfs(rootfs)


def verify_rootfs(rootfs: Path) -> None:
    binary, resource_root = rootfs_paths(rootfs)
    if not binary.is_file():
        raise RuntimeError(f"missing hiby_player: {binary}")
    validate_modes(binary, resource_root)
    verify_binary_patch(binary.read_bytes())
    verify_translations(resource_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("apply", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "rootfs",
            type=Path,
            help="extracted R1 1.6 rootfs containing usr/bin and usr/resource",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "apply":
            apply_rootfs(args.rootfs)
            action = "patched and verified"
        else:
            verify_rootfs(args.rootfs)
            action = "verified"
    except (OSError, RuntimeError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    binary, _ = rootfs_paths(args.rootfs)
    print(f"{action}: {binary}")
    print(f"sha256={sha256_bytes(binary.read_bytes())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
