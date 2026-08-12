#define _GNU_SOURCE

#include "r1_cfw_protocol.h"

#include <dlfcn.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <signal.h>
#include <stddef.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

/* These guards describe the unmodified HiBy R1 1.6 executable. The preload
 * hook changes one writable callback word only after every guard matches. */
#define R1_PLAYER_DESCRIPTOR_ADDRESS ((uintptr_t)0x00892048U)
#define R1_PLAYER_CALLBACK_ADDRESS ((uintptr_t)0x00892090U)
#define R1_PLAYER_NOOP_ADDRESS ((uintptr_t)0x0053bbc0U)
#define R1_PLAYER_STOCK_ROUTE_ADDRESS ((uintptr_t)0x0053ae60U)
#define R1_PLAYER_NOOP_WORD0 0x03e00008U
#define R1_PLAYER_NOOP_WORD1 0x00001025U
#define R1_FRAMEBUFFER_MAX_BYTES (32U * 1024U * 1024U)
#ifndef R1_UI_APPLICATION
#define R1_UI_APPLICATION "/usr/bin/r1-cfw-ui"
#endif
#define R1_INPUT_SCAN_LIMIT 8U
#define R1_QEMU_FRAMEBUFFER_PATH_ENV "R1_QEMU_FB_PATH"
#define R1_QEMU_INHERITED_FB_FD_ENV "R1_QEMU_INHERITED_FB_FD"
#define R1_QEMU_EXEC_WRAPPER_ENV "R1_QEMU_EXEC_WRAPPER"
#define R1_QEMU_DEFAULT_EXEC_WRAPPER "/tmp/r1-host-qemu"

extern char **environ;

struct framebuffer_snapshot {
    struct fb_var_screeninfo variable;
    size_t bytes;
    void *mapping;
    void *copy;
};

struct touch_ownership {
    int descriptor;
    int stock_descriptor;
    int grabbed;
};

/* Linux getdents64 is used only in the post-fork child.  libc directory
 * helpers allocate memory and may take locks that belonged to another player
 * thread at fork time, while a blind close(3..OPEN_MAX) sweep can spend many
 * seconds under qemu-user. */
struct r1_linux_dirent64 {
    uint64_t inode;
    int64_t offset;
    uint16_t record_length;
    uint8_t type;
    char name[];
};

typedef int (*stock_callback_t)(void *, void *);
typedef int (*ioctl_function_t)(int, unsigned long, ...);

static volatile uint32_t *const callback_slot =
    (volatile uint32_t *)R1_PLAYER_CALLBACK_ADDRESS;
static volatile int callback_active;

struct virtual_display_state {
    atomic_flag lock;
    int active;
    uint32_t yoffset;
    uint32_t yres;
    uint32_t yres_virtual;
    uint64_t suppressed_pans;
};

static struct virtual_display_state virtual_display = {
    .lock = ATOMIC_FLAG_INIT,
};
static uint32_t installed_callback;
static int hook_installed;
static _Atomic(void *) next_ioctl_address;
static _Thread_local int resolving_next_ioctl;

static ioctl_function_t next_ioctl_function(void) {
    void *address = atomic_load_explicit(&next_ioctl_address,
                                         memory_order_acquire);
    ioctl_function_t function = NULL;
    _Static_assert(sizeof(function) == sizeof(address),
                   "function and data pointers must have equal size");
    if (!address && !resolving_next_ioctl) {
        resolving_next_ioctl = 1;
        void *candidate = dlsym(RTLD_NEXT, "ioctl");
        resolving_next_ioctl = 0;
        if (candidate) {
            void *expected = NULL;
            if (atomic_compare_exchange_strong_explicit(
                    &next_ioctl_address, &expected, candidate,
                    memory_order_release, memory_order_acquire)) {
                address = candidate;
            } else {
                address = expected;
            }
        }
    }
    if (address) memcpy(&function, &address, sizeof(function));
    return function;
}

/* Keep the raw third ABI word intact. ioctl requests are a mixture of pointer
 * and integer arguments, and consuming them through va_arg with one guessed C
 * type is undefined for the other form. The fixed-width entry point below is
 * deliberately exported under the ioctl ELF symbol while retaining a distinct
 * C identifier, so it cannot conflict with libc's variadic declaration. */
