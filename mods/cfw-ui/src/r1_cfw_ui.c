#define _GNU_SOURCE

#include "r1_cfw_data.h"
#include "r1_cfw_platform.h"
#include "r1_cfw_protocol.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* Legacy sidecar palette. */
#define COLOR_BLACK 0x0000
#define COLOR_WHITE 0xffff
#define COLOR_DIM 0x7bef
#define COLOR_DIVIDER 0x4a49
#define COLOR_HEADER 0x1082
#define COLOR_ACCENT 0x131e
#define COLOR_ON 0x07e0

/* Retro Handheld RGB565 design tokens. */
#define RETRO_CANVAS 0xf77c
#define RETRO_BRIGHT 0xffde
#define RETRO_HEADER 0x4a49
#define RETRO_HEADER_DOT 0x6b4d
#define RETRO_INK 0x10c3
#define RETRO_MUTED 0x73ae
#define RETRO_BORDER 0x9491
#define RETRO_MINT 0x26d3
#define RETRO_CYAN 0x055d

#define HEADER_HEIGHT 80
#define ROW_HEIGHT 72

struct options {
    int framebuffer_fd;
    int touch_fd;
    int test_mode;
    int test_stride;
    int test_bpp;
    size_t test_bytes;
    int test_packed_rgb565;
    int dump_info;
    int launcher_show;
    const char *launcher_name;
    int launcher_enabled;
    int theme_show;
    const char *theme_name;
};

struct ui_state {
    struct r1_display display;
    struct r1_touch_state touch;
    struct r1_cfw_paths paths;
    struct r1_cfw_system_info info;
    enum r1_cfw_screen_id screen;
    enum r1_cfw_action_id last_action;
    enum r1_cfw_theme theme;
    uint32_t launcher_mask;
    uint32_t sequence;
    int route;
    int touch_fd;
    int redraw;
    const char *test_state_path;
    const char *launcher_notice;
};

static volatile sig_atomic_t stop_requested;

static void request_stop(int signal_number) {
    (void)signal_number;
    stop_requested = 1;
}

static int64_t monotonic_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static long parse_long(const char *value, const char *label) {
    char *end = NULL;
    long result;
    errno = 0;
    result = strtol(value, &end, 0);
    if (errno || !end || *end) {
        fprintf(stderr, "invalid %s: %s\n", label, value);
        exit(2);
    }
    return result;
}

static void usage(FILE *stream, const char *program) {
    fprintf(stream,
            "Usage: %s [UI options | data command]\n"
            "\n"
            "UI options:\n"
            "  --fb-fd N --touch-fd N       inherited device descriptors\n"
            "  --test-mode                  regular-file framebuffer mode\n"
            "  --fb-stride N --fb-bpp N     test framebuffer format\n"
            "  --fb-bytes N                 test framebuffer mapping size\n"
            "  --packed-rgb565              packed RGB565 in padded rows\n"
            "\n"
            "Data commands:\n"
            "  --dump-info                  print collected key=value data\n"
            "  --launcher-show              print persistent launcher mask\n"
            "  --launcher-set NAME 0|1      update one launcher tile\n"
            "  --theme-show                 print persistent visual theme\n"
            "  --theme-set NAME             set stock, light, dark, or retro\n"
            "\n"
            "Emulator-only environment:\n"
            "  R1_CFW_TEST_STATE_PATH       28-byte r1_cfw_test_state record\n"
            "    7 little-endian uint32: magic, version, sequence, screen,\n"
            "    launcher mask, last action, route (magic 0x52314346)\n"
            "  R1_CFW_TEST_AUTO_EXIT_MS     clean root-Back exit deadline\n"
            "  R1_CFW_TEST_CRASH_AFTER_MS   forced SIGABRT deadline\n",
            program);
}

static struct options parse_options(int argc, char **argv) {
    struct options options;
    int index;
    memset(&options, 0, sizeof(options));
    options.framebuffer_fd = -1;
    options.touch_fd = -1;
    options.test_stride = 1920;
    options.test_bpp = 32;
    options.test_bytes = 3072000;
    options.launcher_enabled = -1;

