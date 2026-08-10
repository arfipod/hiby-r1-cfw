#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <unistd.h>

/*
 * Minimal Linux framebuffer contract for running the proprietary R1 UI under
 * qemu-user. Pixel memory remains a normal file supplied by the host harness.
 */

#define R1_WIDTH 480U
#define R1_HEIGHT 800U
#define R1_BYTES_PER_PIXEL 4U
#define R1_FRAME_COUNT 2U

static int framebuffer_fd = -1;

static int (*next_open)(const char *, int, ...);
static int (*next_open64)(const char *, int, ...);
static int (*next_close)(int);
static int (*next_ioctl)(int, unsigned long, ...);

static void resolve_symbols(void) {
    if (!next_open) {
        next_open = dlsym(RTLD_NEXT, "open");
        next_open64 = dlsym(RTLD_NEXT, "open64");
        next_close = dlsym(RTLD_NEXT, "close");
        next_ioctl = dlsym(RTLD_NEXT, "ioctl");
    }
}

static int record_open(const char *path, int result) {
    if (result >= 0 && path && strcmp(path, "/dev/fb0") == 0) {
        framebuffer_fd = result;
    }
    return result;
}

int open(const char *path, int flags, ...) {
    mode_t mode = 0;
    resolve_symbols();
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
        return record_open(path, next_open(path, flags, mode));
    }
    return record_open(path, next_open(path, flags));
}

int open64(const char *path, int flags, ...) {
    mode_t mode = 0;
    resolve_symbols();
    if (!next_open64) {
        next_open64 = next_open;
    }
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = (mode_t)va_arg(arguments, int);
        va_end(arguments);
        return record_open(path, next_open64(path, flags, mode));
    }
    return record_open(path, next_open64(path, flags));
}

int close(int fd) {
    resolve_symbols();
    if (fd == framebuffer_fd) {
        framebuffer_fd = -1;
    }
    return next_close(fd);
}

static void fill_fix_screeninfo(struct fb_fix_screeninfo *info) {
    memset(info, 0, sizeof(*info));
    strncpy(info->id, "r1-qemu-fb", sizeof(info->id) - 1);
    info->smem_len = R1_WIDTH * R1_HEIGHT * R1_BYTES_PER_PIXEL * R1_FRAME_COUNT;
    info->type = FB_TYPE_PACKED_PIXELS;
    info->visual = FB_VISUAL_TRUECOLOR;
    info->ypanstep = 1;
    info->line_length = R1_WIDTH * R1_BYTES_PER_PIXEL;
    info->accel = FB_ACCEL_NONE;
}

static void fill_var_screeninfo(struct fb_var_screeninfo *info) {
    memset(info, 0, sizeof(*info));
    info->xres = R1_WIDTH;
    info->yres = R1_HEIGHT;
    info->xres_virtual = R1_WIDTH;
    info->yres_virtual = R1_HEIGHT * R1_FRAME_COUNT;
    info->bits_per_pixel = R1_BYTES_PER_PIXEL * 8U;
    info->red.offset = 16;
    info->red.length = 8;
    info->green.offset = 8;
    info->green.length = 8;
    info->blue.offset = 0;
    info->blue.length = 8;
    info->transp.offset = 24;
    info->transp.length = 8;
    info->activate = FB_ACTIVATE_NOW;
    info->height = (uint32_t)-1;
    info->width = (uint32_t)-1;
}

int ioctl(int fd, unsigned long request, ...) {
    void *argument;
    va_list arguments;

    va_start(arguments, request);
    argument = va_arg(arguments, void *);
    va_end(arguments);

    resolve_symbols();
    if (fd != framebuffer_fd) {
        return next_ioctl(fd, request, argument);
    }

    switch (request) {
    case FBIOGET_FSCREENINFO:
        fill_fix_screeninfo(argument);
        return 0;
    case FBIOGET_VSCREENINFO:
        fill_var_screeninfo(argument);
        return 0;
    case FBIOPUT_VSCREENINFO:
    case FBIOPAN_DISPLAY:
    case FBIOBLANK:
        return 0;
    default:
        errno = ENOTTY;
        return -1;
    }
}