static int forward_ioctl(int descriptor, unsigned long request,
                         uintptr_t argument) {
    ioctl_function_t function = next_ioctl_function();
    if (function) return function(descriptor, request, argument);
    /* Fail open if the dynamic lookup is unavailable during early loading.
     * A direct system call preserves normal hardware ioctl behavior. */
    return (int)syscall(SYS_ioctl, descriptor, request, argument);
}

__attribute__((visibility("default")))
int r1_cfw_ioctl(int descriptor, unsigned long request, uintptr_t argument)
    __asm__("ioctl");

static void virtual_display_lock(void) {
    while (atomic_flag_test_and_set_explicit(&virtual_display.lock,
                                             memory_order_acquire)) {
        /* The critical sections contain scalar state and one fbdev ioctl. */
    }
}

static void virtual_display_unlock(void) {
    atomic_flag_clear_explicit(&virtual_display.lock, memory_order_release);
}

static void virtual_display_begin(const struct fb_var_screeninfo *variable) {
    virtual_display_lock();
    virtual_display.yoffset = variable->yoffset;
    virtual_display.yres = variable->yres;
    virtual_display.yres_virtual = variable->yres_virtual;
    virtual_display.suppressed_pans = 0;
    virtual_display.active = 1;
    virtual_display_unlock();
}

static uint32_t virtual_display_finish(int descriptor,
                                       struct fb_var_screeninfo *variable) {
    uint32_t yoffset;
    virtual_display_lock();
    yoffset = virtual_display.active ? virtual_display.yoffset
                                     : variable->yoffset;
    variable->yoffset = yoffset;
    /* Keep virtual state live through the restoring pan. A stock rendering
     * thread that reaches ioctl concurrently waits here, then sees inactive
     * state and forwards its newer request normally. */
    (void)forward_ioctl(descriptor, FBIOPAN_DISPLAY, (uintptr_t)variable);
    virtual_display.active = 0;
    virtual_display_unlock();
    return yoffset;
}

static int virtual_display_ioctl(int descriptor, unsigned long request,
                                 uintptr_t argument, int *handled) {
    int result;
    *handled = 0;
    virtual_display_lock();
    if (!virtual_display.active) {
        virtual_display_unlock();
        return 0;
    }
    if (request == (unsigned long)FBIOPAN_DISPLAY) {
        struct fb_var_screeninfo requested;
        uint32_t maximum;
        *handled = 1;
        if (!argument) {
            virtual_display_unlock();
            errno = EFAULT;
            return -1;
        }
        memcpy(&requested, (const void *)argument, sizeof(requested));
        maximum = virtual_display.yres_virtual > virtual_display.yres
                      ? virtual_display.yres_virtual - virtual_display.yres
                      : 0U;
        if (requested.yoffset > maximum) {
            virtual_display_unlock();
            errno = EINVAL;
            return -1;
        }
        virtual_display.yoffset = requested.yoffset;
        ++virtual_display.suppressed_pans;
        virtual_display_unlock();
        return 0;
    }
    if (request == (unsigned long)FBIOGET_VSCREENINFO) {
        *handled = 1;
        result = forward_ioctl(descriptor, request, argument);
        if (result == 0 && argument) {
            struct fb_var_screeninfo *reported = (void *)argument;
            reported->yoffset = virtual_display.yoffset;
        }
        virtual_display_unlock();
        return result;
    }
    virtual_display_unlock();
    return 0;
}

__attribute__((visibility("default")))
int r1_cfw_ioctl(int descriptor, unsigned long request, uintptr_t argument) {
    int handled;
    int result = virtual_display_ioctl(descriptor, request, argument, &handled);
    return handled ? result : forward_ioctl(descriptor, request, argument);
}

#ifdef R1_CFW_HOOK_TEST
__attribute__((visibility("default")))
void r1_cfw_test_set_callback_active(int active) {
    struct fb_var_screeninfo variable;
    memset(&variable, 0, sizeof(variable));
    variable.yres = 800;
    variable.yres_virtual = 1600;
    callback_active = active ? 1 : 0;
    if (active)
        virtual_display_begin(&variable);
    else {
        virtual_display_lock();
        virtual_display.active = 0;
        virtual_display_unlock();
    }
}