    for (index = 1; index < argc; ++index) {
        if (strcmp(argv[index], "--fb-fd") == 0 && index + 1 < argc)
            options.framebuffer_fd = (int)parse_long(argv[++index], "fb fd");
        else if (strcmp(argv[index], "--touch-fd") == 0 && index + 1 < argc)
            options.touch_fd = (int)parse_long(argv[++index], "touch fd");
        else if (strcmp(argv[index], "--fb-stride") == 0 && index + 1 < argc)
            options.test_stride = (int)parse_long(argv[++index], "fb stride");
        else if (strcmp(argv[index], "--fb-bpp") == 0 && index + 1 < argc)
            options.test_bpp = (int)parse_long(argv[++index], "fb bpp");
        else if (strcmp(argv[index], "--fb-bytes") == 0 && index + 1 < argc)
            options.test_bytes = (size_t)parse_long(argv[++index], "fb bytes");
        else if (strcmp(argv[index], "--test-mode") == 0)
            options.test_mode = 1;
        else if (strcmp(argv[index], "--packed-rgb565") == 0)
            options.test_packed_rgb565 = 1;
        else if (strcmp(argv[index], "--dump-info") == 0)
            options.dump_info = 1;
        else if (strcmp(argv[index], "--launcher-show") == 0)
            options.launcher_show = 1;
        else if (strcmp(argv[index], "--launcher-set") == 0 && index + 2 < argc) {
            options.launcher_name = argv[++index];
            options.launcher_enabled = (int)parse_long(argv[++index], "tile state");
            if (options.launcher_enabled != 0 && options.launcher_enabled != 1) {
                fprintf(stderr, "tile state must be 0 or 1\n");
                exit(2);
            }
        } else if (strcmp(argv[index], "--theme-show") == 0) {
            options.theme_show = 1;
        } else if (strcmp(argv[index], "--theme-set") == 0 && index + 1 < argc) {
            options.theme_name = argv[++index];
        } else if (strcmp(argv[index], "--help") == 0) {
            usage(stdout, argv[0]);
            exit(0);
        } else {
            fprintf(stderr, "unsupported or incomplete argument: %s\n",
                    argv[index]);
            usage(stderr, argv[0]);
            exit(2);
        }
    }
    return options;
}

static void print_storage(const char *prefix,
                          const struct r1_cfw_storage_info *storage) {
    printf("%s_present=%d\n", prefix, storage->available);
    printf("%s_total=%llu\n", prefix, (unsigned long long)storage->total);
    printf("%s_used=%llu\n", prefix, (unsigned long long)storage->used);
    printf("%s_available=%llu\n", prefix, (unsigned long long)storage->free);
}

static int run_data_command(const struct options *options,
                            const struct r1_cfw_paths *paths) {
    if (options->launcher_name) {
        uint32_t mask;
        int result = r1_cfw_launcher_set(paths, options->launcher_name,
                                         options->launcher_enabled, &mask);
        if (result < 0) {
            if (result == -ENOSPC)
                fprintf(stderr, "%s\n", R1_CFW_LAUNCHER_LIMIT_MESSAGE);
            else
                fprintf(stderr, "launcher update failed: %d\n", result);
            return 1;
        }
        printf("launcher_mask=%02x\n", mask);
        return 0;
    }
    if (options->launcher_show) {
        printf("launcher_mask=%02x\n", r1_cfw_launcher_load(paths));
        return 0;
    }
    if (options->theme_name) {
        enum r1_cfw_theme theme;
        int result = r1_cfw_theme_set(paths, options->theme_name, &theme);
        if (result < 0) {
            fprintf(stderr, "theme update failed: %d\n", result);
            return 1;
        }
        printf("theme=%s\n", r1_cfw_theme_name(theme));
        return 0;
    }
    if (options->theme_show) {
        enum r1_cfw_theme theme = r1_cfw_theme_load(paths);
        printf("theme=%s\n", r1_cfw_theme_name(theme));
        return 0;
    }
    if (options->dump_info) {
        struct r1_cfw_system_info info;
        enum r1_cfw_theme theme = r1_cfw_theme_load(paths);
        r1_cfw_collect_info(paths, &info);
        printf("cfw_version=%s\n", R1_CFW_VERSION);
        printf("stock_version=%s\n", R1_CFW_STOCK_VERSION);
        printf("theme=%s\n", r1_cfw_theme_name(theme));
        printf("ssh_enabled=%d\n", info.ssh_enabled);
        printf("wifi_ip=%s\n", info.wifi_ip);
        printf("hostname=%s\n", info.hostname);
        printf("kernel=%s\n", info.kernel);
        printf("uptime_seconds=%llu\n",
               (unsigned long long)info.uptime_seconds);
        printf("memory_total_kib=%llu\n",
               (unsigned long long)info.memory.total_kib);
        printf("memory_used_kib=%llu\n",
               (unsigned long long)info.memory.used_kib);
        printf("memory_available_kib=%llu\n",
               (unsigned long long)info.memory.available_kib);
        print_storage("internal", &info.internal_storage);
        print_storage("sd", &info.sd_storage);
        printf("launcher_mask=%02x\n", r1_cfw_launcher_load(paths));
        return 0;
    }
    return -1;
}

