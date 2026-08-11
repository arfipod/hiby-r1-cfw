#ifndef R1_CFW_PLATFORM_H
#define R1_CFW_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

#define R1_SCREEN_WIDTH 480
#define R1_SCREEN_HEIGHT 800

struct r1_display {
    int fd;
    uint8_t *memory;
    size_t map_bytes;
    int stride;
    int bits_per_pixel;
    int packed_rgb565;
    int active_page;
};

enum r1_gesture_kind {
    R1_GESTURE_NONE,
    R1_GESTURE_TAP,
    R1_GESTURE_DRAG,
};

struct r1_gesture {
    enum r1_gesture_kind kind;
    int x;
    int y;
    int delta_x;
    int delta_y;
};

struct r1_touch_state {
    int active;
    int release_pending;
    int x;
    int y;
    int down_x;
    int down_y;
    int moved;
};

int r1_display_init(struct r1_display *display, int descriptor, int test_mode,
                    int test_stride, int test_bpp, size_t test_bytes,
                    int test_packed_rgb565);
void r1_display_destroy(struct r1_display *display);
void r1_display_pan(struct r1_display *display, int page);
void r1_display_clear(struct r1_display *display, int page, uint16_t color);
void r1_display_fill(struct r1_display *display, int page, int x, int y,
                     int width, int height, uint16_t color);
void r1_display_text(struct r1_display *display, int page, int x, int y,
                     const char *text, int scale, uint16_t color);
void r1_display_text_right(struct r1_display *display, int page, int right,
                           int y, const char *text, int scale,
                           uint16_t color);
int r1_display_text_width(const char *text, int scale);

void r1_touch_init(struct r1_touch_state *state);
int r1_touch_read(int descriptor, struct r1_touch_state *state,
                  struct r1_gesture *gesture);

#endif
