#define _GNU_SOURCE

#include "r1_cfw_platform.h"

#include <errno.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

struct glyph {
    char character;
    uint8_t columns[5];
};

static const struct glyph glyphs[] = {
    {'A',{0x7e,0x11,0x11,0x11,0x7e}}, {'B',{0x7f,0x49,0x49,0x49,0x36}},
    {'C',{0x3e,0x41,0x41,0x41,0x22}}, {'D',{0x7f,0x41,0x41,0x22,0x1c}},
    {'E',{0x7f,0x49,0x49,0x49,0x41}}, {'F',{0x7f,0x09,0x09,0x09,0x01}},
    {'G',{0x3e,0x41,0x49,0x49,0x7a}}, {'H',{0x7f,0x08,0x08,0x08,0x7f}},
    {'I',{0x00,0x41,0x7f,0x41,0x00}}, {'J',{0x20,0x40,0x41,0x3f,0x01}},
    {'K',{0x7f,0x08,0x14,0x22,0x41}}, {'L',{0x7f,0x40,0x40,0x40,0x40}},
    {'M',{0x7f,0x02,0x0c,0x02,0x7f}}, {'N',{0x7f,0x04,0x08,0x10,0x7f}},
    {'O',{0x3e,0x41,0x41,0x41,0x3e}}, {'P',{0x7f,0x09,0x09,0x09,0x06}},
    {'Q',{0x3e,0x41,0x51,0x21,0x5e}}, {'R',{0x7f,0x09,0x19,0x29,0x46}},
    {'S',{0x46,0x49,0x49,0x49,0x31}}, {'T',{0x01,0x01,0x7f,0x01,0x01}},
    {'U',{0x3f,0x40,0x40,0x40,0x3f}}, {'V',{0x1f,0x20,0x40,0x20,0x1f}},
    {'W',{0x3f,0x40,0x38,0x40,0x3f}}, {'X',{0x63,0x14,0x08,0x14,0x63}},
    {'Y',{0x07,0x08,0x70,0x08,0x07}}, {'Z',{0x61,0x51,0x49,0x45,0x43}},
    {'0',{0x3e,0x45,0x49,0x51,0x3e}}, {'1',{0x00,0x21,0x7f,0x01,0x00}},
    {'2',{0x21,0x43,0x45,0x49,0x31}}, {'3',{0x42,0x41,0x51,0x69,0x46}},
    {'4',{0x0c,0x14,0x24,0x7f,0x04}}, {'5',{0x72,0x51,0x51,0x51,0x4e}},
    {'6',{0x1e,0x29,0x49,0x49,0x06}}, {'7',{0x40,0x47,0x48,0x50,0x60}},
    {'8',{0x36,0x49,0x49,0x49,0x36}}, {'9',{0x30,0x49,0x49,0x4a,0x3c}},
    {'-',{0x08,0x08,0x08,0x08,0x08}}, {'/',{0x20,0x10,0x08,0x04,0x02}},
    {':',{0x00,0x36,0x36,0x00,0x00}}, {'.',{0x00,0x60,0x60,0x00,0x00}},
    {'<',{0x08,0x14,0x22,0x41,0x00}}, {'>',{0x00,0x41,0x22,0x14,0x08}},
    {'_',{0x40,0x40,0x40,0x40,0x40}}, {'%',{0x63,0x13,0x08,0x64,0x63}},
};

static uint32_t rgb565_to_xrgb8888(uint16_t color) {
    uint32_t red = (color >> 11) & 0x1f;
    uint32_t green = (color >> 5) & 0x3f;
    uint32_t blue = color & 0x1f;
    red = (red << 3) | (red >> 2);
    green = (green << 2) | (green >> 4);
    blue = (blue << 3) | (blue >> 2);
    return (red << 16) | (green << 8) | blue;
}