static int retro_active(const struct ui_state *ui) {
    return ui->theme == R1_CFW_THEME_RETRO;
}

static uint16_t row_value_color(const struct ui_state *ui, uint16_t legacy) {
    if (!retro_active(ui)) return legacy;
    if (legacy == COLOR_ON) return RETRO_MINT;
    if (legacy == COLOR_ACCENT) return RETRO_CYAN;
    return RETRO_MUTED;
}

static void draw_box(struct ui_state *ui, int page, int x, int y,
                     int width, int height, uint16_t fill, uint16_t border) {
    r1_display_fill(&ui->display, page, x, y, width, height, fill);
    r1_display_fill(&ui->display, page, x, y, width, 1, border);
    r1_display_fill(&ui->display, page, x, y + height - 1, width, 1, border);
    r1_display_fill(&ui->display, page, x, y, 1, height, border);
    r1_display_fill(&ui->display, page, x + width - 1, y, 1, height, border);
}

static const char *screen_title(enum r1_cfw_screen_id screen) {
    switch (screen) {
    case R1_CFW_SCREEN_LAUNCHER: return "LAUNCHER";
    case R1_CFW_SCREEN_INTERNAL: return "INTERNAL STORAGE";
    case R1_CFW_SCREEN_SD: return "MICROSD";
    case R1_CFW_SCREEN_MEMORY: return "MEMORY";
    case R1_CFW_SCREEN_SYSTEM: return "SYSTEM INFO";
    case R1_CFW_SCREEN_ABOUT: return "ABOUT CFW";
    case R1_CFW_SCREEN_APPEARANCE: return "APPEARANCE";
    default: return "CFW";
    }
}

static void draw_header(struct ui_state *ui, int page) {
    const char *title = screen_title(ui->screen);
    uint16_t fill = retro_active(ui) ? RETRO_HEADER : COLOR_HEADER;
    int x;
    int y;
    r1_display_fill(&ui->display, page, 0, 0, R1_SCREEN_WIDTH, HEADER_HEIGHT,
                    fill);
    if (retro_active(ui)) {
        for (y = 4; y < HEADER_HEIGHT; y += 8)
            for (x = 4; x < R1_SCREEN_WIDTH; x += 8)
                r1_display_fill(&ui->display, page, x, y, 2, 2,
                                RETRO_HEADER_DOT);
    }
    r1_display_text(&ui->display, page, 20, 25, "<", 4, COLOR_WHITE);
    r1_display_text(&ui->display, page,
                    (R1_SCREEN_WIDTH - r1_display_text_width(title, 3)) / 2,
                    26, title, 3, COLOR_WHITE);
}

