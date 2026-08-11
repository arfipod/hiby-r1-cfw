#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <ucontext.h>
#include <unistd.h>

/*
 * qemu-user compatibility layer for the proprietary R1 UI.
 *
 * It presents regular files as the stock 480x800 double-buffer framebuffer
 * and the 6 MiB HGL DMA arena. The driver's 16-byte row-copy descriptors are
 * executed in userspace, so a host viewer can read exact frames from the
 * framebuffer backing file. It also emulates the unaligned LDC1/SDC1 accesses
 * that the physical R1 Linux/MIPS kernel fixes up but qemu-user reports as
 * SIGBUS. This library is emulator-only and must never be shipped in a CFW.
 */

#define R1_WIDTH 480U
#define R1_HEIGHT 800U
#define R1_BYTES_PER_PIXEL 4U
#define R1_FRAME_COUNT 2U
#define R1_FRAMEBUFFER_SIZE \
    (R1_WIDTH * R1_HEIGHT * R1_BYTES_PER_PIXEL * R1_FRAME_COUNT)
#define R1_HGL_DMA_SIZE (6U * 1024U * 1024U)
#define R1_FAKE_SMEM_START 0x10000000U
#define R1_ROTATE_IOCTL 0x80044662UL
#define R1_DEFAULT_FRAMEBUFFER_PATH "/tmp/r1-qemu-framebuffer.raw"
#define R1_DEFAULT_DMA_PATH "/tmp/r1-qemu-hgl-dma"
#define R1_DEFAULT_STATE_PATH "/tmp/r1-qemu-frame-state"
#define R1_DEFAULT_TOUCH_PATH "/tmp/r1-qemu-touch.fifo"
#define R1_MAX_INPUT_FDS 16U
#define R1_FRAME_STATE_MAGIC 0x52314642U
#define R1_CRASH_STATE_MAGIC 0x52314352U
#define R1_INPUT_EVENT_MAGIC 0x52314556U

typedef void (*signal_handler_t)(int);

struct r1_dma_descriptor {
    uint32_t source_offset;
    uint32_t destination_physical;
    int16_t source_stride;
    int16_t destination_stride;
    uint16_t line_bytes;
    uint16_t rows;
};

struct r1_frame_state {
    uint32_t magic;
    uint32_t version;
    uint32_t yoffset;
    uint32_t sequence;
};

struct r1_input_event_log {
    uint32_t magic;
    int32_t fd;
    uint32_t requested;
    int32_t result;
    uint8_t payload[16];
};

_Static_assert(sizeof(struct r1_dma_descriptor) == 16,
               "unexpected R1 DMA descriptor size");

static int framebuffer_fd = -1;
static int hgl_dma_fd = -1;
static int frame_state_fd = -1;
static int input_log_fd = -1;
static int input_event_log_fd = -1;
static int crash_log_fd = -1;
static int input_fds[R1_MAX_INPUT_FDS];
static unsigned input_open_serial;
static uint32_t current_yoffset;
static uint32_t frame_sequence;
static uint8_t *framebuffer_mapping;
static size_t framebuffer_mapping_length;
static uint8_t *hgl_dma_mapping;
static size_t hgl_dma_mapping_length;
static signal_handler_t downstream_sigbus = SIG_DFL;
static signal_handler_t downstream_sigsegv = SIG_DFL;

static int (*next_open)(const char *, int, ...);
static int (*next_open64)(const char *, int, ...);
static int (*next_openat64)(int, const char *, int, ...);
static int (*next_close)(int);
static int (*next_ioctl)(int, unsigned long, ...);
static void *(*next_mmap)(void *, size_t, int, int, int, off_t);
static void *(*next_mmap64)(void *, size_t, int, int, int, off64_t);
static ssize_t (*next_write)(int, const void *, size_t);
static ssize_t (*next_read)(int, void *, size_t);
static int (*next_ftruncate)(int, off_t);
static ssize_t (*next_pwrite)(int, const void *, size_t, off_t);
static int (*next_mkfifo)(const char *, mode_t);
static signal_handler_t (*next_signal)(int, signal_handler_t);
static int (*next_sigaction)(int, const struct sigaction *, struct sigaction *);