__attribute__((visibility("default")))
void r1_cfw_test_begin_virtual_display(uint32_t yoffset, uint32_t yres,
                                       uint32_t yres_virtual) {
    struct fb_var_screeninfo variable;
    memset(&variable, 0, sizeof(variable));
    variable.yoffset = yoffset;
    variable.yres = yres;
    variable.yres_virtual = yres_virtual;
    virtual_display_begin(&variable);
}

__attribute__((visibility("default")))
int r1_cfw_test_virtual_display_active(void) {
    int active;
    virtual_display_lock();
    active = virtual_display.active;
    virtual_display_unlock();
    return active;
}

__attribute__((visibility("default")))
uint32_t r1_cfw_test_virtual_yoffset(void) {
    uint32_t yoffset;
    virtual_display_lock();
    yoffset = virtual_display.yoffset;
    virtual_display_unlock();
    return yoffset;
}

__attribute__((visibility("default")))
uint64_t r1_cfw_test_suppressed_pans(void) {
    uint64_t count;
    virtual_display_lock();
    count = virtual_display.suppressed_pans;
    virtual_display_unlock();
    return count;
}

__attribute__((visibility("default")))
int r1_cfw_test_unfiltered_ioctl(int descriptor, unsigned long request,
                                 uintptr_t argument) {
    return forward_ioctl(descriptor, request, argument);
}
#endif

static int executable_is_player(void) {
    char path[512];
    const char *base;
    ssize_t length = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (length < 0) return 0;
    path[length] = '\0';
    base = strrchr(path, '/');
    base = base ? base + 1 : path;
    return strcmp(base, "hiby_player") == 0;
}

static int mapped_range(uintptr_t address, size_t bytes, char permission) {
    char line[512];
    FILE *stream = fopen("/proc/self/maps", "r");
    int found = 0;
    if (!stream || bytes == 0 || address + bytes < address) {
        if (stream) fclose(stream);
        return 0;
    }
    while (fgets(line, sizeof(line), stream)) {
        unsigned long start;
        unsigned long end;
        char permissions[5] = {0};
        if (sscanf(line, "%lx-%lx %4s", &start, &end, permissions) != 3)
            continue;
        if (address >= (uintptr_t)start && address + bytes <= (uintptr_t)end &&
            strchr(permissions, permission)) {
            found = 1;
            break;
        }
    }
    fclose(stream);
    return found;
}

static int stock_guards_match(void) {
    static const char descriptor_name[] = "launcher_apps_vg_step";
    const volatile uint32_t *noop =
        (const volatile uint32_t *)R1_PLAYER_NOOP_ADDRESS;
    if (!mapped_range(R1_PLAYER_DESCRIPTOR_ADDRESS, sizeof(descriptor_name),
                      'r') ||
        !mapped_range(R1_PLAYER_CALLBACK_ADDRESS, sizeof(*callback_slot),
                      'w') ||
        !mapped_range(R1_PLAYER_NOOP_ADDRESS, sizeof(uint32_t) * 2U, 'x')) {
        return 0;
    }
    if (memcmp((const void *)R1_PLAYER_DESCRIPTOR_ADDRESS, descriptor_name,
               sizeof(descriptor_name)) != 0) {
        return 0;
    }
    if (*callback_slot != (uint32_t)R1_PLAYER_NOOP_ADDRESS) return 0;
    if (noop[0] != R1_PLAYER_NOOP_WORD0 || noop[1] != R1_PLAYER_NOOP_WORD1)
        return 0;
    return 1;
}

static int parse_descriptor(const char *text) {
    char *end = NULL;
    long value;
    if (!text || !*text) return -1;
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno || !end || *end || value < 0 || value > 0x7fffffffL) return -1;
    return (int)value;
}

static int duplicate_for_exec(int descriptor) {
    int duplicate = fcntl(descriptor, F_DUPFD, 64);
    int flags;
    if (duplicate < 0) return -errno;
    flags = fcntl(duplicate, F_GETFD);
    if (flags < 0 || fcntl(duplicate, F_SETFD, flags & ~FD_CLOEXEC) < 0) {
        int saved_errno = errno;
        close(duplicate);
        return -saved_errno;
    }
    return duplicate;
}