static void draw_row(struct ui_state *ui, int page, int index,
                     const char *label, const char *value, uint16_t color) {
    int y = HEADER_HEIGHT + index * ROW_HEIGHT;
    uint16_t label_color = COLOR_WHITE;
    if (retro_active(ui)) {
        draw_box(ui, page, 8, y + 4, R1_SCREEN_WIDTH - 16, ROW_HEIGHT - 8,
                 RETRO_BRIGHT, RETRO_BORDER);
        label_color = RETRO_INK;
    } else {
        r1_display_fill(&ui->display, page, 0, y + ROW_HEIGHT - 1,
                        R1_SCREEN_WIDTH, 1, COLOR_DIVIDER);
    }
    r1_display_text(&ui->display, page, 22, y + 25, label, 3, label_color);
    if (value)
        r1_display_text_right(&ui->display, page, R1_SCREEN_WIDTH - 22,
                              y + 25, value, 3,
                              row_value_color(ui, color));
}

static void draw_detail_row(struct ui_state *ui, int page, int index,
                            const char *label, const char *value) {
    int y = HEADER_HEIGHT + index * ROW_HEIGHT;
    uint16_t label_color = COLOR_DIM;
    uint16_t value_color = COLOR_WHITE;
    if (retro_active(ui)) {
        draw_box(ui, page, 8, y + 4, R1_SCREEN_WIDTH - 16, ROW_HEIGHT - 8,
                 RETRO_BRIGHT, RETRO_BORDER);
        label_color = RETRO_MUTED;
        value_color = RETRO_INK;
    } else {
        r1_display_fill(&ui->display, page, 0, y + ROW_HEIGHT - 1,
                        R1_SCREEN_WIDTH, 1, COLOR_DIVIDER);
    }
    r1_display_text(&ui->display, page, 22, y + 12, label, 2, label_color);
    r1_display_text(&ui->display, page, 22, y + 39,
                    value && *value ? value : "NOT AVAILABLE", 2, value_color);
}

static void draw_ssh_row(struct ui_state *ui, int page, const char *status) {
    int y = HEADER_HEIGHT;
    uint16_t label_color = COLOR_WHITE;
    if (retro_active(ui)) {
        draw_box(ui, page, 8, y + 4, R1_SCREEN_WIDTH - 16, ROW_HEIGHT - 8,
                 RETRO_BRIGHT, RETRO_BORDER);
        label_color = RETRO_INK;
    } else {
        r1_display_fill(&ui->display, page, 0, y + ROW_HEIGHT - 1,
                        R1_SCREEN_WIDTH, 1, COLOR_DIVIDER);
    }
    r1_display_text(&ui->display, page, 22, y + 10, "SSH SERVER", 2,
                    label_color);
    r1_display_text(&ui->display, page, 22, y + 39, status, 2,
                    ui->info.ssh_enabled
                        ? row_value_color(ui, COLOR_ON)
                        : row_value_color(ui, COLOR_DIM));
}

static void draw_main(struct ui_state *ui, int page) {
    char free_value[32];
    char memory_value[32];
    char ssh_value[80];
    if (ui->info.internal_storage.available)
        r1_cfw_format_bytes(ui->info.internal_storage.free, free_value,
                            sizeof(free_value));
    else
        snprintf(free_value, sizeof(free_value), "NOT AVAILABLE");
    snprintf(memory_value, sizeof(memory_value), "%llu MIB FREE",
             (unsigned long long)(ui->info.memory.available_kib / 1024));
    if (ui->info.ssh_enabled && ui->info.wifi_ip[0])
        snprintf(ssh_value, sizeof(ssh_value), "%s:2222", ui->info.wifi_ip);
    else if (ui->info.ssh_enabled)
        snprintf(ssh_value, sizeof(ssh_value), "ENABLED / NO WI-FI");
    else
        snprintf(ssh_value, sizeof(ssh_value), "OFF");
    draw_ssh_row(ui, page, ssh_value);
    draw_row(ui, page, 1, "WI-FI", ">", COLOR_DIM);
    draw_row(ui, page, 2, "BLUETOOTH", ">", COLOR_DIM);
    draw_row(ui, page, 3, "LAUNCHER", ">", COLOR_DIM);
    draw_row(ui, page, 4, "INTERNAL", free_value, COLOR_DIM);
    draw_row(ui, page, 5, "MICROSD",
             ui->info.sd_storage.available ? ">" : "NOT INSERTED", COLOR_DIM);
    draw_row(ui, page, 6, "MEMORY", memory_value, COLOR_DIM);
    draw_row(ui, page, 7, "SYSTEM INFO", ">", COLOR_DIM);
    draw_row(ui, page, 8, "ABOUT CFW", ">", COLOR_DIM);
}

