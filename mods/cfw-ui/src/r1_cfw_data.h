#ifndef R1_CFW_DATA_H
#define R1_CFW_DATA_H

#include <stddef.h>
#include <stdint.h>

#define R1_CFW_VERSION "0.1 experimental"
#define R1_CFW_STOCK_VERSION "1.6"

enum r1_cfw_launcher_bit {
    R1_CFW_TILE_MUSIC = 1U << 0,
    R1_CFW_TILE_STREAM = 1U << 1,
    R1_CFW_TILE_WIRELESS = 1U << 2,
    R1_CFW_TILE_EBOOK = 1U << 3,
    R1_CFW_TILE_SYSTEM = 1U << 4,
    R1_CFW_TILE_CFW = 1U << 5,
    R1_CFW_TILE_ABOUT = 1U << 6,
};

#define R1_CFW_LAUNCHER_ALL 0x7fU
#define R1_CFW_LAUNCHER_DEFAULT 0x71U
#define R1_CFW_LAUNCHER_MIN_TILES 4U
#define R1_CFW_LAUNCHER_MAX_TILES 6U
#define R1_CFW_LAUNCHER_LIMIT_MESSAGE \
    "Maximum 6 launcher tiles. Disable one first."

enum r1_cfw_theme {
    R1_CFW_THEME_STOCK = 0,
    R1_CFW_THEME_LIGHT = 1,
    R1_CFW_THEME_DARK = 2,
    R1_CFW_THEME_RETRO = 3,
    R1_CFW_THEME_COUNT = 4,
};

#define R1_CFW_THEME_DEFAULT R1_CFW_THEME_STOCK

struct r1_cfw_paths {
    char proc_root[256];
    char data_dir[256];
    char internal_path[256];
    char sd_path[256];
    char ssh_control[256];
};

struct r1_cfw_storage_info {
    int available;
    uint64_t total;
    uint64_t free;
    uint64_t used;
};

struct r1_cfw_memory_info {
    uint64_t total_kib;
    uint64_t available_kib;
    uint64_t used_kib;
};

struct r1_cfw_system_info {
    int ssh_enabled;
    char wifi_ip[64];
    char hostname[128];
    char kernel[192];
    uint64_t uptime_seconds;
    struct r1_cfw_storage_info internal_storage;
    struct r1_cfw_storage_info sd_storage;
    struct r1_cfw_memory_info memory;
};

void r1_cfw_paths_init(struct r1_cfw_paths *paths);

uint32_t r1_cfw_launcher_load(const struct r1_cfw_paths *paths);
int r1_cfw_launcher_save(const struct r1_cfw_paths *paths, uint32_t mask);
int r1_cfw_launcher_set(const struct r1_cfw_paths *paths, const char *name,
                        int enabled, uint32_t *saved_mask);
const char *r1_cfw_launcher_name(unsigned index);
uint32_t r1_cfw_launcher_bit(unsigned index);

enum r1_cfw_theme r1_cfw_theme_load(const struct r1_cfw_paths *paths);
int r1_cfw_theme_save(const struct r1_cfw_paths *paths,
                      enum r1_cfw_theme theme);
int r1_cfw_theme_set(const struct r1_cfw_paths *paths, const char *name,
                     enum r1_cfw_theme *saved_theme);
const char *r1_cfw_theme_name(enum r1_cfw_theme theme);
const char *r1_cfw_theme_label(enum r1_cfw_theme theme);

int r1_cfw_ssh_is_enabled(const struct r1_cfw_paths *paths);
int r1_cfw_ssh_toggle(const struct r1_cfw_paths *paths);
int r1_cfw_collect_info(const struct r1_cfw_paths *paths,
                        struct r1_cfw_system_info *info);

void r1_cfw_format_bytes(uint64_t bytes, char *output, size_t output_size);
void r1_cfw_format_uptime(uint64_t seconds, char *output, size_t output_size);

#endif