static void resolve_symbols(void)
{
    if (next_open)
        return;
    next_open = dlsym(RTLD_NEXT, "open");
    next_open64 = dlsym(RTLD_NEXT, "open64");
    next_openat64 = dlsym(RTLD_NEXT, "openat64");
    next_close = dlsym(RTLD_NEXT, "close");
    next_ioctl = dlsym(RTLD_NEXT, "ioctl");
    next_mmap = dlsym(RTLD_NEXT, "mmap");
    next_mmap64 = dlsym(RTLD_NEXT, "mmap64");
    next_write = dlsym(RTLD_NEXT, "write");
    next_read = dlsym(RTLD_NEXT, "read");
    next_ftruncate = dlsym(RTLD_NEXT, "ftruncate");
    next_pwrite = dlsym(RTLD_NEXT, "pwrite");
    next_mkfifo = dlsym(RTLD_NEXT, "mkfifo");
    next_signal = dlsym(RTLD_NEXT, "signal");
    next_sigaction = dlsym(RTLD_NEXT, "sigaction");
    if (!next_open || !next_close || !next_ioctl || !next_mmap || !next_write ||
        !next_read || !next_ftruncate || !next_pwrite || !next_mkfifo ||
        !next_signal || !next_sigaction)
        _exit(190);
    if (!next_open64)
        next_open64 = next_open;
    if (!next_mmap64)
        next_mmap64 = (void *(*)(void *, size_t, int, int, int, off64_t))next_mmap;
}

static void copy_bytes(volatile uint8_t *destination,
                       const volatile uint8_t *source, unsigned count)
{
    while (count-- != 0)
        *destination++ = *source++;
}

static void pass_hardware_sigbus(void)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = downstream_sigbus == SIG_IGN ? SIG_DFL : downstream_sigbus;
    sigemptyset(&action.sa_mask);
    (void)next_sigaction(SIGBUS, &action, NULL);
}

static void emulate_unaligned_fpu(int signal_number, siginfo_t *information,
                                  void *opaque_context)
{
    ucontext_t *context = opaque_context;
    mcontext_t *machine;
    uint32_t pc;
    uint32_t instruction;
    uint32_t effective_address;
    unsigned opcode;
    unsigned base;
    unsigned floating_register;
    volatile uint8_t *memory;
    volatile uint8_t *first_half;
    volatile uint8_t *second_half;

    if (signal_number != SIGBUS || !information || !context ||
        information->si_code != BUS_ADRALN)
        goto downstream;
    machine = &context->uc_mcontext;
    pc = (uint32_t)machine->pc;
    instruction = *(const volatile uint32_t *)(uintptr_t)pc;
    opcode = instruction >> 26;
    base = (instruction >> 21) & 31U;
    floating_register = (instruction >> 16) & 31U;
    effective_address = (uint32_t)machine->gregs[base] +
                        (uint32_t)(int32_t)(int16_t)(instruction & 0xffffU);

    /* 0x35=LDC1, 0x3d=SDC1. The stock binary uses o32 FR=0, so each
     * double spans the low halves of an even/odd signal-frame FPR pair. */
    if ((opcode != 0x35U && opcode != 0x3dU) ||
        (uint32_t)(uintptr_t)information->si_addr != effective_address ||
        !(effective_address & 7U) || (machine->used_math & 7U) != 1U ||
        (floating_register & 1U) || floating_register == 31U)
        goto downstream;

    memory = (volatile uint8_t *)(uintptr_t)effective_address;
    first_half =
        (volatile uint8_t *)&machine->fpregs.fp_r.fp_dregs[floating_register];
    second_half = (volatile uint8_t *)&machine->fpregs.fp_r
                       .fp_dregs[floating_register + 1U];
    if (opcode == 0x3dU) {
        copy_bytes(memory, first_half, 4);
        copy_bytes(memory + 4, second_half, 4);
    } else {
        copy_bytes(first_half, memory, 4);
        copy_bytes(second_half, memory + 4, 4);
    }
    machine->pc = (greg_t)(uint32_t)(pc + 4U);
    return;

downstream:
    if (information && information->si_code <= 0) {
        if (downstream_sigbus != SIG_DFL && downstream_sigbus != SIG_IGN)
            downstream_sigbus(signal_number);
        return;
    }
    /* Reinstall the requested player/default disposition. Returning retries
     * the fault once, at which point that disposition receives it. */
    pass_hardware_sigbus();
}