static void draw_launcher(struct ui_state *ui, int page) {
    unsigned index;
    uint16_t note_color = row_value_color(ui, COLOR_ACCENT);
    for (index = 0; index < 7; ++index) {
        uint32_t bit = r1_cfw_launcher_bit(index);
        char label[32];
        const char *name = r1_cfw_launcher_name(index);
        snprintf(label, sizeof(label), "%s", name ? name : "UNKNOWN");
        draw_row(ui, page, (int)index, label,
                 (ui->launcher_mask & bit) ? "ON" : "OFF",
                 (ui->launcher_mask & bit) ? COLOR_ON : COLOR_DIM);
    }
    r1_display_text(&ui->display, page, 22, 718,
                    "APPLIES ON NEXT PLAYER RESTART", 2, note_color);
    if (ui->launcher_notice) {
        int width = r1_display_text_width(ui->launcher_notice, 1);
        r1_display_text(&ui->display, page,
                        (R1_SCREEN_WIDTH - width) / 2, 752,
                        ui->launcher_notice, 1, note_color);
    } else {
        r1_display_text(&ui->display, page, 22, 748,
                        "CFW LOCKED ON / 4 TO 6 TILES", 2, note_color);
    }
}

static void draw_storage(struct ui_state *ui, int page,
                         const struct r1_cfw_storage_info *storage) {
    char total[32];
    char used[32];
    char free_value[32];
    if (!storage->available) {
        draw_detail_row(ui, page, 0, "STATUS", "NOT INSERTED");
        return;
    }
    r1_cfw_format_bytes(storage->total, total, sizeof(total));
    r1_cfw_format_bytes(storage->used, used, sizeof(used));
    r1_cfw_format_bytes(storage->free, free_value, sizeof(free_value));
    draw_detail_row(ui, page, 0, "TOTAL", total);
    draw_detail_row(ui, page, 1, "USED", used);
    draw_detail_row(ui, page, 2, "AVAILABLE", free_value);
}

static void draw_memory(struct ui_state *ui, int page) {
    char total[32];
    char used[32];
    char available[32];
    snprintf(total, sizeof(total), "%llu MIB",
             (unsigned long long)(ui->info.memory.total_kib / 1024));
    snprintf(used, sizeof(used), "%llu MIB",
             (unsigned long long)(ui->info.memory.used_kib / 1024));
    snprintf(available, sizeof(available), "%llu MIB",
             (unsigned long long)(ui->info.memory.available_kib / 1024));
    draw_detail_row(ui, page, 0, "TOTAL", total);
    draw_detail_row(ui, page, 1, "USED", used);
    draw_detail_row(ui, page, 2, "AVAILABLE", available);
}

static void draw_system(struct ui_state *ui, int page) {
    char uptime[64];
    r1_cfw_format_uptime(ui->info.uptime_seconds, uptime, sizeof(uptime));
    draw_detail_row(ui, page, 0, "CFW VERSION", R1_CFW_VERSION);
    draw_detail_row(ui, page, 1, "STOCK FIRMWARE", R1_CFW_STOCK_VERSION);
    draw_detail_row(ui, page, 2, "KERNEL", ui->info.kernel);
    draw_detail_row(ui, page, 3, "UPTIME", uptime);
    draw_detail_row(ui, page, 4, "WI-FI IP", ui->info.wifi_ip);
    draw_detail_row(ui, page, 5, "HOSTNAME", ui->info.hostname);
}

static void draw_about(struct ui_state *ui, int page) {
    draw_detail_row(ui, page, 0, "PROJECT", "HIBY R1 CFW");
    draw_detail_row(ui, page, 1, "VERSION", R1_CFW_VERSION);
    draw_detail_row(ui, page, 2, "BASE", "HIBY R1 FIRMWARE 1.6");
    draw_detail_row(ui, page, 3, "STATUS", "EXPERIMENTAL");
    draw_detail_row(ui, page, 4, "APPEARANCE",
                    r1_cfw_theme_label(ui->theme));
}