int r1_display_init(struct r1_display *display, int descriptor, int test_mode,
                    int test_stride, int test_bpp, size_t test_bytes,
                    int test_packed_rgb565) {
    struct fb_var_screeninfo variable;
    struct fb_fix_screeninfo fixed;
    memset(display, 0, sizeof(*display));
    display->fd = descriptor;
    if (descriptor < 0) return -EINVAL;

    if (test_mode) {
        if (test_stride < R1_SCREEN_WIDTH * 2 ||
            (test_bpp != 16 && test_bpp != 32) ||
            test_bytes < (size_t)test_stride * R1_SCREEN_HEIGHT * 2U) {
            return -EINVAL;
        }
        display->stride = test_stride;
        display->bits_per_pixel = test_bpp;
        display->map_bytes = test_bytes;
        display->packed_rgb565 = test_packed_rgb565 || test_bpp == 16;
    } else {
        if (ioctl(descriptor, FBIOGET_VSCREENINFO, &variable) < 0 ||
            ioctl(descriptor, FBIOGET_FSCREENINFO, &fixed) < 0) {
            return -errno;
        }
        if (variable.xres != R1_SCREEN_WIDTH ||
            variable.yres != R1_SCREEN_HEIGHT ||
            variable.yres_virtual < R1_SCREEN_HEIGHT * 2 ||
            (variable.bits_per_pixel != 16 && variable.bits_per_pixel != 32) ||
            fixed.line_length <
                R1_SCREEN_WIDTH * variable.bits_per_pixel / 8 ||
            (size_t)fixed.smem_len <
                (size_t)fixed.line_length * variable.yres_virtual) {
            return -ENOTSUP;
        }
        display->stride = (int)fixed.line_length;
        display->bits_per_pixel = (int)variable.bits_per_pixel;
        display->map_bytes = fixed.smem_len;
        display->packed_rgb565 = variable.bits_per_pixel == 16;
        if (getenv("R1_CFW_FB_PACKED_RGB565"))
            display->packed_rgb565 = 1;
        display->active_page = variable.yoffset >= R1_SCREEN_HEIGHT;
    }
    display->memory = mmap(NULL, display->map_bytes, PROT_READ | PROT_WRITE,
                           MAP_SHARED, descriptor, 0);
    if (display->memory == MAP_FAILED) {
        display->memory = NULL;
        return -errno;
    }
    return 0;
}

void r1_display_destroy(struct r1_display *display) {
    if (display->memory) munmap(display->memory, display->map_bytes);
    display->memory = NULL;
}

static uint8_t *pixel(struct r1_display *display, int page, int x, int y) {
    int bytes_per_pixel = display->packed_rgb565 ? 2 : 4;
    size_t offset = (size_t)(page * R1_SCREEN_HEIGHT + y) *
                    (size_t)display->stride + (size_t)x * bytes_per_pixel;
    return display->memory + offset;
}

void r1_display_fill(struct r1_display *display, int page, int x, int y,
                     int width, int height, uint16_t color) {
    int row;
    int column;
    if (x < 0) { width += x; x = 0; }
    if (y < 0) { height += y; y = 0; }
    if (x + width > R1_SCREEN_WIDTH) width = R1_SCREEN_WIDTH - x;
    if (y + height > R1_SCREEN_HEIGHT) height = R1_SCREEN_HEIGHT - y;
    if (width <= 0 || height <= 0 || !display->memory) return;
    for (row = 0; row < height; ++row) {
        uint8_t *destination = pixel(display, page, x, y + row);
        if (display->packed_rgb565) {
            uint16_t *pixels = (uint16_t *)destination;
            for (column = 0; column < width; ++column) pixels[column] = color;
        } else {
            uint32_t *pixels = (uint32_t *)destination;
            uint32_t xrgb = rgb565_to_xrgb8888(color);
            for (column = 0; column < width; ++column) pixels[column] = xrgb;
        }
    }
}

void r1_display_clear(struct r1_display *display, int page, uint16_t color) {
    r1_display_fill(display, page, 0, 0, R1_SCREEN_WIDTH, R1_SCREEN_HEIGHT,
                    color);
}

