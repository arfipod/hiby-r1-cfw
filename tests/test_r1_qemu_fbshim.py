from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SHIM_SOURCE = REPO_ROOT / "tools/r1-qemu-ui/fbshim.c"
HOOK_SOURCE = REPO_ROOT / "mods/cfw-ui/src/r1_cfw_hook.c"
SOURCE_DIR = HOOK_SOURCE.parent


class R1QEMUFramebufferHandoffSourceTests(unittest.TestCase):
    def test_handoff_is_qemu_scoped_and_exec_safe(self) -> None:
        hook = HOOK_SOURCE.read_text(encoding="utf-8")
        shim = SHIM_SOURCE.read_text(encoding="utf-8")

        self.assertIn('"R1_QEMU_INHERITED_FB_FD"', hook)
        self.assertIn('getenv(R1_QEMU_FRAMEBUFFER_PATH_ENV)', hook)
        self.assertIn("execve(executable, arguments, child_environment)", hook)
        self.assertIn('"/tmp/r1-host-qemu"', hook)
        self.assertNotIn("setenv(R1_QEMU_INHERITED_FB_FD_ENV", hook)

        self.assertIn('"R1_QEMU_INHERITED_FB_FD"', shim)
        self.assertIn("fcntl((int)value, F_GETFD)", shim)
        self.assertIn("framebuffer_fd = (int)value", shim)
        self.assertIn("unsetenv(R1_INHERITED_FRAMEBUFFER_FD_ENV)", shim)
        self.assertIn("int __open_2(const char *path, int flags)", shim)
        self.assertIn("int __open64_2(const char *path, int flags)", shim)


class R1QEMUFramebufferHandoffIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.zig = REPO_ROOT / "work/host-tools/zig-x86_64-linux-0.16.0/zig"
        cls.qemu = REPO_ROOT / "work/host-tools/qemu-user/usr/bin/qemu-mipsel"
        cls.rootfs = REPO_ROOT / "work/r1-cfw-0.1/rootfs"
        if not cls.zig.is_file() or not os.access(cls.zig, os.X_OK):
            raise unittest.SkipTest("the pinned Zig toolchain is unavailable")
        if not cls.qemu.is_file() or not os.access(cls.qemu, os.X_OK):
            raise unittest.SkipTest("qemu-mipsel is unavailable")
        if not (cls.rootfs / "lib/ld.so.1").exists():
            raise unittest.SkipTest("the extracted R1 rootfs is unavailable")

        cls.temporary = tempfile.TemporaryDirectory()
        cls.build = Path(cls.temporary.name)
        cls.shim = cls.build / "libr1-qemu-fbshim.so"
        cls.probe = cls.build / "fb-handoff-probe"
        cls.callback_hook = cls.build / "libr1-cfw-hook-test.so"
        cls.callback_probe = cls.build / "cfw-callback-probe"
        cls.sidecar = cls.build / "r1-cfw-ui"
        probe_source = cls.build / "fb-handoff-probe.c"
        callback_probe_source = cls.build / "cfw-callback-probe.c"
        probe_source.write_text(
            textwrap.dedent(
                r"""
                #include <errno.h>
                #include <fcntl.h>
                #include <linux/fb.h>
                #include <stdio.h>
                #include <stdlib.h>
                #include <sys/ioctl.h>
                #include <sys/mman.h>
                #include <unistd.h>

                int main(int argc, char **argv) {
                    struct fb_var_screeninfo variable;
                    const char *handoff = getenv("R1_QEMU_INHERITED_FB_FD");
                    void *mapping;
                    int descriptor;
                    int result;
                    if (argc != 3) return 90;
                    if (argv[2][0] == 'o') {
                        descriptor = open("/dev/fb0", O_RDWR | O_CLOEXEC);
                        if (descriptor < 0) return 93;
                    } else {
                        descriptor = atoi(argv[1]);
                    }
                    errno = 0;
                    result = ioctl(descriptor, FBIOGET_VSCREENINFO, &variable);
                    if (argv[2][0] == 'v' || argv[2][0] == 'o') {
                        if (result != 0 || variable.xres != 480U ||
                            variable.yres != 800U ||
                            variable.yres_virtual != 1600U ||
                            variable.bits_per_pixel != 32U || handoff != NULL) {
                            return 91;
                        }
                        mapping = mmap(NULL, 480U * 800U * 4U * 2U,
                                       PROT_READ | PROT_WRITE, MAP_SHARED,
                                       descriptor, 0);
                        if (mapping == MAP_FAILED) return 94;
                        ((volatile unsigned char *)mapping)[0] = 0x5a;
                        if (munmap(mapping, 480U * 800U * 4U * 2U) != 0)
                            return 95;
                        if (argv[2][0] == 'o') close(descriptor);
                        return 0;
                    }
                    return result == -1 && errno == ENOTTY && handoff == NULL
                               ? 0
                               : 92;
                }
                """
            ),
            encoding="ascii",
        )
        callback_probe_source.write_text(
            textwrap.dedent(
                r"""
                #define _GNU_SOURCE
                #include <dlfcn.h>

                typedef int (*callback_t)(void);

                int main(void) {
                    callback_t callback =
                        (callback_t)dlsym(RTLD_DEFAULT,
                                          "r1_cfw_test_callback");
                    if (!callback) return 96;
                    if (callback() != 0) return 97;
                    if (callback() != 0) return 98;
                    return 0;
                }
                """
            ),
            encoding="ascii",
        )
        target = [
            str(cls.zig),
            "cc",
            "-target",
            "mipsel-linux-gnueabihf.2.22",
            "-march=mips32r2",
            "-mabi=32",
            "-Os",
            "-D_FORTIFY_SOURCE=2",
        ]
        cache_environment = os.environ.copy()
        cache_environment["ZIG_GLOBAL_CACHE_DIR"] = str(cls.build / "zig-global")
        cache_environment["ZIG_LOCAL_CACHE_DIR"] = str(cls.build / "zig-local")
        subprocess.run(
            [
                *target,
                "-fPIC",
                "-shared",
                "-Wl,-soname,libr1-qemu-fbshim.so",
                str(SHIM_SOURCE),
                "-ldl",
                "-o",
                str(cls.shim),
            ],
            check=True,
            cwd=REPO_ROOT,
            env=cache_environment,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [*target, str(probe_source), "-o", str(cls.probe)],
            check=True,
            cwd=REPO_ROOT,
            env=cache_environment,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                *target,
                "-fPIC",
                "-shared",
                "-fvisibility=hidden",
                "-DR1_CFW_HOOK_TEST",
                f'-DR1_UI_APPLICATION="{cls.sidecar}"',
                str(HOOK_SOURCE),
                "-ldl",
                "-o",
                str(cls.callback_hook),
            ],
            check=True,
            cwd=REPO_ROOT,
            env=cache_environment,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [
                *target,
                "-fno-pie",
                "-no-pie",
                str(SOURCE_DIR / "r1_cfw_ui.c"),
                str(SOURCE_DIR / "r1_cfw_data.c"),
                str(SOURCE_DIR / "r1_cfw_platform.c"),
                "-o",
                str(cls.sidecar),
            ],
            check=True,
            cwd=REPO_ROOT,
            env=cache_environment,
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            [*target, str(callback_probe_source), "-ldl", "-o", str(cls.callback_probe)],
            check=True,
            cwd=REPO_ROOT,
            env=cache_environment,
            capture_output=True,
            timeout=30,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def run_probe(
        self,
        descriptor_text: str | None,
        descriptor: int,
        expectation: str,
        *,
        pass_descriptor: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment.update(
            {
                "LD_PRELOAD": str(self.shim),
                "QEMU_CPU": "XBurstR2",
                "R1_QEMU_STATE_PATH": str(self.build / "frame-state.bin"),
                "R1_QEMU_FB_PATH": str(self.build / "opened-framebuffer.raw"),
            }
        )
        if descriptor_text is None:
            environment.pop("R1_QEMU_INHERITED_FB_FD", None)
        else:
            environment["R1_QEMU_INHERITED_FB_FD"] = descriptor_text
        return subprocess.run(
            [
                str(self.qemu),
                "-L",
                str(self.rootfs),
                str(self.probe),
                str(descriptor),
                expectation,
            ],
            check=False,
            env=environment,
            pass_fds=(descriptor,) if pass_descriptor else (),
            capture_output=True,
            timeout=10,
        )

    def test_inherited_descriptor_receives_framebuffer_ioctls_after_exec(self) -> None:
        framebuffer = self.build / "inherited-framebuffer.raw"
        with framebuffer.open("wb") as stream:
            stream.truncate(480 * 800 * 4 * 2)
        descriptor = os.open(framebuffer, os.O_RDWR)
        try:
            result = self.run_probe(
                str(descriptor), descriptor, "valid", pass_descriptor=True
            )
        finally:
            os.close(descriptor)
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))

    def test_absent_handoff_keeps_regular_file_ioctl_behavior(self) -> None:
        backing = self.build / "plain-file.raw"
        backing.touch()
        descriptor = os.open(backing, os.O_RDWR)
        try:
            result = self.run_probe(
                None, descriptor, "plain", pass_descriptor=True
            )
        finally:
            os.close(descriptor)
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))

    def test_fortified_open_is_redirected_to_the_framebuffer_backing(self) -> None:
        symbols = subprocess.run(
            ["readelf", "-Ws", str(self.probe)],
            check=True,
            text=True,
            capture_output=True,
            timeout=5,
        ).stdout
        self.assertIn("__open_2", symbols)
        result = self.run_probe(None, -1, "open")
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
        backing = self.build / "opened-framebuffer.raw"
        self.assertEqual(480 * 800 * 4 * 2, backing.stat().st_size)
        self.assertEqual(b"\x5a", backing.read_bytes()[:1])

    def test_malformed_or_closed_handoffs_fail_before_main(self) -> None:
        for malformed in ("", "+3", " 3", "3x", "2147483648", "12345"):
            with self.subTest(value=malformed):
                result = self.run_probe(malformed, 0, "valid")
                self.assertEqual(190, result.returncode, result.stderr)

    def test_real_sidecar_exec_restores_the_framebuffer_twice(self) -> None:
        framebuffer = self.build / "callback-framebuffer.raw"
        original = bytes(range(251)) * ((480 * 800 * 4 * 2) // 251 + 1)
        original = original[: 480 * 800 * 4 * 2]
        framebuffer.write_bytes(original)
        state = self.build / "callback-cfw-state.bin"
        touch = self.build / "callback-touch"
        environment = os.environ.copy()
        environment.update(
            {
                "LD_PRELOAD": f"{self.callback_hook}:{self.shim}",
                "QEMU_CPU": "XBurstR2",
                "R1_QEMU_FB_PATH": str(framebuffer),
                "R1_QEMU_TOUCH_PATH": str(touch),
                "R1_QEMU_STATE_PATH": str(self.build / "callback-frame-state.bin"),
                "R1_CFW_TEST_STATE_PATH": str(state),
                "R1_CFW_TEST_AUTO_EXIT_MS": "100",
                "R1_CFW_FB_FORMAT": "rgb565-padded",
                "R1_CFW_FB_PACKED_RGB565": "1",
                "R1_CFW_DATA_DIR": str(self.build / "callback-data"),
                "R1_CFW_INTERNAL_PATH": str(self.build / "callback-internal"),
                "R1_CFW_SD_PATH": str(self.build / "callback-sd"),
                "R1_CFW_PROC_ROOT": str(self.build / "callback-proc"),
            }
        )
        result = subprocess.run(
            [
                str(self.qemu),
                "-L",
                str(self.rootfs),
                str(self.callback_probe),
            ],
            check=False,
            env=environment,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
        self.assertTrue(state.is_file(), (result.stdout, result.stderr))
        self.assertEqual(28, state.stat().st_size)
        self.assertEqual(original, framebuffer.read_bytes())


if __name__ == "__main__":
    unittest.main()