static void draw_appearance(struct ui_state *ui, int page) {
    static const char *labels[] = {
        "SYSTEM DEFAULT", "LIGHT", "DARK", "RETRO HANDHELD"
    };
    unsigned index;
    uint16_t note_color = row_value_color(ui, COLOR_ACCENT);
    for (index = 0; index < R1_CFW_THEME_COUNT; ++index) {
        draw_row(ui, page, (int)index, labels[index],
                 ui->theme == (enum r1_cfw_theme)index ? "SELECTED" : NULL,
                 COLOR_ON);
    }
    r1_display_text(&ui->display, page, 22, 402,
                    "PLAYER THEME APPLIES AFTER RESTART", 2, note_color);
    r1_display_text(&ui->display, page, 22, 432,
                    "CFW PREVIEW UPDATES IMMEDIATELY", 2, note_color);
}

static void publish_test_state(struct ui_state *ui) {
    struct r1_cfw_test_state state;
    int descriptor;
    ssize_t written;
    if (!ui->test_state_path || !*ui->test_state_path) return;
    memset(&state, 0, sizeof(state));
    state.magic = R1_CFW_TEST_STATE_MAGIC;
    state.version = 1;
    state.sequence = ++ui->sequence;
    state.screen_id = ui->screen;
    state.launcher_mask = ui->launcher_mask;
    state.last_action = ui->last_action;
    state.route = (uint32_t)ui->route;
    descriptor = open(ui->test_state_path, O_WRONLY | O_CREAT, 0600);
    if (descriptor >= 0) {
        written = pwrite(descriptor, &state, sizeof(state), 0);
        if (written == (ssize_t)sizeof(state)) {
            int truncate_result =
                ftruncate(descriptor, (off_t)sizeof(state));
            (void)truncate_result;
        }
        close(descriptor);
    }
}

static void render(struct ui_state *ui) {
    int page = ui->display.active_page ? 0 : 1;
    r1_display_clear(&ui->display, page,
                     retro_active(ui) ? RETRO_CANVAS : COLOR_BLACK);
    draw_header(ui, page);
    switch (ui->screen) {
    case R1_CFW_SCREEN_MAIN: draw_main(ui, page); break;
    case R1_CFW_SCREEN_LAUNCHER: draw_launcher(ui, page); break;
    case R1_CFW_SCREEN_INTERNAL:
        draw_storage(ui, page, &ui->info.internal_storage); break;
    case R1_CFW_SCREEN_SD:
        draw_storage(ui, page, &ui->info.sd_storage); break;
    case R1_CFW_SCREEN_MEMORY: draw_memory(ui, page); break;
    case R1_CFW_SCREEN_SYSTEM: draw_system(ui, page); break;
    case R1_CFW_SCREEN_ABOUT: draw_about(ui, page); break;
    case R1_CFW_SCREEN_APPEARANCE: draw_appearance(ui, page); break;
    default: break;
    }
    r1_display_pan(&ui->display, page);
    publish_test_state(ui);
    ui->redraw = 0;
}

static void refresh_info(struct ui_state *ui) {
    r1_cfw_collect_info(&ui->paths, &ui->info);
    ui->launcher_mask = r1_cfw_launcher_load(&ui->paths);
    ui->theme = r1_cfw_theme_load(&ui->paths);
}

static void go_to_page(struct ui_state *ui, enum r1_cfw_screen_id screen) {
    ui->screen = screen;
    ui->last_action = R1_CFW_ACTION_OPEN_PAGE;
    ui->launcher_notice = NULL;
    refresh_info(ui);
    ui->redraw = 1;
}

static void handle_back(struct ui_state *ui) {
    ui->last_action = R1_CFW_ACTION_BACK;
    if (ui->screen == R1_CFW_SCREEN_MAIN) {
        ui->route = R1_CFW_ROUTE_BACK;
        stop_requested = 1;
    } else if (ui->screen == R1_CFW_SCREEN_APPEARANCE) {
        go_to_page(ui, R1_CFW_SCREEN_ABOUT);
        ui->last_action = R1_CFW_ACTION_BACK;
    } else {
        go_to_page(ui, R1_CFW_SCREEN_MAIN);
        ui->last_action = R1_CFW_ACTION_BACK;
    }
}

