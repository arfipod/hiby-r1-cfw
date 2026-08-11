#define _GNU_SOURCE

#include "r1_cfw_data.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

struct launcher_entry {
    const char *name;
    uint32_t bit;
};

struct theme_entry {
    enum r1_cfw_theme theme;
    const char *name;
    const char *label;
};

static const struct launcher_entry launcher_entries[] = {
    {"music", R1_CFW_TILE_MUSIC},
    {"stream", R1_CFW_TILE_STREAM},
    {"wireless", R1_CFW_TILE_WIRELESS},
    {"ebook", R1_CFW_TILE_EBOOK},
    {"system", R1_CFW_TILE_SYSTEM},
    {"cfw", R1_CFW_TILE_CFW},
    {"about", R1_CFW_TILE_ABOUT},
};

static const struct theme_entry theme_entries[] = {
    {R1_CFW_THEME_STOCK, "stock", "SYSTEM"},
    {R1_CFW_THEME_LIGHT, "light", "LIGHT"},
    {R1_CFW_THEME_DARK, "dark", "DARK"},
    {R1_CFW_THEME_RETRO, "retro", "RETRO"},
};

static void copy_environment(char *destination, size_t size,
                             const char *name, const char *fallback) {
    const char *value = getenv(name);
    if (!value || !*value) value = fallback;
    snprintf(destination, size, "%s", value);
}

static unsigned visible_tiles(uint32_t mask) {
    unsigned count = 0;
    mask &= R1_CFW_LAUNCHER_ALL;
    while (mask) {
        count += mask & 1U;
        mask >>= 1;
    }
    return count;
}

static int valid_mask(uint32_t mask) {
    unsigned count;
    if (mask & ~R1_CFW_LAUNCHER_ALL) return 0;
    if (!(mask & R1_CFW_TILE_CFW)) return 0;
    count = visible_tiles(mask);
    return count >= R1_CFW_LAUNCHER_MIN_TILES &&
           count <= R1_CFW_LAUNCHER_MAX_TILES;
}

static int valid_theme(enum r1_cfw_theme theme) {
    return theme >= R1_CFW_THEME_STOCK && theme < R1_CFW_THEME_COUNT;
}

void r1_cfw_paths_init(struct r1_cfw_paths *paths) {
    memset(paths, 0, sizeof(*paths));
    copy_environment(paths->proc_root, sizeof(paths->proc_root),
                     "R1_CFW_PROC_ROOT", "/proc");
    copy_environment(paths->data_dir, sizeof(paths->data_dir),
                     "R1_CFW_DATA_DIR", "/usr/data/r1-cfw");
    copy_environment(paths->internal_path, sizeof(paths->internal_path),
                     "R1_CFW_INTERNAL_PATH", "/usr/data");
    copy_environment(paths->sd_path, sizeof(paths->sd_path),
                     "R1_CFW_SD_PATH", "/usr/data/mnt/sd_0");
    copy_environment(paths->ssh_control, sizeof(paths->ssh_control),
                     "R1_CFW_SSH_CONTROL", "/usr/bin/r1-ssh-control");
}

static int joined_path(char *output, size_t output_size, const char *root,
                       const char *leaf) {
    int length = snprintf(output, output_size, "%s/%s", root, leaf);
    return length >= 0 && (size_t)length < output_size ? 0 : -ENAMETOOLONG;
}

static int ensure_data_directory(const struct r1_cfw_paths *paths) {
    if (mkdir(paths->data_dir, 0700) < 0 && errno != EEXIST) return -errno;
    if (chmod(paths->data_dir, 0700) < 0 && errno != EROFS) return -errno;
    return 0;
}