static int install_alignment_emulator(void)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = emulate_unaligned_fpu;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    return next_sigaction(SIGBUS, &action, NULL);
}

static void diagnose_segv(int signal_number, siginfo_t *information,
                          void *opaque_context)
{
    ucontext_t *context = opaque_context;
    uint32_t record[16];
    struct sigaction action;
    unsigned index;
    memset(record, 0, sizeof(record));
    record[0] = R1_CRASH_STATE_MAGIC;
    record[1] = (uint32_t)signal_number;
    record[2] = information ? (uint32_t)information->si_code : 0U;
    record[3] = information ? (uint32_t)(uintptr_t)information->si_addr : 0U;
    if (context) {
        record[4] = (uint32_t)context->uc_mcontext.pc;
        record[5] = (uint32_t)context->uc_mcontext.gregs[29];
        record[6] = (uint32_t)context->uc_mcontext.gregs[31];
        record[7] = (uint32_t)context->uc_mcontext.gregs[28];
        for (index = 0; index < 8U; ++index)
            record[8U + index] = (uint32_t)context->uc_mcontext.gregs[4U + index];
    }
    if (crash_log_fd >= 0)
        (void)next_pwrite(crash_log_fd, record, sizeof(record), 0);

    if (information && information->si_code <= 0) {
        if (downstream_sigsegv != SIG_DFL && downstream_sigsegv != SIG_IGN)
            downstream_sigsegv(signal_number);
        return;
    }
    memset(&action, 0, sizeof(action));
    action.sa_handler = downstream_sigsegv == SIG_IGN ? SIG_DFL : downstream_sigsegv;
    sigemptyset(&action.sa_mask);
    (void)next_sigaction(SIGSEGV, &action, NULL);
}

static int install_segv_diagnostic(void)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = diagnose_segv;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    return next_sigaction(SIGSEGV, &action, NULL);
}

__attribute__((constructor)) static void initialize_shim(void)
{
    const char *input_log_path;
    const char *input_event_log_path;
    const char *crash_log_path;
    const char *state_path;
    resolve_symbols();
    state_path = getenv("R1_QEMU_STATE_PATH");
    if (!state_path || !*state_path)
        state_path = R1_DEFAULT_STATE_PATH;
    /* The player launches guest shell helpers which inherit LD_PRELOAD.  They
     * construct this shim too, so truncating here would erase live state from
     * the main process.  The host runner clears these files once per session. */
    frame_state_fd = next_open(state_path, O_RDWR | O_CREAT, 0600);
    input_log_path = getenv("R1_QEMU_INPUT_LOG_PATH");
    if (input_log_path && *input_log_path)
        input_log_fd = next_open(input_log_path, O_WRONLY | O_CREAT, 0600);
    input_event_log_path = getenv("R1_QEMU_INPUT_EVENT_LOG_PATH");
    if (input_event_log_path && *input_event_log_path)
        input_event_log_fd = next_open(input_event_log_path,
                                       O_WRONLY | O_CREAT | O_APPEND, 0600);
    crash_log_path = getenv("R1_QEMU_CRASH_LOG_PATH");
    if (crash_log_path && *crash_log_path)
        crash_log_fd = next_open(crash_log_path, O_RDWR | O_CREAT, 0600);
    if (install_alignment_emulator() != 0 || install_segv_diagnostic() != 0)
        _exit(190);
}

