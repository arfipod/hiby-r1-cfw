from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "mods/r1-filectl/src/r1_filectl.c"


def b64(value: str) -> str:
    encoded = base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
    return encoded or "-"


class R1FileCtlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host C compiler unavailable")
        cls.temporary_build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary_build.name) / "r1-filectl"
        subprocess.run(
            [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-D_FORTIFY_SOURCE=2", "-fstack-protector-strong", str(SOURCE),
             "-o", str(cls.binary)],
            check=True,
            cwd=REPO_ROOT,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_build.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.sd = self.base / "sd"
        self.sd.mkdir()
        self.environment = os.environ.copy()
        self.environment.update(
            R1_FILECTL_SD_ROOT=str(self.sd),
            R1_FILECTL_ASSUME_MOUNTED="1",
            R1_FILECTL_LOCK_PATH=str(self.base / "lock"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.binary), *arguments],
            text=True,
            capture_output=True,
            check=False,
            env=self.environment,
            timeout=5,
        )

    def ok(self, *arguments: str) -> dict[str, object]:
        result = self.run_command(*arguments)
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
        lines = result.stdout.splitlines()
        self.assertEqual(1, len(lines), lines)
        return json.loads(lines[0])

    def failed(self, code: str, *arguments: str) -> dict[str, object]:
        result = self.run_command(*arguments)
        self.assertNotEqual(0, result.returncode, (result.stdout, result.stderr))
        payload = json.loads(result.stdout)
        self.assertEqual(code, payload["code"])
        self.assertTrue(payload["message"])
        return payload

    def lines(self, *arguments: str) -> list[dict[str, object]]:
        result = self.run_command(*arguments)
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_protocol_health_and_explicit_missing_mount(self) -> None:
        self.assertEqual(1, self.ok("protocol")["protocol"])
        self.assertEqual(str(self.sd), self.ok("health")["sd_root"])
        environment = self.environment.copy()
        environment.pop("R1_FILECTL_ASSUME_MOUNTED")
        mounts = self.base / "mounts"
        mounts.write_text("", encoding="ascii")
        environment["R1_FILECTL_PROC_MOUNTS"] = str(mounts)
        result = subprocess.run(
            [str(self.binary), "health"], text=True, capture_output=True,
            env=environment, timeout=5,
        )
        self.assertEqual("SD_NOT_MOUNTED", json.loads(result.stdout)["code"])

    def test_listing_is_sorted_and_private_tree_is_hidden(self) -> None:
        (self.sd / "z.mp3").write_bytes(b"z")
        (self.sd / "Alpha").mkdir()
        (self.sd / "Álbum").mkdir()
        (self.sd / ".r1-manager").mkdir()
        records = self.lines("list", "-")
        entries = [record for record in records if record.get("entry")]
        self.assertEqual(["Alpha", "z.mp3", "Álbum"],
                         [entry["name"] for entry in entries])
        self.assertEqual(3, records[-1]["count"])
        for entry in entries:
            encoded = str(entry["path_b64"])
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            self.assertEqual(entry["path"], decoded)

    def test_traversal_private_access_and_symlink_following_are_blocked(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.sd / "escape").symlink_to(outside, target_is_directory=True)
        self.failed("INVALID_PATH", "list", b64("../outside"))
        self.failed("INVALID_PATH", "stat", b64(".r1-manager/anything"))
        entry = next(r for r in self.lines("list", "-") if r.get("name") == "escape")
        self.assertEqual("symlink", entry["kind"])
        self.failed("IO_ERROR", "list", b64("escape"))

    def test_folder_rename_move_and_conflict_policies(self) -> None:
        (self.sd / "Music").mkdir()
        self.ok("mkdir", b64("Music"), b64("$(literal)"))
        source = self.sd / "Music/$(literal)/song.mp3"
        source.write_bytes(b"one")
        renamed = self.ok("rename", b64("Music/$(literal)/song.mp3"),
                          b64("01 - Song.mp3"))
        self.assertEqual("Music/$(literal)/01 - Song.mp3", renamed["path"])
        (self.sd / "Destination").mkdir()
        (self.sd / "Destination/01 - Song.mp3").write_bytes(b"existing")
        self.failed("DESTINATION_EXISTS", "move", b64(str(renamed["path"])),
                    b64("Destination"), "fail")
        kept = self.ok("move", b64(str(renamed["path"])), b64("Destination"), "keep")
        self.assertEqual("Destination/01 - Song (2).mp3", kept["path"])
        self.assertEqual(b"one", (self.sd / str(kept["path"])).read_bytes())

    def test_trash_restore_purge_empty_and_recursive_delete(self) -> None:
        album = self.sd / "Music/Artist/Album"
        album.mkdir(parents=True)
        (album / "song.mp3").write_bytes(b"song")
        trashed = self.ok("trash", b64("Music/Artist/Album"))
        records = self.lines("trash-list")
        entry = next(record for record in records if record.get("entry"))
        self.assertEqual("Music/Artist/Album", entry["original_path"])
        self.ok("restore", str(trashed["trash_id"]))
        self.assertEqual(b"song", (album / "song.mp3").read_bytes())
        second = self.ok("trash", b64("Music/Artist/Album"))
        self.ok("purge", str(second["trash_id"]))
        folder = self.sd / "NonEmpty"
        folder.mkdir()
        (folder / "child").write_text("x")
        self.failed("DIRECTORY_NOT_EMPTY", "delete", b64("NonEmpty"), "empty")
        self.ok("delete", b64("NonEmpty"), "recursive")
        for name in ("a", "b"):
            (self.sd / name).write_text(name)
            self.ok("trash", b64(name))
        self.assertEqual(2, self.ok("empty-trash")["deleted"])

    def prepare(self, destination: str, data: bytes, policy: str = "fail") -> tuple[str, Path]:
        prepared = self.ok("upload-prepare", b64(destination), str(len(data)), policy)
        staging = Path(str(prepared["staging_absolute"]))
        staging.write_bytes(data)
        return str(prepared["upload_id"]), staging

    def test_upload_is_staged_size_checked_and_committed_atomically(self) -> None:
        destination = "Music/Jorge Drexler/Taracá/11 - Las palabras.mp3"
        upload_id, staging = self.prepare(destination, b"music")
        self.assertIn(".r1-manager/incoming", str(staging))
        self.assertEqual(5, self.ok("upload-status", upload_id)["received_size"])
        self.assertEqual(destination, self.ok("upload-commit", upload_id)["path"])
        self.assertEqual(b"music", (self.sd / destination).read_bytes())
        incomplete = self.ok("upload-prepare", b64("Music/incomplete.mp3"), "10", "fail")
        Path(str(incomplete["staging_absolute"])).write_bytes(b"short")
        self.failed("UPLOAD_INCOMPLETE", "upload-commit", str(incomplete["upload_id"]))
        self.ok("upload-cancel", str(incomplete["upload_id"]))

    def test_upload_conflict_policies(self) -> None:
        destination = "Music/Album/Song.mp3"
        target = self.sd / destination
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        upload, _ = self.prepare(destination, b"skip", "skip")
        self.assertTrue(self.ok("upload-commit", upload)["skipped"])
        self.assertEqual(b"old", target.read_bytes())
        upload, _ = self.prepare(destination, b"new", "replace")
        self.ok("upload-commit", upload)
        self.assertEqual(b"new", target.read_bytes())
        upload, _ = self.prepare(destination, b"kept", "keep")
        kept = self.ok("upload-commit", upload)
        self.assertEqual("Music/Album/Song (2).mp3", kept["path"])

    def test_mips_build_when_pinned_toolchain_is_present(self) -> None:
        script = REPO_ROOT / "tools/build-r1-filectl.sh"
        self.assertTrue(os.access(script, os.X_OK))
        zig = REPO_ROOT / "work/host-tools/zig-x86_64-linux-0.16.0/zig"
        if not os.access(zig, os.X_OK):
            self.skipTest("pinned Zig toolchain unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            environment = os.environ.copy()
            environment["R1_FILECTL_ZIG_CACHE_DIR"] = str(Path(temporary) / "cache")
            result = subprocess.run([str(script), str(output)], cwd=REPO_ROOT,
                                    text=True, capture_output=True,
                                    env=environment, timeout=90)
            self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
            binary = output / "r1-filectl"
            self.assertEqual(0o755, stat.S_IMODE(binary.stat().st_mode))
            header = subprocess.run(["readelf", "-h", str(binary)], text=True,
                                    capture_output=True, check=True).stdout
            self.assertIn("MIPS", header)
            self.assertIn("mips32r2", header)


if __name__ == "__main__":
    unittest.main()