static int descriptor_from_name(const char *name) {
    unsigned value = 0;
    const unsigned char *cursor = (const unsigned char *)name;
    if (!cursor || *cursor < '0' || *cursor > '9') return -1;
    do {
        unsigned digit = (unsigned)(*cursor - '0');
        if (value > ((unsigned)INT32_MAX - digit) / 10U) return -1;
        value = value * 10U + digit;
        ++cursor;
    } while (*cursor >= '0' && *cursor <= '9');
    if (*cursor != '\0') return -1;
    return (int)value;
}

/* Run after fork using raw syscalls and a stack buffer only.  The parent opens
 * the directory before fork, so this path cannot allocate, resolve symbols,
 * or acquire process-local locks inherited from another player thread. */
static int child_close_unneeded_descriptors(int directory_descriptor,
                                            int framebuffer, int touch) {
    _Alignas(uint64_t) unsigned char buffer[4096];
    for (;;) {
        long bytes = syscall(SYS_getdents64, directory_descriptor, buffer,
                             sizeof(buffer));
        size_t position = 0;
        if (bytes == 0) break;
        if (bytes < 0) return -errno;
        while (position < (size_t)bytes) {
            struct r1_linux_dirent64 *entry =
                (struct r1_linux_dirent64 *)(void *)(buffer + position);
            size_t minimum = offsetof(struct r1_linux_dirent64, name) + 1U;
            int descriptor;
            if (entry->record_length < minimum ||
                entry->record_length > (size_t)bytes - position) {
                return -EIO;
            }
            descriptor = descriptor_from_name(entry->name);
            if (descriptor > STDERR_FILENO &&
                descriptor != directory_descriptor &&
                descriptor != framebuffer && descriptor != touch) {
                (void)syscall(SYS_close, descriptor);
            }
            position += entry->record_length;
        }
    }
    (void)syscall(SYS_close, directory_descriptor);
    return 0;
}

/* If stock already owns EVIOCGRAB, transfer it to a fresh queue and retain a
 * duplicate of the exact stock open-file-description for restoration. This
 * prevents the player and sidecar from splitting one touch stream. */
static int transfer_stock_grab(int fresh_descriptor, const char *device_path) {
    DIR *directory = opendir("/proc/self/fd");
    struct dirent *entry;
    int retained = -1;
    if (!directory) return -1;
    while ((entry = readdir(directory)) != NULL) {
        char link_path[64];
        char target[256];
        int source = parse_descriptor(entry->d_name);
        int duplicate;
        ssize_t length;
        if (source < 0 || source == fresh_descriptor ||
            source == dirfd(directory)) {
            continue;
        }
        if (snprintf(link_path, sizeof(link_path), "/proc/self/fd/%d", source) >=
            (int)sizeof(link_path)) {
            continue;
        }
        length = readlink(link_path, target, sizeof(target) - 1);
        if (length < 0) continue;
        target[length] = '\0';
        if (strcmp(target, device_path) != 0) continue;
        duplicate = dup(source);
        if (duplicate < 0) continue;
        if (ioctl(duplicate, EVIOCGRAB, 0) == 0) {
            if (ioctl(fresh_descriptor, EVIOCGRAB, 1) == 0) {
                retained = duplicate;
                break;
            }
            (void)ioctl(duplicate, EVIOCGRAB, 1);
        }
        close(duplicate);
    }
    closedir(directory);
    return retained;
}

static int bit_is_set(const unsigned long *bits, unsigned bit) {
    unsigned width = sizeof(unsigned long) * 8U;
    return !!(bits[bit / width] & (1UL << (bit % width)));
}

static int touch_capabilities(int descriptor) {
    unsigned long bits[(ABS_MAX / (sizeof(unsigned long) * 8U)) + 1U];
    memset(bits, 0, sizeof(bits));
    if (ioctl(descriptor, EVIOCGBIT(EV_ABS, sizeof(bits)), bits) < 0)
        return 0;
    return (bit_is_set(bits, ABS_MT_POSITION_X) &&
            bit_is_set(bits, ABS_MT_POSITION_Y)) ||
           (bit_is_set(bits, ABS_X) && bit_is_set(bits, ABS_Y));
}