signal_handler_t signal(int signal_number, signal_handler_t requested)
{
    signal_handler_t previous;
    resolve_symbols();
    if (signal_number != SIGBUS)
    {
        if (signal_number != SIGSEGV)
            return next_signal(signal_number, requested);
        previous = downstream_sigsegv;
        downstream_sigsegv = requested;
        (void)install_segv_diagnostic();
        return previous;
    }
    previous = downstream_sigbus;
    downstream_sigbus = requested;
    (void)install_alignment_emulator();
    return previous;
}

static int open_hgl_dma_backing(void)
{
    const char *path = getenv("R1_QEMU_DMA_PATH");
    int result;
    if (!path || !*path)
        path = R1_DEFAULT_DMA_PATH;
    result = next_open(path, O_RDWR | O_CREAT, 0600);
    if (result >= 0 && next_ftruncate(result, R1_HGL_DMA_SIZE) != 0) {
        int saved_errno = errno;
        next_close(result);
        errno = saved_errno;
        return -1;
    }
    if (result >= 0)
        hgl_dma_fd = result;
    return result;
}

static int open_framebuffer_backing(void)
{
    const char *path = getenv("R1_QEMU_FB_PATH");
    int result;
    if (!path || !*path)
        path = R1_DEFAULT_FRAMEBUFFER_PATH;
    result = next_open(path, O_RDWR | O_CREAT, 0600);
    if (result >= 0 && next_ftruncate(result, R1_FRAMEBUFFER_SIZE) != 0) {
        int saved_errno = errno;
        next_close(result);
        errno = saved_errno;
        return -1;
    }
    if (result >= 0)
        framebuffer_fd = result;
    return result;
}

static int is_event_name(const char *path)
{
    const char *name = strrchr(path, '/');
    name = name ? name + 1 : path;
    return strcmp(name, "event0") == 0 || strcmp(name, "event1") == 0;
}

static int is_input_fd(int fd)
{
    unsigned index;
    for (index = 0; index < R1_MAX_INPUT_FDS; ++index) {
        /* Store fd+1 so the zero-initialized array can represent an empty
         * slot.  Atomic loads avoid racing concurrent LiteGUI/input opens. */
        if (__sync_fetch_and_add(&input_fds[index], 0) == fd + 1)
            return 1;
    }
    return 0;
}

static int record_input_open(const char *path, int result)
{
    unsigned index;
    if (result < 0 || !is_event_name(path))
        return result;
    for (index = 0; index < R1_MAX_INPUT_FDS; ++index) {
        if (__sync_bool_compare_and_swap(&input_fds[index], 0, result + 1))
            return result;
    }
    next_close(result);
    errno = EMFILE;
    return -1;
}

static int open_input_backing(void)
{
    const char *path = getenv("R1_QEMU_TOUCH_PATH");
    char endpoint[512];
    int result;
    int length;
    if (!path || !*path)
        path = R1_DEFAULT_TOUCH_PATH;
    /* evdev broadcasts every event to every open file description, whereas
     * multiple readers of one host FIFO would split the byte stream.  Give
     * every guest open its own endpoint; bridge.py broadcasts to all of them. */
    length = snprintf(endpoint, sizeof(endpoint), "%s.%u", path,
                      __sync_fetch_and_add(&input_open_serial, 1U));
    if (length < 0 || (size_t)length >= sizeof(endpoint)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    if (next_mkfifo(endpoint, 0600) != 0 && errno != EEXIST)
        return -1;
    result = next_open(endpoint, O_RDWR | O_NONBLOCK);
    return record_input_open("event0", result);
}

static int record_open(const char *path, int result)
{
    if (result >= 0 && path && strcmp(path, "/dev/fb0") == 0)
        framebuffer_fd = result;
    if (result >= 0 && is_event_name(path))
        return record_input_open(path, result);
    return result;
}

int open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    resolve_symbols();
    if (strcmp(path, "/dev/fb0") == 0)
        return open_framebuffer_backing();
    if (strcmp(path, "/dev/sa_hgl_dma") == 0)
        return open_hgl_dma_backing();
    if (strcmp(path, "/dev/input/event0") == 0 ||
        strcmp(path, "/dev/input/event1") == 0)
        return open_input_backing();
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
        return record_open(path, next_open(path, flags, mode));
    }
    return record_open(path, next_open(path, flags));
}