static const uint8_t *find_glyph(char character) {
    size_t index;
    if (character >= 'a' && character <= 'z') character -= 'a' - 'A';
    for (index = 0; index < sizeof(glyphs) / sizeof(glyphs[0]); ++index)
        if (glyphs[index].character == character) return glyphs[index].columns;
    return NULL;
}

int r1_display_text_width(const char *text, int scale) {
    return text ? (int)strlen(text) * 6 * scale : 0;
}

void r1_display_text(struct r1_display *display, int page, int x, int y,
                     const char *text, int scale, uint16_t color) {
    if (!text) return;
    while (*text) {
        const uint8_t *columns = find_glyph(*text++);
        int column;
        if (columns) {
            for (column = 0; column < 5; ++column) {
                int row;
                for (row = 0; row < 7; ++row) {
                    if (columns[column] & (1U << row))
                        r1_display_fill(display, page, x + column * scale,
                                        y + row * scale, scale, scale, color);
                }
            }
        }
        x += 6 * scale;
    }
}

void r1_display_text_right(struct r1_display *display, int page, int right,
                           int y, const char *text, int scale,
                           uint16_t color) {
    r1_display_text(display, page,
                    right - r1_display_text_width(text, scale), y, text, scale,
                    color);
}

void r1_display_pan(struct r1_display *display, int page) {
    struct fb_var_screeninfo variable;
    page = page ? 1 : 0;
    if (ioctl(display->fd, FBIOGET_VSCREENINFO, &variable) == 0) {
        variable.yoffset = (uint32_t)(page * R1_SCREEN_HEIGHT);
        (void)ioctl(display->fd, FBIOPAN_DISPLAY, &variable);
    }
    display->active_page = page;
}

void r1_touch_init(struct r1_touch_state *state) {
    memset(state, 0, sizeof(*state));
    state->down_x = -1;
    state->down_y = -1;
}

int r1_touch_read(int descriptor, struct r1_touch_state *state,
                  struct r1_gesture *gesture) {
    struct input_event event;
    ssize_t count;
    memset(gesture, 0, sizeof(*gesture));
    while ((count = read(descriptor, &event, sizeof(event))) == sizeof(event)) {
        if (event.type == EV_ABS) {
            if (event.code == ABS_X || event.code == ABS_MT_POSITION_X)
                state->x = event.value;
            else if (event.code == ABS_Y || event.code == ABS_MT_POSITION_Y)
                state->y = event.value;
            else if (event.code == ABS_MT_TRACKING_ID) {
                if (event.value >= 0 && !state->active) {
                    state->active = 1;
                    state->down_x = -1;
                    state->down_y = -1;
                    state->moved = 0;
                } else if (event.value < 0 && state->active) {
                    state->release_pending = 1;
                }
            }
            if (state->active && state->down_x >= 0 &&
                (abs(state->x - state->down_x) > 16 ||
                 abs(state->y - state->down_y) > 16))
                state->moved = 1;
        } else if (event.type == EV_KEY && event.code == BTN_TOUCH) {
            if (event.value && !state->active) {
                state->active = 1;
                state->down_x = state->x;
                state->down_y = state->y;
                state->moved = 0;
            } else if (!event.value && state->active) {
                state->release_pending = 1;
            }
        } else if (event.type == EV_SYN && event.code == SYN_REPORT) {
            if (state->active && state->down_x < 0) {
                state->down_x = state->x;
                state->down_y = state->y;
            }
            if (state->release_pending) {
                gesture->x = state->x;
                gesture->y = state->y;
                gesture->delta_x = state->x - state->down_x;
                gesture->delta_y = state->y - state->down_y;
                gesture->kind = state->moved ? R1_GESTURE_DRAG : R1_GESTURE_TAP;
                state->active = 0;
                state->release_pending = 0;
                state->down_x = -1;
                state->down_y = -1;
                state->moved = 0;
                return 1;
            }
        }
    }
    if (count < 0 && errno != EAGAIN && errno != EINTR) return -errno;
    return 0;
}