static int find_touch_device(char *selected_path, size_t selected_size) {
    int fallback = -1;
    char fallback_path[64] = {0};
    unsigned index;
    for (index = 0; index < R1_INPUT_SCAN_LIMIT; ++index) {
        char path[64];
        char name[256] = {0};
        int descriptor;
        if (snprintf(path, sizeof(path), "/dev/input/event%u", index) >=
            (int)sizeof(path)) {
            continue;
        }
        descriptor = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (descriptor < 0) continue;
        if (ioctl(descriptor, EVIOCGNAME(sizeof(name)), name) >= 0 &&
            strcmp(name, "hyn_ts") == 0) {
            if (fallback >= 0) close(fallback);
            snprintf(selected_path, selected_size, "%s", path);
            return descriptor;
        }
        /* event1 is the verified physical-R1 location. Accept it only when
         * its advertised axes prove it is a touchscreen, then continue the
         * scan in case an exact hyn_ts match exists at a later index. */
        if (index == 1U && touch_capabilities(descriptor)) {
            fallback = descriptor;
            snprintf(fallback_path, sizeof(fallback_path), "%s", path);
        } else {
            close(descriptor);
        }
    }
    if (fallback >= 0)
        snprintf(selected_path, selected_size, "%s", fallback_path);
    return fallback;
}

static int acquire_touch(struct touch_ownership *touch) {
    char device_path[64] = {0};
    memset(touch, 0, sizeof(*touch));
    touch->descriptor = -1;
    touch->stock_descriptor = -1;
    touch->descriptor = find_touch_device(device_path, sizeof(device_path));
    if (touch->descriptor < 0) return -ENODEV;
    if (ioctl(touch->descriptor, EVIOCGRAB, 1) == 0) {
        touch->grabbed = 1;
        return 0;
    }
    touch->stock_descriptor =
        transfer_stock_grab(touch->descriptor, device_path);
    if (touch->stock_descriptor >= 0) {
        touch->grabbed = 1;
        return 0;
    }
    close(touch->descriptor);
    touch->descriptor = -1;
    return -EBUSY;
}

static void release_touch(struct touch_ownership *touch) {
    if (touch->descriptor >= 0 && touch->grabbed)
        (void)ioctl(touch->descriptor, EVIOCGRAB, 0);
    if (touch->stock_descriptor >= 0) {
        (void)ioctl(touch->stock_descriptor, EVIOCGRAB, 1);
        close(touch->stock_descriptor);
    }
    if (touch->descriptor >= 0) close(touch->descriptor);
    touch->descriptor = -1;
    touch->stock_descriptor = -1;
    touch->grabbed = 0;
}

static int capture_framebuffer(int descriptor,
                               struct framebuffer_snapshot *snapshot) {
    struct fb_fix_screeninfo fixed;
    size_t minimum;
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->mapping = MAP_FAILED;
    if (ioctl(descriptor, FBIOGET_VSCREENINFO, &snapshot->variable) < 0 ||
        ioctl(descriptor, FBIOGET_FSCREENINFO, &fixed) < 0) {
        return -errno;
    }
    if (snapshot->variable.xres != 480U ||
        snapshot->variable.yres != 800U ||
        snapshot->variable.yres_virtual < 1600U ||
        (snapshot->variable.bits_per_pixel != 16U &&
         snapshot->variable.bits_per_pixel != 32U)) {
        return -ENOTSUP;
    }
    minimum = (size_t)fixed.line_length * snapshot->variable.yres_virtual;
    if (fixed.line_length <
            480U * snapshot->variable.bits_per_pixel / 8U ||
        fixed.smem_len < minimum || fixed.smem_len > R1_FRAMEBUFFER_MAX_BYTES) {
        return -EOVERFLOW;
    }
    snapshot->bytes = fixed.smem_len;
    snapshot->mapping = mmap(NULL, snapshot->bytes, PROT_READ | PROT_WRITE,
                             MAP_SHARED, descriptor, 0);
    if (snapshot->mapping == MAP_FAILED) return -errno;
    snapshot->copy = malloc(snapshot->bytes);
    if (!snapshot->copy) {
        munmap(snapshot->mapping, snapshot->bytes);
        snapshot->mapping = MAP_FAILED;
        return -ENOMEM;
    }
    memcpy(snapshot->copy, snapshot->mapping, snapshot->bytes);
    return 0;
}