int open64(const char *path, int flags, ...)
{
    mode_t mode = 0;
    resolve_symbols();
    if (strcmp(path, "/dev/fb0") == 0)
        return open_framebuffer_backing();
    if (strcmp(path, "/dev/sa_hgl_dma") == 0)
        return open_hgl_dma_backing();
    if (strcmp(path, "/dev/input/event0") == 0 ||
        strcmp(path, "/dev/input/event1") == 0)
        return open_input_backing();
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
        return record_open(path, next_open64(path, flags, mode));
    }
    return record_open(path, next_open64(path, flags));
}

int openat64(int directory_fd, const char *path, int flags, ...)
{
    mode_t mode = 0;
    int result;
    resolve_symbols();
    if (!next_openat64) {
        errno = ENOSYS;
        return -1;
    }
    /* hiby_player discovers event nodes with readdir() and then opens the
     * relative name with openat64(O_RDONLY).  Opening the host FIFO that way
     * would block until the browser bridge connects.  Redirect it to the
     * shim's O_RDWR|O_NONBLOCK endpoint, exactly as for absolute opens. */
    if (is_event_name(path))
        return open_input_backing();
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
        result = next_openat64(directory_fd, path, flags, mode);
    } else {
        result = next_openat64(directory_fd, path, flags);
    }
    return result;
}

int close(int fd)
{
    unsigned index;
    resolve_symbols();
    if (fd == framebuffer_fd)
        framebuffer_fd = -1;
    if (fd == hgl_dma_fd)
        hgl_dma_fd = -1;
    for (index = 0; index < R1_MAX_INPUT_FDS; ++index) {
        if (__sync_bool_compare_and_swap(&input_fds[index], fd + 1, 0))
            break;
    }
    return next_close(fd);
}

static void remember_mapping(int fd, void *result, size_t length)
{
    if (result == MAP_FAILED)
        return;
    if (fd == framebuffer_fd) {
        framebuffer_mapping = result;
        framebuffer_mapping_length = length;
    } else if (fd == hgl_dma_fd) {
        hgl_dma_mapping = result;
        hgl_dma_mapping_length = length;
    }
}

void *mmap(void *address, size_t length, int protection, int flags, int fd,
           off_t offset)
{
    void *result;
    resolve_symbols();
    result = next_mmap(address, length, protection, flags, fd, offset);
    remember_mapping(fd, result, length);
    return result;
}

void *mmap64(void *address, size_t length, int protection, int flags, int fd,
             off64_t offset)
{
    void *result;
    resolve_symbols();
    result = next_mmap64(address, length, protection, flags, fd, offset);
    remember_mapping(fd, result, length);
    return result;
}

static int copy_dma_rows(const struct r1_dma_descriptor *descriptor)
{
    int64_t source = descriptor->source_offset;
    int64_t destination =
        (uint32_t)(descriptor->destination_physical - R1_FAKE_SMEM_START);
    unsigned row;
    if (!framebuffer_mapping || !hgl_dma_mapping || !descriptor->line_bytes ||
        !descriptor->rows)
        return -1;
    for (row = 0; row < descriptor->rows; ++row) {
        if (source < 0 || destination < 0 ||
            (uint64_t)source + descriptor->line_bytes > hgl_dma_mapping_length ||
            (uint64_t)destination + descriptor->line_bytes >
                framebuffer_mapping_length)
            return -1;
        memcpy(framebuffer_mapping + destination, hgl_dma_mapping + source,
               descriptor->line_bytes);
        source += descriptor->source_stride;
        destination += descriptor->destination_stride;
    }
    return 0;
}

ssize_t write(int fd, const void *buffer, size_t count)
{
    struct r1_dma_descriptor descriptor;
    resolve_symbols();
    if (fd != hgl_dma_fd)
        return next_write(fd, buffer, count);
    if (count != sizeof(descriptor)) {
        errno = EINVAL;
        return -1;
    }
    memcpy(&descriptor, buffer, sizeof(descriptor));
    if (copy_dma_rows(&descriptor) != 0) {
        errno = EFAULT;
        return -1;
    }
    return (ssize_t)count;
}