static void handle_main_tap(struct ui_state *ui, int row) {
    switch (row) {
    case 0:
        (void)r1_cfw_ssh_toggle(&ui->paths);
        ui->last_action = R1_CFW_ACTION_SSH_TOGGLE;
        refresh_info(ui);
        ui->redraw = 1;
        break;
    case 1:
        ui->route = R1_CFW_ROUTE_WIFI;
        ui->last_action = R1_CFW_ACTION_WIFI_ROUTE;
        stop_requested = 1;
        break;
    case 2:
        ui->route = R1_CFW_ROUTE_BLUETOOTH;
        ui->last_action = R1_CFW_ACTION_BLUETOOTH_ROUTE;
        stop_requested = 1;
        break;
    case 3: go_to_page(ui, R1_CFW_SCREEN_LAUNCHER); break;
    case 4: go_to_page(ui, R1_CFW_SCREEN_INTERNAL); break;
    case 5: go_to_page(ui, R1_CFW_SCREEN_SD); break;
    case 6: go_to_page(ui, R1_CFW_SCREEN_MEMORY); break;
    case 7: go_to_page(ui, R1_CFW_SCREEN_SYSTEM); break;
    case 8: go_to_page(ui, R1_CFW_SCREEN_ABOUT); break;
    default: break;
    }
}

static void handle_launcher_tap(struct ui_state *ui, int row) {
    const char *name;
    uint32_t bit;
    uint32_t mask;
    int result;
    if (row < 0 || row >= 7) return;
    ui->launcher_notice = NULL;
    name = r1_cfw_launcher_name((unsigned)row);
    bit = r1_cfw_launcher_bit((unsigned)row);
    if (!name || bit == R1_CFW_TILE_CFW) {
        ui->last_action = R1_CFW_ACTION_LAUNCHER_REJECTED;
        ui->redraw = 1;
        return;
    }
    result = r1_cfw_launcher_set(&ui->paths, name,
                                 !(ui->launcher_mask & bit), &mask);
    if (result < 0) {
        ui->last_action = R1_CFW_ACTION_LAUNCHER_REJECTED;
        if (result == -ENOSPC)
            ui->launcher_notice = R1_CFW_LAUNCHER_LIMIT_MESSAGE;
    } else {
        ui->launcher_mask = mask;
        ui->last_action = R1_CFW_ACTION_LAUNCHER_TOGGLE;
    }
    ui->redraw = 1;
}

static void handle_about_tap(struct ui_state *ui, int row) {
    if (row == 4) go_to_page(ui, R1_CFW_SCREEN_APPEARANCE);
}

static void handle_appearance_tap(struct ui_state *ui, int row) {
    enum r1_cfw_theme selected;
    if (row < 0 || row >= R1_CFW_THEME_COUNT) return;
    selected = (enum r1_cfw_theme)row;
    if (r1_cfw_theme_save(&ui->paths, selected) < 0) {
        ui->last_action = R1_CFW_ACTION_THEME_REJECTED;
    } else {
        ui->theme = selected;
        ui->last_action = R1_CFW_ACTION_THEME_SET;
    }
    ui->redraw = 1;
}

static void handle_gesture(struct ui_state *ui,
                           const struct r1_gesture *gesture) {
    int row;
    if (gesture->kind == R1_GESTURE_DRAG) {
        ui->last_action = R1_CFW_ACTION_DRAG_IGNORED;
        ui->redraw = 1;
        return;
    }
    if (gesture->kind != R1_GESTURE_TAP) return;
    if (gesture->x < 110 && gesture->y < HEADER_HEIGHT) {
        handle_back(ui);
        return;
    }
    if (gesture->y < HEADER_HEIGHT) return;
    row = (gesture->y - HEADER_HEIGHT) / ROW_HEIGHT;
    if (ui->screen == R1_CFW_SCREEN_MAIN) handle_main_tap(ui, row);
    else if (ui->screen == R1_CFW_SCREEN_LAUNCHER)
        handle_launcher_tap(ui, row);
    else if (ui->screen == R1_CFW_SCREEN_ABOUT)
        handle_about_tap(ui, row);
    else if (ui->screen == R1_CFW_SCREEN_APPEARANCE)
        handle_appearance_tap(ui, row);
}