static void restore_framebuffer(int descriptor,
                                struct framebuffer_snapshot *snapshot) {
    if (snapshot->mapping != MAP_FAILED && snapshot->copy) {
        struct fb_var_screeninfo variable = snapshot->variable;
        memcpy(snapshot->mapping, snapshot->copy, snapshot->bytes);
        (void)msync(snapshot->mapping, snapshot->bytes, MS_SYNC);
        /* hiby_player keeps rendering on other threads while the sidecar owns
         * the visible display. Reconcile fb0 with the last page flip that the
         * player was told had succeeded, rather than the entry-time page. */
        (void)virtual_display_finish(descriptor, &variable);
    }
}

static void destroy_framebuffer(struct framebuffer_snapshot *snapshot) {
    free(snapshot->copy);
    if (snapshot->mapping != MAP_FAILED)
        munmap(snapshot->mapping, snapshot->bytes);
    snapshot->copy = NULL;
    snapshot->mapping = MAP_FAILED;
}

static int launch_ui(int framebuffer, int touch) {
    char framebuffer_text[24];
    char framebuffer_environment[64];
    char touch_text[24];
    char *arguments[7];
    char **child_environment = NULL;
    const char *emulator_framebuffer_path;
    const char *emulator_exec_wrapper = NULL;
    const char *executable = R1_UI_APPLICATION;
    int descriptor_directory = -1;
    size_t environment_count = 0;
    size_t environment_index;
    size_t child_environment_index = 0;
    size_t inherited_name_length = strlen(R1_QEMU_INHERITED_FB_FD_ENV);
    int status;
    pid_t child;
    pid_t waited;
    snprintf(framebuffer_text, sizeof(framebuffer_text), "%d", framebuffer);
    snprintf(touch_text, sizeof(touch_text), "%d", touch);
    /* The physical sidecar already receives a real framebuffer descriptor and
     * must retain its stock exec environment.  Under qemu-user, however, the
     * emulator-only preload shim needs to learn that the inherited descriptor
     * is the framebuffer after exec resets its process-local bookkeeping. A
     * host qemu wrapper is also required because qemu-user cannot exec another
     * MIPS ELF unless the host has a matching binfmt_misc registration.
     * Build a private envp before fork so the multithreaded player child never
     * calls malloc/setenv between fork and exec. */
    emulator_framebuffer_path = getenv(R1_QEMU_FRAMEBUFFER_PATH_ENV);
    if (emulator_framebuffer_path && *emulator_framebuffer_path) {
        int length = snprintf(framebuffer_environment,
                              sizeof(framebuffer_environment), "%s=%d",
                              R1_QEMU_INHERITED_FB_FD_ENV, framebuffer);
        if (length < 0 || (size_t)length >= sizeof(framebuffer_environment))
            return -EOVERFLOW;
        while (environ && environ[environment_count]) ++environment_count;
        child_environment = calloc(environment_count + 2U,
                                   sizeof(*child_environment));
        if (!child_environment) return -ENOMEM;
        for (environment_index = 0; environment_index < environment_count;
             ++environment_index) {
            const char *entry = environ[environment_index];
            if (strncmp(entry, R1_QEMU_INHERITED_FB_FD_ENV,
                        inherited_name_length) == 0 &&
                entry[inherited_name_length] == '=') {
                continue;
            }
            child_environment[child_environment_index++] =
                environ[environment_index];
        }
        child_environment[child_environment_index++] = framebuffer_environment;
        child_environment[child_environment_index] = NULL;
        emulator_exec_wrapper = getenv(R1_QEMU_EXEC_WRAPPER_ENV);
        if (!emulator_exec_wrapper || !*emulator_exec_wrapper)
            emulator_exec_wrapper = R1_QEMU_DEFAULT_EXEC_WRAPPER;
        executable = emulator_exec_wrapper;
        arguments[0] = (char *)emulator_exec_wrapper;
        arguments[1] = (char *)R1_UI_APPLICATION;
        arguments[2] = "--fb-fd";
        arguments[3] = framebuffer_text;
        arguments[4] = "--touch-fd";
        arguments[5] = touch_text;
        arguments[6] = NULL;
    } else {
        arguments[0] = (char *)R1_UI_APPLICATION;
        arguments[1] = "--fb-fd";
        arguments[2] = framebuffer_text;
        arguments[3] = "--touch-fd";
        arguments[4] = touch_text;
        arguments[5] = NULL;
    }

    descriptor_directory = open("/proc/self/fd",
                                O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (descriptor_directory < 0) {
        int saved_errno = errno;
        free(child_environment);
        return -saved_errno;
    }
    child = fork();
    if (child < 0) {
        int saved_errno = errno;
        close(descriptor_directory);
        free(child_environment);
        return -saved_errno;
    }
    if (child == 0) {
        (void)prctl(PR_SET_PDEATHSIG, SIGKILL);
        if (getppid() == 1) _exit(126);
        if (child_close_unneeded_descriptors(descriptor_directory, framebuffer,
                                             touch) < 0)
            _exit(126);
        if (child_environment)
            execve(executable, arguments, child_environment);
        else
            execv(R1_UI_APPLICATION, arguments);
        _exit(127);
    }
    close(descriptor_directory);
    do {
        waited = waitpid(child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    free(child_environment);
    if (waited < 0) return -errno;
    if (!WIFEXITED(status)) return -EINTR;
    if (WEXITSTATUS(status) == 127) return -ENOEXEC;
    return WEXITSTATUS(status);
}

static int cfw_callback(void *argument0, void *argument1) {
    struct framebuffer_snapshot snapshot;
    struct touch_ownership touch;
    int framebuffer = -1;
    int child_framebuffer = -1;
    int child_touch = -1;
    int result = 0;
    if (__sync_lock_test_and_set(&callback_active, 1)) return 0;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.mapping = MAP_FAILED;
    memset(&touch, 0, sizeof(touch));
    touch.descriptor = -1;
    touch.stock_descriptor = -1;
    framebuffer = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (framebuffer < 0) goto cleanup;
    if (capture_framebuffer(framebuffer, &snapshot) < 0) goto cleanup;
    virtual_display_begin(&snapshot.variable);
    if (acquire_touch(&touch) < 0) goto restore;
    child_framebuffer = duplicate_for_exec(framebuffer);
    if (child_framebuffer < 0) {
        child_framebuffer = -1;
        goto restore;
    }
    child_touch = duplicate_for_exec(touch.descriptor);
    if (child_touch < 0) {
        child_touch = -1;
        goto restore;
    }
    result = launch_ui(child_framebuffer, child_touch);

restore:
    restore_framebuffer(framebuffer, &snapshot);
cleanup:
    if (child_touch >= 0) close(child_touch);
    if (child_framebuffer >= 0) close(child_framebuffer);
    release_touch(&touch);
    destroy_framebuffer(&snapshot);
    if (framebuffer >= 0) close(framebuffer);
    __sync_lock_release(&callback_active);

    if (result == R1_CFW_ROUTE_WIFI || result == R1_CFW_ROUTE_BLUETOOTH) {
        stock_callback_t stock_route =
            (stock_callback_t)R1_PLAYER_STOCK_ROUTE_ADDRESS;
        return stock_route(argument0, argument1);
    }
    return 0;
}

#ifdef R1_CFW_HOOK_TEST
__attribute__((visibility("default")))
int r1_cfw_test_callback(void) {
    return cfw_callback(NULL, NULL);
}
#endif

__attribute__((constructor)) static void install_hook(void) {
    if (!executable_is_player() || !stock_guards_match()) return;
    installed_callback = (uint32_t)(uintptr_t)&cfw_callback;
    __sync_synchronize();
    *callback_slot = installed_callback;
    __sync_synchronize();
    hook_installed = 1;
}

__attribute__((destructor)) static void remove_hook(void) {
    if (!hook_installed || *callback_slot != installed_callback) return;
    *callback_slot = (uint32_t)R1_PLAYER_NOOP_ADDRESS;
    __sync_synchronize();
}