ssize_t read(int fd, void *buffer, size_t count)
{
    struct r1_input_event_log record;
    ssize_t result;
    size_t payload_size;
    resolve_symbols();
    result = next_read(fd, buffer, count);
    if (!is_input_fd(fd) || input_event_log_fd < 0)
        return result;
    memset(&record, 0, sizeof(record));
    record.magic = R1_INPUT_EVENT_MAGIC;
    record.fd = fd;
    record.requested = (uint32_t)count;
    record.result = (int32_t)result;
    if (result > 0) {
        payload_size = (size_t)result < sizeof(record.payload)
                           ? (size_t)result
                           : sizeof(record.payload);
        memcpy(record.payload, buffer, payload_size);
    }
    (void)next_write(input_event_log_fd, &record, sizeof(record));
    return result;
}

static void fill_fix_screeninfo(struct fb_fix_screeninfo *information)
{
    memset(information, 0, sizeof(*information));
    strncpy(information->id, "r1-qemu-fb", sizeof(information->id) - 1);
    information->smem_start = R1_FAKE_SMEM_START;
    information->smem_len = R1_FRAMEBUFFER_SIZE;
    information->type = FB_TYPE_PACKED_PIXELS;
    information->visual = FB_VISUAL_TRUECOLOR;
    information->ypanstep = 1;
    information->line_length = R1_WIDTH * R1_BYTES_PER_PIXEL;
    information->accel = FB_ACCEL_NONE;
}

static void fill_var_screeninfo(struct fb_var_screeninfo *information)
{
    memset(information, 0, sizeof(*information));
    information->xres = R1_WIDTH;
    information->yres = R1_HEIGHT;
    information->xres_virtual = R1_WIDTH;
    information->yres_virtual = R1_HEIGHT * R1_FRAME_COUNT;
    information->bits_per_pixel = R1_BYTES_PER_PIXEL * 8U;
    information->red.offset = 16;
    information->red.length = 8;
    information->green.offset = 8;
    information->green.length = 8;
    information->blue.offset = 0;
    information->blue.length = 8;
    information->transp.offset = 24;
    information->transp.length = 8;
    information->yoffset = current_yoffset;
    information->activate = FB_ACTIVATE_NOW;
    information->height = (uint32_t)-1;
    information->width = (uint32_t)-1;
}

static void set_capability(void *argument, unsigned size, unsigned bit)
{
    uint8_t *bytes = argument;
    if (bit / 8U < size)
        bytes[bit / 8U] |= (uint8_t)(1U << (bit % 8U));
}

