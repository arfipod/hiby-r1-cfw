from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "r1_qemu_userdata", REPO_ROOT / "tools/r1-qemu-ui/userdata.py"
)
assert MODULE_SPEC and MODULE_SPEC.loader
userdata = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = userdata
MODULE_SPEC.loader.exec_module(userdata)


def put_u32(blob: bytearray, offset: int, value: int) -> None:
    blob[offset : offset + 4] = value.to_bytes(4, "little")


def synthetic_profile(schema: int = userdata.CURRENT_SCHEMA) -> bytes:
    # Non-zero, non-repeating data makes accidental collateral edits visible.
    blob = bytearray((index * 73 + 19) & 0xFF for index in range(userdata.USER_DATA_SIZE))
    put_u32(blob, userdata.MAGIC_OFFSET, userdata.MAGIC)
    put_u32(blob, userdata.SCHEMA_OFFSET, schema)
    put_u32(blob, userdata.LANGUAGE_OFFSET, 7)
    put_u32(blob, userdata.SLEEP_TIME_OFFSET, 5)
    put_u32(blob, userdata.IDLE_SHUTDOWN_TIME_OFFSET, 9)
    put_u32(blob, userdata.LANGUAGE_ONBOARDING_PENDING_OFFSET, 1)
    put_u32(blob, userdata.IDLE_SHUTDOWN_ENABLED_OFFSET, 1)
    put_u32(blob, userdata.SLEEP_SHUTDOWN_ENABLED_OFFSET, 1)
    put_u32(blob, userdata.ONBOARDING_COMPLETE_OFFSET, 0)
    blob[
        userdata.REGION_CODE_OFFSET : userdata.REGION_CODE_OFFSET
        + userdata.REGION_CODE_SIZE
    ] = bytes(userdata.REGION_CODE_SIZE)
    blob[
        userdata.TIME_ZONE_OFFSET : userdata.TIME_ZONE_OFFSET
        + userdata.TIME_ZONE_SIZE
    ] = bytes(userdata.TIME_ZONE_SIZE)
    return bytes(blob)


class UserDataTests(unittest.TestCase):
    def test_validation_rejects_wrong_size_magic_and_schema(self) -> None:
        valid = synthetic_profile()
        with self.assertRaisesRegex(userdata.UserDataError, "exactly 2768"):
            userdata.UserDataProfile.parse(valid[:-1])

        wrong_magic = bytearray(valid)
        put_u32(wrong_magic, userdata.MAGIC_OFFSET, 0)
        with self.assertRaisesRegex(userdata.UserDataError, "magic"):
            userdata.UserDataProfile.parse(bytes(wrong_magic))

        unknown_schema = bytearray(valid)
        put_u32(unknown_schema, userdata.SCHEMA_OFFSET, 0xDEADBEEF)
        with self.assertRaisesRegex(userdata.UserDataError, "unsupported.*schema"):
            userdata.UserDataProfile.parse(bytes(unknown_schema))

    def test_prepare_changes_only_verified_profile_fields(self) -> None:
        original = synthetic_profile(userdata.FACTORY_SCHEMA)
        prepared = userdata.UserDataProfile.parse(original).prepare_for_qemu()

        changed = {
            index
            for index, pair in enumerate(zip(original, prepared.data))
            if pair[0] != pair[1]
        }
        permitted = set()
        for offset in (
            userdata.SCHEMA_OFFSET,
            userdata.LANGUAGE_OFFSET,
            userdata.LANGUAGE_ONBOARDING_PENDING_OFFSET,
            userdata.IDLE_SHUTDOWN_ENABLED_OFFSET,
            userdata.SLEEP_SHUTDOWN_ENABLED_OFFSET,
            userdata.ONBOARDING_COMPLETE_OFFSET,
        ):
            permitted.update(range(offset, offset + 4))
        permitted.update(
            range(
                userdata.REGION_CODE_OFFSET,
                userdata.REGION_CODE_OFFSET + userdata.REGION_CODE_SIZE,
            )
        )
        permitted.update(
            range(
                userdata.TIME_ZONE_OFFSET,
                userdata.TIME_ZONE_OFFSET + userdata.TIME_ZONE_SIZE,
            )
        )
        self.assertLessEqual(changed, permitted)

        details = prepared.inspect()
        self.assertEqual("0x0134fe6e", details["schema"])
        self.assertEqual("english", details["language_name"])
        self.assertFalse(details["language_onboarding_pending"])
        self.assertTrue(details["onboarding_complete"])
        self.assertEqual("US", details["region_code"])
        self.assertEqual("America/New_York", details["time_zone"])
        self.assertFalse(details["idle_shutdown"]["enabled"])
        self.assertFalse(details["sleep_shutdown"]["enabled"])
        self.assertEqual(9, details["idle_shutdown"]["time_setting"])
        self.assertEqual(5, details["sleep_shutdown"]["time_setting"])

    def test_current_profile_does_not_need_an_unrelated_header_update(self) -> None:
        original = synthetic_profile()
        prepared = userdata.UserDataProfile.parse(original).prepare_for_qemu().data
        changed = {
            index
            for index, pair in enumerate(zip(original, prepared))
            if pair[0] != pair[1]
        }
        self.assertTrue(changed)
        self.assertFalse(changed & set(range(userdata.SCHEMA_OFFSET, userdata.SCHEMA_OFFSET + 4)))

    def test_write_prepared_profile_is_separate_and_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "factory-user.ini"
            destination = root / "runtime/user.ini"
            source.write_bytes(synthetic_profile(userdata.FACTORY_SCHEMA))
            source.chmod(0o640)

            result = userdata.write_prepared_profile(source, destination)

            self.assertEqual(result.data, destination.read_bytes())
            self.assertEqual(0o640, stat.S_IMODE(destination.stat().st_mode))
            self.assertEqual(userdata.FACTORY_SCHEMA, userdata.UserDataProfile.read(source).schema)
            with self.assertRaisesRegex(userdata.UserDataError, "same"):
                userdata.write_prepared_profile(source, source)


if __name__ == "__main__":
    unittest.main()