static int atomic_write_config(const struct r1_cfw_paths *paths,
                               const char *leaf, const char *content) {
    char target[512];
    char temporary[560];
    int descriptor = -1;
    int directory_descriptor = -1;
    int result;
    size_t length = strlen(content);
    ssize_t written;

    result = ensure_data_directory(paths);
    if (result < 0) return result;
    if (joined_path(target, sizeof(target), paths->data_dir, leaf) < 0)
        return -ENAMETOOLONG;
    if (snprintf(temporary, sizeof(temporary), "%s.tmp.%ld", target,
                 (long)getpid()) >= (int)sizeof(temporary)) {
        return -ENAMETOOLONG;
    }
    descriptor = open(temporary, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (descriptor < 0) return -errno;
    result = 0;
    written = write(descriptor, content, length);
    if (written != (ssize_t)length) result = errno ? -errno : -EIO;
    if (result == 0 && fsync(descriptor) < 0) result = -errno;
    if (close(descriptor) < 0 && result == 0) result = -errno;
    descriptor = -1;
    if (result == 0 && rename(temporary, target) < 0) result = -errno;
    if (result < 0) unlink(temporary);
    if (result == 0) {
        directory_descriptor = open(paths->data_dir, O_RDONLY | O_DIRECTORY);
        if (directory_descriptor >= 0) {
            (void)fsync(directory_descriptor);
            close(directory_descriptor);
        }
    }
    return result;
}

uint32_t r1_cfw_launcher_load(const struct r1_cfw_paths *paths) {
    char path[512];
    char line[80];
    char *end = NULL;
    size_t line_length;
    unsigned long value;
    FILE *stream;
    if (joined_path(path, sizeof(path), paths->data_dir, "launcher.conf") < 0)
        return R1_CFW_LAUNCHER_DEFAULT;
    stream = fopen(path, "r");
    if (!stream) return R1_CFW_LAUNCHER_DEFAULT;
    if (!fgets(line, sizeof(line), stream)) {
        fclose(stream);
        (void)r1_cfw_launcher_save(paths, R1_CFW_LAUNCHER_DEFAULT);
        return R1_CFW_LAUNCHER_DEFAULT;
    }
    line_length = strlen(line);
    if (fgetc(stream) != EOF || ferror(stream)) {
        fclose(stream);
        (void)r1_cfw_launcher_save(paths, R1_CFW_LAUNCHER_DEFAULT);
        return R1_CFW_LAUNCHER_DEFAULT;
    }
    fclose(stream);
    if (line_length != 17U ||
        strncmp(line, "launcher_mask=", 14) != 0 || line[16] != '\n') {
        (void)r1_cfw_launcher_save(paths, R1_CFW_LAUNCHER_DEFAULT);
        return R1_CFW_LAUNCHER_DEFAULT;
    }
    if (!((line[14] >= '0' && line[14] <= '9') ||
          (line[14] >= 'a' && line[14] <= 'f')) ||
        !((line[15] >= '0' && line[15] <= '9') ||
          (line[15] >= 'a' && line[15] <= 'f'))) {
        (void)r1_cfw_launcher_save(paths, R1_CFW_LAUNCHER_DEFAULT);
        return R1_CFW_LAUNCHER_DEFAULT;
    }
    errno = 0;
    value = strtoul(line + 14, &end, 16);
    if (errno || end != line + 16 || *end != '\n' ||
        value > R1_CFW_LAUNCHER_ALL) {
        (void)r1_cfw_launcher_save(paths, R1_CFW_LAUNCHER_DEFAULT);
        return R1_CFW_LAUNCHER_DEFAULT;
    }
    if (!valid_mask((uint32_t)value)) {
        (void)r1_cfw_launcher_save(paths, R1_CFW_LAUNCHER_DEFAULT);
        return R1_CFW_LAUNCHER_DEFAULT;
    }
    return (uint32_t)value;
}

int r1_cfw_launcher_save(const struct r1_cfw_paths *paths, uint32_t mask) {
    char content[32];
    if (!valid_mask(mask)) return -ERANGE;
    if (snprintf(content, sizeof(content), "launcher_mask=%02x\n", mask) >=
        (int)sizeof(content)) {
        return -EOVERFLOW;
    }
    return atomic_write_config(paths, "launcher.conf", content);
}

const char *r1_cfw_launcher_name(unsigned index) {
    if (index >= sizeof(launcher_entries) / sizeof(launcher_entries[0]))
        return NULL;
    return launcher_entries[index].name;
}

uint32_t r1_cfw_launcher_bit(unsigned index) {
    if (index >= sizeof(launcher_entries) / sizeof(launcher_entries[0]))
        return 0;
    return launcher_entries[index].bit;
}

int r1_cfw_launcher_set(const struct r1_cfw_paths *paths, const char *name,
                        int enabled, uint32_t *saved_mask) {
    uint32_t mask = r1_cfw_launcher_load(paths);
    size_t index;
    for (index = 0; index < sizeof(launcher_entries) / sizeof(launcher_entries[0]);
         ++index) {
        if (strcmp(name, launcher_entries[index].name) != 0) continue;
        if (launcher_entries[index].bit == R1_CFW_TILE_CFW && !enabled)
            return -EPERM;
        if (enabled && !(mask & launcher_entries[index].bit) &&
            visible_tiles(mask) >= R1_CFW_LAUNCHER_MAX_TILES) {
            return -ENOSPC;
        }
        if (enabled)
            mask |= launcher_entries[index].bit;
        else
            mask &= ~launcher_entries[index].bit;
        if (!valid_mask(mask)) return -ERANGE;
        if (r1_cfw_launcher_save(paths, mask) < 0) return -EIO;
        if (saved_mask) *saved_mask = mask;
        return 0;
    }
    return -ENOENT;
}

const char *r1_cfw_theme_name(enum r1_cfw_theme theme) {
    size_t index;
    for (index = 0; index < sizeof(theme_entries) / sizeof(theme_entries[0]);
         ++index) {
        if (theme_entries[index].theme == theme) return theme_entries[index].name;
    }
    return theme_entries[0].name;
}

const char *r1_cfw_theme_label(enum r1_cfw_theme theme) {
    size_t index;
    for (index = 0; index < sizeof(theme_entries) / sizeof(theme_entries[0]);
         ++index) {
        if (theme_entries[index].theme == theme) return theme_entries[index].label;
    }
    return theme_entries[0].label;
}

int r1_cfw_theme_save(const struct r1_cfw_paths *paths,
                      enum r1_cfw_theme theme) {
    char content[48];
    const char *name;
    if (!valid_theme(theme)) return -ERANGE;
    name = r1_cfw_theme_name(theme);
    if (snprintf(content, sizeof(content), "theme=%s\n", name) >=
        (int)sizeof(content)) {
        return -EOVERFLOW;
    }
    return atomic_write_config(paths, "theme.conf", content);
}

enum r1_cfw_theme r1_cfw_theme_load(const struct r1_cfw_paths *paths) {
    char path[512];
    char line[80];
    FILE *stream;
    size_t index;
    size_t length;

    if (joined_path(path, sizeof(path), paths->data_dir, "theme.conf") < 0)
        return R1_CFW_THEME_DEFAULT;
    stream = fopen(path, "r");
    if (!stream) return R1_CFW_THEME_DEFAULT;
    if (!fgets(line, sizeof(line), stream)) {
        fclose(stream);
        (void)r1_cfw_theme_save(paths, R1_CFW_THEME_DEFAULT);
        return R1_CFW_THEME_DEFAULT;
    }
    length = strlen(line);
    if (fgetc(stream) != EOF || ferror(stream)) {
        fclose(stream);
        (void)r1_cfw_theme_save(paths, R1_CFW_THEME_DEFAULT);
        return R1_CFW_THEME_DEFAULT;
    }
    fclose(stream);
    if (length < 8U || strncmp(line, "theme=", 6) != 0 ||
        line[length - 1] != '\n') {
        (void)r1_cfw_theme_save(paths, R1_CFW_THEME_DEFAULT);
        return R1_CFW_THEME_DEFAULT;
    }
    line[length - 1] = '\0';
    for (index = 0; index < sizeof(theme_entries) / sizeof(theme_entries[0]);
         ++index) {
        if (strcmp(line + 6, theme_entries[index].name) == 0)
            return theme_entries[index].theme;
    }
    (void)r1_cfw_theme_save(paths, R1_CFW_THEME_DEFAULT);
    return R1_CFW_THEME_DEFAULT;
}

int r1_cfw_theme_set(const struct r1_cfw_paths *paths, const char *name,
                     enum r1_cfw_theme *saved_theme) {
    size_t index;
    if (!name) return -EINVAL;
    for (index = 0; index < sizeof(theme_entries) / sizeof(theme_entries[0]);
         ++index) {
        if (strcmp(name, theme_entries[index].name) != 0) continue;
        if (r1_cfw_theme_save(paths, theme_entries[index].theme) < 0)
            return -EIO;
        if (saved_theme) *saved_theme = theme_entries[index].theme;
        return 0;
    }
    return -ENOENT;
}

static int run_controller(const char *controller, const char *command) {
    const char *emulator_wrapper = getenv("R1_QEMU_EXEC_WRAPPER");
    pid_t child;
    pid_t waited;
    int status;
    child = fork();
    if (child < 0) return -errno;
    if (child == 0) {
        int null_descriptor = open("/dev/null", O_WRONLY);
        if (null_descriptor >= 0) {
            (void)dup2(null_descriptor, STDOUT_FILENO);
            (void)dup2(null_descriptor, STDERR_FILENO);
            if (null_descriptor > STDERR_FILENO) close(null_descriptor);
        }
        if (emulator_wrapper && *emulator_wrapper) {
            execl(emulator_wrapper, emulator_wrapper, "/bin/sh", controller,
                  command, (char *)NULL);
        } else {
            execl(controller, controller, command, (char *)NULL);
        }
        _exit(127);
    }
    do {
        waited = waitpid(child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    if (waited < 0) return -errno;
    if (!WIFEXITED(status)) return -EIO;
    return WEXITSTATUS(status);
}

int r1_cfw_ssh_is_enabled(const struct r1_cfw_paths *paths) {
    return run_controller(paths->ssh_control, "is-enabled") == 0;
}

int r1_cfw_ssh_toggle(const struct r1_cfw_paths *paths) {
    int status = run_controller(paths->ssh_control, "toggle");
    return status == 0 ? 0 : -EIO;
}

static void collect_storage(const char *path, struct r1_cfw_storage_info *info) {
    struct statvfs statistics;
    memset(info, 0, sizeof(*info));
    if (statvfs(path, &statistics) < 0) return;
    info->total = (uint64_t)statistics.f_blocks * statistics.f_frsize;
    info->free = (uint64_t)statistics.f_bavail * statistics.f_frsize;
    info->used = info->total -
                 (uint64_t)statistics.f_bfree * statistics.f_frsize;
    info->available = 1;
}

static int mount_is_present(const struct r1_cfw_paths *paths,
                            const char *mount_path) {
    char path[512];
    char device[256];
    char mount[256];
    char filesystem[64];
    char options[256];
    FILE *stream;
    if (getenv("R1_CFW_ASSUME_SD_MOUNTED")) return 1;
    if (joined_path(path, sizeof(path), paths->proc_root, "mounts") < 0)
        return 0;
    stream = fopen(path, "r");
    if (!stream) return 0;
    while (fscanf(stream, "%255s %255s %63s %255s %*d %*d", device, mount,
                  filesystem, options) == 4) {
        if (strcmp(mount, mount_path) == 0) {
            fclose(stream);
            return 1;
        }
    }
    fclose(stream);
    return 0;
}

static void collect_memory(const struct r1_cfw_paths *paths,
                           struct r1_cfw_memory_info *memory) {
    char path[512];
    char line[256];
    char key[64];
    unsigned long long value;
    uint64_t free_kib = 0;
    uint64_t buffers_kib = 0;
    uint64_t cached_kib = 0;
    FILE *stream;
    memset(memory, 0, sizeof(*memory));
    if (joined_path(path, sizeof(path), paths->proc_root, "meminfo") < 0)
        return;
    stream = fopen(path, "r");
    if (!stream) return;
    while (fgets(line, sizeof(line), stream)) {
        if (sscanf(line, "%63[^:]: %llu", key, &value) != 2) continue;
        if (strcmp(key, "MemTotal") == 0) memory->total_kib = value;
        else if (strcmp(key, "MemAvailable") == 0)
            memory->available_kib = value;
        else if (strcmp(key, "MemFree") == 0) free_kib = value;
        else if (strcmp(key, "Buffers") == 0) buffers_kib = value;
        else if (strcmp(key, "Cached") == 0) cached_kib = value;
    }
    fclose(stream);
    if (!memory->available_kib)
        memory->available_kib = free_kib + buffers_kib + cached_kib;
    if (memory->available_kib > memory->total_kib)
        memory->available_kib = memory->total_kib;
    memory->used_kib = memory->total_kib - memory->available_kib;
}

static uint64_t collect_uptime(const struct r1_cfw_paths *paths) {
    char path[512];
    double seconds = 0;
    FILE *stream;
    if (joined_path(path, sizeof(path), paths->proc_root, "uptime") < 0)
        return 0;
    stream = fopen(path, "r");
    if (!stream) return 0;
    if (fscanf(stream, "%lf", &seconds) != 1) seconds = 0;
    fclose(stream);
    return seconds > 0 ? (uint64_t)seconds : 0;
}

static void collect_wifi_ip(char *output, size_t output_size) {
    struct ifreq request;
    int descriptor;
    const char *test_value = getenv("R1_CFW_TEST_WIFI_IP");
    memset(output, 0, output_size);
    if (test_value) {
        snprintf(output, output_size, "%s", test_value);
        return;
    }
    descriptor = socket(AF_INET, SOCK_DGRAM, 0);
    if (descriptor < 0) return;
    memset(&request, 0, sizeof(request));
    snprintf(request.ifr_name, sizeof(request.ifr_name), "wlan0");
    if (ioctl(descriptor, SIOCGIFADDR, &request) == 0) {
        struct sockaddr_in *address = (struct sockaddr_in *)&request.ifr_addr;
        (void)inet_ntop(AF_INET, &address->sin_addr, output, output_size);
    }
    close(descriptor);
}

int r1_cfw_collect_info(const struct r1_cfw_paths *paths,
                        struct r1_cfw_system_info *info) {
    struct utsname system_name;
    memset(info, 0, sizeof(*info));
    info->ssh_enabled = r1_cfw_ssh_is_enabled(paths);
    collect_wifi_ip(info->wifi_ip, sizeof(info->wifi_ip));
    if (gethostname(info->hostname, sizeof(info->hostname) - 1) < 0)
        info->hostname[0] = '\0';
    if (uname(&system_name) == 0) {
        snprintf(info->kernel, sizeof(info->kernel), "%s %s",
                 system_name.sysname, system_name.release);
    }
    info->uptime_seconds = collect_uptime(paths);
    collect_storage(paths->internal_path, &info->internal_storage);
    if (mount_is_present(paths, paths->sd_path))
        collect_storage(paths->sd_path, &info->sd_storage);
    collect_memory(paths, &info->memory);
    return 0;
}

void r1_cfw_format_bytes(uint64_t bytes, char *output, size_t output_size) {
    static const char *units[] = {"B", "KiB", "MiB", "GiB", "TiB"};
    double value = (double)bytes;
    unsigned unit = 0;
    while (value >= 1024.0 && unit + 1 < sizeof(units) / sizeof(units[0])) {
        value /= 1024.0;
        ++unit;
    }
    if (unit == 0)
        snprintf(output, output_size, "%llu %s",
                 (unsigned long long)bytes, units[unit]);
    else
        snprintf(output, output_size, "%.1f %s", value, units[unit]);
}

void r1_cfw_format_uptime(uint64_t seconds, char *output, size_t output_size) {
    uint64_t days = seconds / 86400;
    uint64_t hours = (seconds % 86400) / 3600;
    uint64_t minutes = (seconds % 3600) / 60;
    if (days)
        snprintf(output, output_size, "%llud %lluh %llum",
                 (unsigned long long)days, (unsigned long long)hours,
                 (unsigned long long)minutes);
    else
        snprintf(output, output_size, "%lluh %llum",
                 (unsigned long long)hours, (unsigned long long)minutes);
}