static int input_ioctl(unsigned long request, void *argument)
{
    uint32_t log_record[3];
    unsigned number = _IOC_NR(request);
    unsigned size = _IOC_SIZE(request);
    unsigned event_type;
    unsigned actual_size;
    struct input_absinfo *absolute;

    if (input_log_fd >= 0) {
        log_record[0] = (uint32_t)request;
        log_record[1] = number;
        log_record[2] = size;
        (void)next_write(input_log_fd, log_record, sizeof(log_record));
    }

    if (request == EVIOCGVERSION) {
        *(int *)argument = 0x010001;
        return 0;
    }
    if (request == EVIOCGID) {
        struct input_id *identity = argument;
        memset(identity, 0, sizeof(*identity));
        identity->bustype = BUS_I2C;
        identity->vendor = 0x4859;
        identity->product = 0x0001;
        identity->version = 1;
        return 0;
    }
    if (number == _IOC_NR(EVIOCGNAME(0))) {
        const char name[] = "hyn_ts";
        if (!size)
            return 0;
        actual_size = size < sizeof(name) ? size : sizeof(name);
        memcpy(argument, name, actual_size);
        return (int)actual_size;
    }
    if (number == _IOC_NR(EVIOCGPROP(0))) {
        actual_size = (INPUT_PROP_MAX + 8U) / 8U;
        if (actual_size > size)
            actual_size = size;
        memset(argument, 0, actual_size);
        set_capability(argument, actual_size, INPUT_PROP_DIRECT);
        return (int)actual_size;
    }
    if (number >= _IOC_NR(EVIOCGBIT(0, 0)) &&
        number < _IOC_NR(EVIOCGBIT(EV_MAX, 0))) {
        event_type = number - _IOC_NR(EVIOCGBIT(0, 0));
        if (event_type == 0U) {
            actual_size = (EV_MAX + 8U) / 8U;
        } else if (event_type == EV_KEY) {
            actual_size = (KEY_MAX + 8U) / 8U;
        } else if (event_type == EV_ABS) {
            actual_size = (ABS_MAX + 8U) / 8U;
        } else {
            actual_size = 0;
        }
        if (actual_size > size)
            actual_size = size;
        memset(argument, 0, actual_size);
        if (event_type == 0U) {
            set_capability(argument, actual_size, EV_SYN);
            set_capability(argument, actual_size, EV_KEY);
            set_capability(argument, actual_size, EV_ABS);
        } else if (event_type == EV_KEY) {
            set_capability(argument, actual_size, BTN_TOUCH);
        } else if (event_type == EV_ABS) {
            set_capability(argument, actual_size, ABS_MT_TOUCH_MAJOR);
            set_capability(argument, actual_size, ABS_MT_POSITION_X);
            set_capability(argument, actual_size, ABS_MT_POSITION_Y);
            set_capability(argument, actual_size, ABS_MT_TRACKING_ID);
            set_capability(argument, actual_size, ABS_MT_PRESSURE);
        }
        return (int)actual_size;
    }
    if (number >= _IOC_NR(EVIOCGABS(0)) &&
        number <= _IOC_NR(EVIOCGABS(ABS_MAX))) {
        unsigned code = number - _IOC_NR(EVIOCGABS(0));
        absolute = argument;
        memset(absolute, 0, sizeof(*absolute));
        if (code == ABS_MT_POSITION_X)
            absolute->maximum = R1_WIDTH;
        else if (code == ABS_MT_POSITION_Y)
            absolute->maximum = R1_HEIGHT;
        else if (code == ABS_MT_TRACKING_ID)
            absolute->maximum = 65535;
        else
            absolute->maximum = 255;
        return 0;
    }
    if (number == _IOC_NR(EVIOCGRAB) || number == _IOC_NR(EVIOCSCLOCKID))
        return 0;
    errno = ENOTTY;
    return -1;
}

static void publish_frame_state(uint32_t yoffset)
{
    struct r1_frame_state state;
    current_yoffset = yoffset < R1_HEIGHT ? 0U : R1_HEIGHT;
    ++frame_sequence;
    if (frame_state_fd < 0)
        return;
    state.magic = R1_FRAME_STATE_MAGIC;
    state.version = 1;
    state.yoffset = current_yoffset;
    state.sequence = frame_sequence;
    (void)next_pwrite(frame_state_fd, &state, sizeof(state), 0);
}

int ioctl(int fd, unsigned long request, ...)
{
    void *argument;
    va_list arguments;
    va_start(arguments, request);
    argument = va_arg(arguments, void *);
    va_end(arguments);

    resolve_symbols();
    if (is_input_fd(fd))
        return input_ioctl(request, argument);
    if (fd != framebuffer_fd)
        return next_ioctl(fd, request, argument);
    switch (request) {
    case FBIOGET_FSCREENINFO:
        fill_fix_screeninfo(argument);
        return 0;
    case FBIOGET_VSCREENINFO:
        fill_var_screeninfo(argument);
        return 0;
    case FBIOPUT_VSCREENINFO:
        if (argument)
            publish_frame_state(((struct fb_var_screeninfo *)argument)->yoffset);
        return 0;
    case FBIOPAN_DISPLAY:
        if (argument)
            publish_frame_state(((struct fb_var_screeninfo *)argument)->yoffset);
        return 0;
    case FBIOBLANK:
    case R1_ROTATE_IOCTL:
        return 0;
    default:
        errno = ENOTTY;
        return -1;
    }
}