static int environment_milliseconds(const char *name) {
    const char *value = getenv(name);
    long parsed;
    if (!value || !*value) return 0;
    parsed = parse_long(value, name);
    return parsed > 0 && parsed <= 3600000 ? (int)parsed : 0;
}

static int run_ui(const struct options *options,
                  const struct r1_cfw_paths *paths) {
    struct ui_state ui;
    int auto_exit_ms = environment_milliseconds("R1_CFW_TEST_AUTO_EXIT_MS");
    int crash_after_ms = environment_milliseconds("R1_CFW_TEST_CRASH_AFTER_MS");
    int64_t started = monotonic_ms();
    int64_t next_pan = started;
    int result;
    memset(&ui, 0, sizeof(ui));
    ui.paths = *paths;
    ui.screen = R1_CFW_SCREEN_MAIN;
    ui.route = R1_CFW_ROUTE_BACK;
    ui.touch_fd = options->touch_fd;
    ui.test_state_path = getenv("R1_CFW_TEST_STATE_PATH");
    ui.redraw = 1;
    r1_touch_init(&ui.touch);
    refresh_info(&ui);

    result = r1_display_init(&ui.display, options->framebuffer_fd,
                             options->test_mode, options->test_stride,
                             options->test_bpp, options->test_bytes,
                             options->test_packed_rgb565);
    if (result < 0) {
        fprintf(stderr, "display initialization failed: %d\n", result);
        return 3;
    }
    if (ui.touch_fd < 0) {
        r1_display_destroy(&ui.display);
        fprintf(stderr, "missing inherited touch descriptor\n");
        return 4;
    }
    (void)fcntl(ui.touch_fd, F_SETFL,
                fcntl(ui.touch_fd, F_GETFL) | O_NONBLOCK);

    while (!stop_requested) {
        struct pollfd reader;
        struct r1_gesture gesture;
        int64_t now;
        int poll_result;
        if (ui.redraw) render(&ui);
        reader.fd = ui.touch_fd;
        reader.events = POLLIN;
        reader.revents = 0;
        poll_result = poll(&reader, 1, 20);
        if (poll_result < 0 && errno != EINTR) {
            result = -errno;
            fprintf(stderr, "touch poll failed: %d\n", result);
            r1_display_destroy(&ui.display);
            return 5;
        }
        if (poll_result > 0 && (reader.revents & POLLNVAL)) {
            fprintf(stderr, "invalid inherited touch descriptor\n");
            r1_display_destroy(&ui.display);
            return 5;
        }
        if (poll_result > 0 && (reader.revents & (POLLIN | POLLHUP)) &&
            r1_touch_read(ui.touch_fd, &ui.touch, &gesture) > 0) {
            handle_gesture(&ui, &gesture);
        }
        now = monotonic_ms();
        if (now >= next_pan) {
            r1_display_pan(&ui.display, ui.display.active_page);
            next_pan = now + 33;
        }
        if (crash_after_ms && now - started >= crash_after_ms) abort();
        if (auto_exit_ms && now - started >= auto_exit_ms) {
            ui.last_action = R1_CFW_ACTION_BACK;
            ui.route = R1_CFW_ROUTE_BACK;
            break;
        }
    }
    publish_test_state(&ui);
    r1_display_destroy(&ui.display);
    return ui.route;
}

int main(int argc, char **argv) {
    struct options options = parse_options(argc, argv);
    struct r1_cfw_paths paths;
    int command_result;
    signal(SIGINT, request_stop);
    signal(SIGTERM, request_stop);
    r1_cfw_paths_init(&paths);
    command_result = run_data_command(&options, &paths);
    if (command_result >= 0) return command_result;
    if (options.framebuffer_fd < 0 || options.touch_fd < 0) {
        usage(stderr, argv[0]);
        return 2;
    }
    return run_ui(&options, &paths);
}
