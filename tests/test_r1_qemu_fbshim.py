from __future__ import annotations

import os
import stat
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
        self.assertIn('open("/proc/self/fd"', hook)
        self.assertIn("syscall(SYS_getdents64", hook)
        self.assertIn("syscall(SYS_close, descriptor)", hook)
        self.assertNotIn("descriptor_limit", hook)
        self.assertNotIn("setenv(R1_QEMU_INHERITED_FB_FD_ENV", hook)

        self.assertIn('"R1_QEMU_INHERITED_FB_FD"', shim)
        self.assertIn("fcntl((int)value, F_GETFD)", shim)
        self.assertIn("register_framebuffer_fd((int)value)", shim)
        self.assertIn("static int primary_framebuffer_fd = -1", shim)
        self.assertIn("unsetenv(R1_INHERITED_FRAMEBUFFER_FD_ENV)", shim)
        self.assertIn("int __open_2(const char *path, int flags)", shim)
        self.assertIn("int __open64_2(const char *path, int flags)", shim)
        self.assertIn("static int input_grab_fd = -1", shim)
        self.assertIn("owner >= 0 && owner != fd", shim)
        self.assertIn("flush_input_queues();", shim)
        self.assertIn("errno = EBUSY", shim)


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
        cls.input_grab_probe = cls.build / "input-grab-probe"
        cls.callback_hook = cls.build / "libr1-cfw-hook-test.so"
        cls.callback_probe = cls.build / "cfw-callback-probe"
        cls.sidecar = cls.build / "r1-cfw-ui"
        probe_source = cls.build / "fb-handoff-probe.c"
        input_grab_probe_source = cls.build / "input-grab-probe.c"
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
        input_grab_probe_source.write_text(
            textwrap.dedent(
                r"""
                #include <errno.h>
                #include <fcntl.h>
                #include <linux/input.h>
                #include <stdio.h>
                #include <stdlib.h>
                #include <sys/ioctl.h>
                #include <unistd.h>

                static int send_byte(const char *base, unsigned endpoint,
                                     unsigned char value) {
                    char path[512];
                    int descriptor;
                    int length = snprintf(path, sizeof(path), "%s.%u",
                                          base, endpoint);
                    if (length < 0 || (size_t)length >= sizeof(path)) return -1;
                    descriptor = open(path, O_WRONLY | O_NONBLOCK);
                    if (descriptor < 0) return -1;
                    if (write(descriptor, &value, 1) != 1) {
                        close(descriptor);
                        return -1;
                    }
                    return close(descriptor);
                }

                static int expect_eagain(int descriptor) {
                    unsigned char value = 0;
                    ssize_t result;
                    errno = 0;
                    result = read(descriptor, &value, 1);
                    if (result != -1 || errno != EAGAIN)
                        dprintf(2, "fd=%d result=%ld errno=%d value=%u\n",
                                descriptor, (long)result, errno, value);
                    return result == -1 && errno == EAGAIN;
                }

                static int expect_byte(int descriptor, unsigned char expected) {
                    unsigned char value = 0;
                    return read(descriptor, &value, 1) == 1 && value == expected;
                }

                int main(void) {
                    const char *base = getenv("R1_QEMU_TOUCH_PATH");
                    int stock0 = open("/dev/input/event0", O_RDONLY | O_NONBLOCK);
                    int stock1 = open("/dev/input/event0", O_RDONLY | O_NONBLOCK);
                    int owner = open("/dev/input/event0", O_RDONLY | O_NONBLOCK);
                    unsigned endpoint;
                    if (!base || stock0 < 0 || stock1 < 0 || owner < 0) return 10;
                    if (ioctl(owner, EVIOCGRAB, 1) != 0) return 11;
                    errno = 0;
                    if (ioctl(stock0, EVIOCGRAB, 1) != -1 || errno != EBUSY)
                        return 12;
                    for (endpoint = 0; endpoint < 3; ++endpoint)
                        if (send_byte(base, endpoint, 0x41) != 0) return 13;
                    if (!expect_eagain(stock0)) return 141;
                    if (!expect_eagain(stock1)) return 142;
                    if (!expect_byte(owner, 0x41)) return 143;

                    /* Leave one sidecar-era byte queued on every endpoint.
                     * Releasing the grab must discard it before stock resumes. */
                    for (endpoint = 0; endpoint < 3; ++endpoint)
                        if (send_byte(base, endpoint, 0x42) != 0) return 15;
                    if (ioctl(owner, EVIOCGRAB, 0) != 0) return 16;
                    if (!expect_eagain(stock0) || !expect_eagain(stock1) ||
                        !expect_eagain(owner)) return 17;

                    for (endpoint = 0; endpoint < 3; ++endpoint)
                        if (send_byte(base, endpoint, 0x43) != 0) return 18;
                    if (!expect_byte(stock0, 0x43) ||
                        !expect_byte(stock1, 0x43) ||
                        !expect_byte(owner, 0x43)) return 19;
                    close(owner);
                    close(stock1);
                    close(stock0);
                    return 0;
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
                #include <fcntl.h>
                #include <linux/fb.h>
                #include <pthread.h>
                #include <stdint.h>
                #include <string.h>
                #include <sys/ioctl.h>
                #include <sys/mman.h>
                #include <time.h>
                #include <unistd.h>

                typedef int (*callback_t)(void);
                typedef int (*display_active_t)(void);

                struct pan_probe {
                    int framebuffer_fd;
                    int pan_result;
                    int get_result;
                    uint32_t reported_yoffset;
                    int saw_active;
                };

                static void *pan_during_sidecar(void *opaque) {
                    struct pan_probe *probe = opaque;
                    display_active_t active = (display_active_t)dlsym(
                        RTLD_DEFAULT, "r1_cfw_test_virtual_display_active");
                    struct fb_var_screeninfo variable;
                    struct timespec delay = {0, 1000000};
                    unsigned attempt;
                    if (!active) return NULL;
                    for (attempt = 0; attempt < 500U; ++attempt) {
                        if (active()) {
                            probe->saw_active = 1;
                            break;
                        }
                        nanosleep(&delay, NULL);
                    }
                    if (!probe->saw_active) return NULL;
                    memset(&variable, 0, sizeof(variable));
                    variable.yoffset = 800U;
                    probe->pan_result = ioctl(probe->framebuffer_fd,
                                              FBIOPAN_DISPLAY, &variable);
                    memset(&variable, 0, sizeof(variable));
                    probe->get_result = ioctl(probe->framebuffer_fd,
                                              FBIOGET_VSCREENINFO, &variable);
                    probe->reported_yoffset = variable.yoffset;
                    return NULL;
                }

                struct r1_dma_descriptor {
                    uint32_t source_offset;
                    uint32_t destination_physical;
                    int16_t source_stride;
                    int16_t destination_stride;
                    uint16_t line_bytes;
                    uint16_t rows;
                };

                int main(void) {
                    const size_t framebuffer_bytes = 480U * 800U * 4U * 2U;
                    const size_t dma_bytes = 6U * 1024U * 1024U;
                    struct r1_dma_descriptor transfer = {
                        0U, 0x10000000U, 1920, 1920, 1920U, 1U
                    };
                    callback_t callback =
                        (callback_t)dlsym(RTLD_DEFAULT,
                                          "r1_cfw_test_callback");
                    unsigned char *framebuffer;
                    unsigned char *dma;
                    unsigned char preserved[1920];
                    int framebuffer_fd;
                    int dma_fd;
                    unsigned lifecycle;
                    pthread_t pan_thread;
                    struct pan_probe pan = {-1, -99, -99, 0U, 0};
                    struct fb_var_screeninfo final_variable;
                    if (!callback) return 96;
                    framebuffer_fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
                    if (framebuffer_fd < 0) return 99;
                    framebuffer = mmap(NULL, framebuffer_bytes,
                                       PROT_READ | PROT_WRITE, MAP_SHARED,
                                       framebuffer_fd, 0);
                    if (framebuffer == MAP_FAILED) return 100;
                    memcpy(preserved, framebuffer, sizeof(preserved));
                    dma_fd = open("/dev/sa_hgl_dma", O_RDWR | O_CLOEXEC);
                    if (dma_fd < 0) return 101;
                    dma = mmap(NULL, dma_bytes, PROT_READ | PROT_WRITE,
                               MAP_SHARED, dma_fd, 0);
                    if (dma == MAP_FAILED) return 102;
                    memset(dma, 0xa5, transfer.line_bytes);
                    pan.framebuffer_fd = framebuffer_fd;
                    if (pthread_create(&pan_thread, NULL, pan_during_sidecar,
                                       &pan) != 0) return 108;
                    for (lifecycle = 0; lifecycle < 8U; ++lifecycle)
                        if (callback() != 0) return 97;
                    if (pthread_join(pan_thread, NULL) != 0) return 109;
                    if (!pan.saw_active || pan.pan_result != 0 ||
                        pan.get_result != 0 || pan.reported_yoffset != 800U)
                        return 110;
                    memset(&final_variable, 0, sizeof(final_variable));
                    if (ioctl(framebuffer_fd, FBIOGET_VSCREENINFO,
                              &final_variable) != 0 ||
                        final_variable.yoffset != 800U) return 111;
                    if (memcmp(framebuffer, preserved, sizeof(preserved)) != 0)
                        return 106;
                    if (write(dma_fd, &transfer, sizeof(transfer)) !=
                        (ssize_t)sizeof(transfer)) return 103;
                    if (framebuffer[0] != 0xa5 ||
                        framebuffer[transfer.line_bytes - 1U] != 0xa5)
                        return 104;
                    if (munmap(dma, dma_bytes) != 0 ||
                        munmap(framebuffer, framebuffer_bytes) != 0)
                        return 105;
                    close(dma_fd);
                    close(framebuffer_fd);
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
                "-U_FORTIFY_SOURCE",
                str(input_grab_probe_source),
                "-o",
                str(cls.input_grab_probe),
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
            [
                *target, str(callback_probe_source), "-ldl", "-pthread",
                "-o", str(cls.callback_probe),
            ],
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

    def test_input_grab_filters_and_drains_broadcast_endpoints(self) -> None:
        touch = self.build / "grab-touch"
        environment = os.environ.copy()
        environment.update(
            {
                "LD_PRELOAD": str(self.shim),
                "QEMU_CPU": "XBurstR2",
                "R1_QEMU_TOUCH_PATH": str(touch),
            }
        )
        result = subprocess.run(
            [str(self.qemu), "-L", str(self.rootfs), str(self.input_grab_probe)],
            check=False,
            env=environment,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))

    def test_malformed_or_closed_handoffs_fail_before_main(self) -> None:
        for malformed in ("", "+3", " 3", "3x", "2147483648", "12345"):
            with self.subTest(value=malformed):
                result = self.run_probe(malformed, 0, "valid")
                self.assertEqual(190, result.returncode, result.stderr)

    def test_sidecar_restores_framebuffer_and_preserves_primary_dma_mapping(self) -> None:
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
                "QEMU_LD_PREFIX": str(self.rootfs),
                "R1_QEMU_FB_PATH": str(framebuffer),
                "R1_QEMU_EXEC_WRAPPER": str(self.qemu),
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
        expected = bytearray(original)
        expected[:1920] = b"\xa5" * 1920
        self.assertEqual(bytes(expected), framebuffer.read_bytes())
        for index in range(8):
            endpoint = Path(f"{touch}.{index}")
            self.assertTrue(stat.S_ISFIFO(endpoint.stat().st_mode), endpoint)


if __name__ == "__main__":
    unittest.main()
