#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/fs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#ifndef RENAME_NOREPLACE
#define RENAME_NOREPLACE (1U << 0)
#endif

#define PROTOCOL_VERSION 1
#define HELPER_VERSION "0.1.0"
#define DEFAULT_SD_ROOT "/usr/data/mnt/sd_0"
#define DEFAULT_MOUNTS "/proc/mounts"
#define DEFAULT_LOCK "/run/r1-filectl.lock"
#define PRIVATE_ROOT ".r1-manager"
#define TRASH_ROOT ".r1-manager/trash"
#define INCOMING_ROOT ".r1-manager/incoming"
#define MAX_RELATIVE_PATH 4095U
#define MAX_NEW_NAME 255U
#define SPACE_RESERVE (1024U * 1024U)

struct context {
    const char *root_path;
    const char *mounts_path;
    const char *lock_path;
    int root_fd;
    int lock_fd;
};

struct bytes {
    unsigned char *data;
    size_t length;
};

struct names {
    char **items;
    size_t count;
    size_t capacity;
};

static const char *env_or(const char *name, const char *fallback) {
    const char *value = getenv(name);
    return value && *value ? value : fallback;
}

static void json_bytes(const unsigned char *data, size_t length) {
    size_t index;
    fputc('"', stdout);
    for (index = 0; index < length; ++index) {
        unsigned char value = data[index];
        switch (value) {
        case '"': fputs("\\\"", stdout); break;
        case '\\': fputs("\\\\", stdout); break;
        case '\b': fputs("\\b", stdout); break;
        case '\f': fputs("\\f", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if (value < 0x20U)
                fprintf(stdout, "\\u%04x", (unsigned)value);
            else
                fputc((int)value, stdout);
        }
    }
    fputc('"', stdout);
}

static void json_string(const char *value) {
    const unsigned char *data = (const unsigned char *)(value ? value : "");
    json_bytes(data, strlen((const char *)data));
}

static int fail(const char *code, const char *message, int error_number) {
    fputs("{\"ok\":false,\"code\":", stdout);
    json_string(code);
    fputs(",\"message\":", stdout);
    json_string(message);
    if (error_number) fprintf(stdout, ",\"errno\":%d", error_number);
    fputs("}\n", stdout);
    return 1;
}

static int fail_errno(const char *fallback_message, int error_number) {
    switch (error_number) {
    case ENOENT:
        return fail("SOURCE_NOT_FOUND",
                    "The requested file or directory no longer exists.",
                    error_number);
    case EEXIST:
        return fail("DESTINATION_EXISTS",
                    "An item already exists at the destination.", error_number);
    case ENOSPC:
        return fail("NOT_ENOUGH_SPACE",
                    "The microSD card does not have enough free space.",
                    error_number);
    case ENOTEMPTY:
        return fail("DIRECTORY_NOT_EMPTY", "The directory is not empty.",
                    error_number);
    case EROFS:
        return fail("READ_ONLY_FILESYSTEM",
                    "The microSD card is mounted read-only.", error_number);
    case EPERM:
    case EACCES:
        return fail("PERMISSION_DENIED",
                    "The filesystem rejected the requested operation.",
                    error_number);
    case ENODEV:
    case ESTALE:
        return fail("CARD_REMOVED",
                    "The microSD card became unavailable during the operation.",
                    error_number);
    case EXDEV:
        return fail("CROSS_DEVICE_MOVE",
                    "The source and destination are not on the same filesystem.",
                    error_number);
    default:
        return fail("IO_ERROR", fallback_message, error_number);
    }
}

static int b64_value(unsigned char value) {
    if (value >= 'A' && value <= 'Z') return value - 'A';
    if (value >= 'a' && value <= 'z') return value - 'a' + 26;
    if (value >= '0' && value <= '9') return value - '0' + 52;
    if (value == '-' || value == '+') return 62;
    if (value == '_' || value == '/') return 63;
    return -1;
}

static int b64_decode(const char *text, struct bytes *result) {
    size_t length;
    size_t input = 0;
    size_t output = 0;
    unsigned accumulator = 0;
    unsigned bits = 0;
    memset(result, 0, sizeof(*result));
    if (!text) return -EINVAL;
    if (strcmp(text, "-") == 0) text = "";
    length = strlen(text);
    if (length > 5500U) return -ENAMETOOLONG;
    result->data = calloc(length * 3U / 4U + 4U, 1U);
    if (!result->data) return -ENOMEM;
    while (input < length) {
        int value = b64_value((unsigned char)text[input++]);
        if (value < 0) goto invalid;
        accumulator = (accumulator << 6) | (unsigned)value;
        bits += 6U;
        if (bits >= 8U) {
            bits -= 8U;
            result->data[output++] =
                (unsigned char)((accumulator >> bits) & 0xffU);
        }
    }
    if (bits && (accumulator & ((1U << bits) - 1U))) goto invalid;
    if (memchr(result->data, '\0', output)) goto invalid;
    result->data[output] = '\0';
    result->length = output;
    return 0;
invalid:
    free(result->data);
    memset(result, 0, sizeof(*result));
    return -EINVAL;
}

static char *b64_encode(const unsigned char *data, size_t length) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    char *result = malloc((length * 4U + 2U) / 3U + 1U);
    size_t input = 0;
    size_t output = 0;
    if (!result) return NULL;
    while (input + 3U <= length) {
        unsigned value = ((unsigned)data[input] << 16) |
                         ((unsigned)data[input + 1U] << 8) |
                         data[input + 2U];
        result[output++] = alphabet[(value >> 18) & 63U];
        result[output++] = alphabet[(value >> 12) & 63U];
        result[output++] = alphabet[(value >> 6) & 63U];
        result[output++] = alphabet[value & 63U];
        input += 3U;
    }
    if (input < length) {
        unsigned value = (unsigned)data[input] << 16;
        result[output++] = alphabet[(value >> 18) & 63U];
        if (input + 1U < length) {
            value |= (unsigned)data[input + 1U] << 8;
            result[output++] = alphabet[(value >> 12) & 63U];
            result[output++] = alphabet[(value >> 6) & 63U];
        } else {
            result[output++] = alphabet[(value >> 12) & 63U];
        }
    }
    result[output] = '\0';
    return result;
}

static void bytes_free(struct bytes *value) {
    free(value->data);
    memset(value, 0, sizeof(*value));
}

static int valid_relative(const char *path, int allow_root, int allow_private) {
    const char *component;
    const char *cursor;
    if (!path) return -EINVAL;
    if (!*path) return allow_root ? 0 : -EINVAL;
    if (path[0] == '/' || strlen(path) > MAX_RELATIVE_PATH) return -EINVAL;
    component = cursor = path;
    for (;;) {
        if (*cursor == '/' || *cursor == '\0') {
            size_t length = (size_t)(cursor - component);
            if (!length || (length == 1U && component[0] == '.') ||
                (length == 2U && component[0] == '.' && component[1] == '.'))
                return -EINVAL;
            if (!allow_private && component == path &&
                length == strlen(PRIVATE_ROOT) &&
                memcmp(component, PRIVATE_ROOT, length) == 0)
                return -EPERM;
            if (!*cursor) break;
            component = cursor + 1;
        }
        ++cursor;
    }
    return 0;
}

static int valid_new_name(const unsigned char *name, size_t length) {
    size_t index;
    static const char forbidden[] = "\\/:*?\"<>|";
    if (!name || !length || length > MAX_NEW_NAME) return -EINVAL;
    if ((length == 1U && name[0] == '.') ||
        (length == 2U && name[0] == '.' && name[1] == '.')) return -EINVAL;
    if (name[length - 1U] == ' ' || name[length - 1U] == '.') return -EINVAL;
    for (index = 0; index < length; ++index)
        if (name[index] < 0x20U || strchr(forbidden, (int)name[index]))
            return -EINVAL;
    return 0;
}

static int mounted(const struct context *context) {
    FILE *stream;
    char device[512], mount[PATH_MAX], type[128], options[512];
    if (getenv("R1_FILECTL_ASSUME_MOUNTED")) return 1;
    stream = fopen(context->mounts_path, "r");
    if (!stream) return 0;
    while (fscanf(stream, "%511s %4095s %127s %511s %*d %*d",
                  device, mount, type, options) == 4) {
        if (strcmp(mount, context->root_path) == 0) {
            fclose(stream);
            return 1;
        }
    }
    fclose(stream);
    return 0;
}

static int context_open(struct context *context, int writable) {
    memset(context, 0, sizeof(*context));
    context->root_fd = context->lock_fd = -1;
    context->root_path = env_or("R1_FILECTL_SD_ROOT", DEFAULT_SD_ROOT);
    context->mounts_path = env_or("R1_FILECTL_PROC_MOUNTS", DEFAULT_MOUNTS);
    context->lock_path = env_or("R1_FILECTL_LOCK_PATH", DEFAULT_LOCK);
    if (!mounted(context)) return -ENODEV;
    context->root_fd = open(context->root_path,
                            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (context->root_fd < 0) return -errno;
    if (writable) {
        context->lock_fd = open(context->lock_path,
                                O_RDWR | O_CREAT | O_CLOEXEC, 0600);
        if (context->lock_fd < 0 || flock(context->lock_fd, LOCK_EX) < 0) {
            int saved = errno;
            if (context->lock_fd >= 0) close(context->lock_fd);
            close(context->root_fd);
            context->root_fd = context->lock_fd = -1;
            return -saved;
        }
    }
    return 0;
}

static void context_close(struct context *context) {
    if (context->lock_fd >= 0) {
        (void)flock(context->lock_fd, LOCK_UN);
        close(context->lock_fd);
    }
    if (context->root_fd >= 0) close(context->root_fd);
    context->root_fd = context->lock_fd = -1;
}

static int duplicate_fd(int descriptor) {
    int result = fcntl(descriptor, F_DUPFD_CLOEXEC, 3);
    return result < 0 ? -errno : result;
}

static int next_component(const char **cursor, char *component,
                          size_t capacity) {
    const char *slash = strchr(*cursor, '/');
    size_t length = slash ? (size_t)(slash - *cursor) : strlen(*cursor);
    if (!length || length >= capacity) return -ENAMETOOLONG;
    memcpy(component, *cursor, length);
    component[length] = '\0';
    *cursor = slash ? slash + 1 : *cursor + length;
    return slash ? 1 : 2;
}

static int open_directory(int root, const char *path) {
    int current = duplicate_fd(root);
    const char *cursor = path;
    if (current < 0) return current;
    while (cursor && *cursor) {
        char component[NAME_MAX + 1U];
        int parsed = next_component(&cursor, component, sizeof(component));
        int next;
        if (parsed < 0) { close(current); return parsed; }
        next = openat(current, component,
                      O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (next < 0) { int saved = errno; close(current); return -saved; }
        close(current);
        current = next;
    }
    return current;
}

static int split_parent(const char *path, char *parent, size_t parent_size,
                        char *name, size_t name_size) {
    const char *slash = strrchr(path, '/');
    size_t parent_length = slash ? (size_t)(slash - path) : 0U;
    const char *leaf = slash ? slash + 1 : path;
    if (!*leaf || parent_length >= parent_size || strlen(leaf) >= name_size)
        return -ENAMETOOLONG;
    memcpy(parent, path, parent_length);
    parent[parent_length] = '\0';
    strcpy(name, leaf);
    return 0;
}

static int open_parent(int root, const char *path, char *name,
                       size_t name_size) {
    char parent[PATH_MAX];
    int result = split_parent(path, parent, sizeof(parent), name, name_size);
    return result < 0 ? result : open_directory(root, parent);
}

static int ensure_directory(int root, const char *path, mode_t mode) {
    int current = duplicate_fd(root);
    const char *cursor = path;
    if (current < 0) return current;
    while (cursor && *cursor) {
        char component[NAME_MAX + 1U];
        int parsed = next_component(&cursor, component, sizeof(component));
        int next;
        if (parsed < 0) { close(current); return parsed; }
        if (mkdirat(current, component, mode) < 0 && errno != EEXIST) {
            int saved = errno; close(current); return -saved;
        }
        next = openat(current, component,
                      O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (next < 0) { int saved = errno; close(current); return -saved; }
        close(current);
        current = next;
    }
    return current;
}

static int ensure_parent(int root, const char *path, char *leaf,
                         size_t leaf_size) {
    char parent[PATH_MAX];
    int result = split_parent(path, parent, sizeof(parent), leaf, leaf_size);
    return result < 0 ? result : ensure_directory(root, parent, 0755);
}

static const char *kind(mode_t mode) {
    if (S_ISREG(mode)) return "file";
    if (S_ISDIR(mode)) return "directory";
    if (S_ISLNK(mode)) return "symlink";
    return "other";
}

static int names_add(struct names *names, const char *name) {
    if (names->count == names->capacity) {
        size_t capacity = names->capacity ? names->capacity * 2U : 32U;
        char **items = realloc(names->items, capacity * sizeof(*items));
        if (!items) return -ENOMEM;
        names->items = items;
        names->capacity = capacity;
    }
    names->items[names->count] = strdup(name);
    if (!names->items[names->count]) return -ENOMEM;
    ++names->count;
    return 0;
}

static void names_free(struct names *names) {
    size_t index;
    for (index = 0; index < names->count; ++index) free(names->items[index]);
    free(names->items);
    memset(names, 0, sizeof(*names));
}

static int compare_names(const void *left, const void *right) {
    const char *const *a = left;
    const char *const *b = right;
    return strcasecmp(*a, *b);
}

static int join_path(char *output, size_t capacity, const char *parent,
                     const char *name) {
    int length = parent && *parent
                     ? snprintf(output, capacity, "%s/%s", parent, name)
                     : snprintf(output, capacity, "%s", name);
    return length >= 0 && (size_t)length < capacity ? 0 : -ENAMETOOLONG;
}

static int rename_noreplace(int old_dir, const char *old_name,
                            int new_dir, const char *new_name) {
#ifdef SYS_renameat2
    if (syscall(SYS_renameat2, old_dir, old_name, new_dir, new_name,
                RENAME_NOREPLACE) == 0) return 0;
    if (errno != ENOSYS && errno != EINVAL) return -errno;
#endif
    struct stat existing;
    if (fstatat(new_dir, new_name, &existing, AT_SYMLINK_NOFOLLOW) == 0)
        return -EEXIST;
    if (errno != ENOENT) return -errno;
    return renameat(old_dir, old_name, new_dir, new_name) < 0 ? -errno : 0;
}

static int choose_keep_name(int directory, const char *requested,
                            char *actual, size_t capacity) {
    struct stat existing;
    const char *dot = strrchr(requested, '.');
    size_t stem = dot && dot != requested ? (size_t)(dot - requested)
                                          : strlen(requested);
    const char *extension = dot && dot != requested ? dot : "";
    unsigned index;
    if (fstatat(directory, requested, &existing, AT_SYMLINK_NOFOLLOW) < 0 &&
        errno == ENOENT) {
        return snprintf(actual, capacity, "%s", requested) < (int)capacity
                   ? 0 : -ENAMETOOLONG;
    }
    for (index = 2U; index < 10000U; ++index) {
        int length = snprintf(actual, capacity, "%.*s (%u)%s",
                              (int)stem, requested, index, extension);
        if (length < 0 || (size_t)length >= capacity) return -ENAMETOOLONG;
        if (fstatat(directory, actual, &existing, AT_SYMLINK_NOFOLLOW) < 0 &&
            errno == ENOENT) return 0;
    }
    return -EEXIST;
}

static int move_with_policy(int source_dir, const char *source,
                            int destination_dir, const char *requested,
                            const char *policy, char *actual, size_t capacity,
                            int *skipped) {
    struct stat existing;
    int result;
    *skipped = 0;
    if (strcmp(policy, "fail") == 0) {
        result = rename_noreplace(source_dir, source, destination_dir, requested);
        if (result < 0) return result;
        strcpy(actual, requested);
        return 0;
    }
    if (strcmp(policy, "skip") == 0) {
        if (fstatat(destination_dir, requested, &existing,
                    AT_SYMLINK_NOFOLLOW) == 0) {
            strcpy(actual, requested);
            *skipped = 1;
            return 0;
        }
        if (errno != ENOENT) return -errno;
        if (renameat(source_dir, source, destination_dir, requested) < 0)
            return -errno;
        strcpy(actual, requested);
        return 0;
    }
    if (strcmp(policy, "replace") == 0) {
        if (renameat(source_dir, source, destination_dir, requested) < 0)
            return -errno;
        strcpy(actual, requested);
        return 0;
    }
    if (strcmp(policy, "keep") == 0) {
        result = choose_keep_name(destination_dir, requested, actual, capacity);
        if (result < 0) return result;
        return renameat(source_dir, source, destination_dir, actual) < 0
                   ? -errno : 0;
    }
    return -EINVAL;
}

static int remove_at(int directory, const char *name, int recursive) {
    struct stat info;
    if (fstatat(directory, name, &info, AT_SYMLINK_NOFOLLOW) < 0) return -errno;
    if (!S_ISDIR(info.st_mode) || S_ISLNK(info.st_mode))
        return unlinkat(directory, name, 0) < 0 ? -errno : 0;
    if (!recursive)
        return unlinkat(directory, name, AT_REMOVEDIR) < 0 ? -errno : 0;
    int child = openat(directory, name,
                       O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    DIR *stream;
    struct dirent *entry;
    int result = 0;
    if (child < 0) return -errno;
    stream = fdopendir(child);
    if (!stream) { int saved = errno; close(child); return -saved; }
    errno = 0;
    while ((entry = readdir(stream)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 ||
            strcmp(entry->d_name, "..") == 0) continue;
        result = remove_at(dirfd(stream), entry->d_name, 1);
        if (result < 0) break;
    }
    if (!entry && errno && result == 0) result = -errno;
    if (closedir(stream) < 0 && result == 0) result = -errno;
    if (result < 0) return result;
    return unlinkat(directory, name, AT_REMOVEDIR) < 0 ? -errno : 0;
}

static int identifier(char *output, size_t capacity) {
    struct timespec now;
    unsigned char random[8];
    unsigned long long value = 0;
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd >= 0) {
        if (read(fd, random, sizeof(random)) == (ssize_t)sizeof(random))
            memcpy(&value, random, sizeof(value));
        close(fd);
    }
    if (clock_gettime(CLOCK_REALTIME, &now) < 0) return -errno;
    return snprintf(output, capacity, "%llx-%lx-%llx",
                    (unsigned long long)now.tv_sec, (unsigned long)getpid(),
                    value) < (int)capacity ? 0 : -ENAMETOOLONG;
}

static int valid_id(const char *id) {
    size_t index, length = id ? strlen(id) : 0U;
    if (length < 8U || length > 96U) return 0;
    for (index = 0; index < length; ++index)
        if (!(id[index] >= 'a' && id[index] <= 'z') &&
            !(id[index] >= 'A' && id[index] <= 'Z') &&
            !(id[index] >= '0' && id[index] <= '9') &&
            id[index] != '-' && id[index] != '_') return 0;
    return 1;
}

static int write_text_at(int directory, const char *name, const char *text) {
    int fd = openat(directory, name,
                    O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    size_t length = strlen(text);
    if (fd < 0) return -errno;
    if (write(fd, text, length) != (ssize_t)length || fsync(fd) < 0) {
        int saved = errno ? errno : EIO;
        close(fd); unlinkat(directory, name, 0); return -saved;
    }
    if (close(fd) < 0) return -errno;
    return 0;
}

static int read_text_at(int directory, const char *name, char *output,
                        size_t capacity) {
    int fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    ssize_t count;
    if (fd < 0) return -errno;
    count = read(fd, output, capacity - 1U);
    if (count < 0) { int saved = errno; close(fd); return -saved; }
    if ((size_t)count == capacity - 1U) { close(fd); return -EOVERFLOW; }
    output[count] = '\0';
    close(fd);
    return 0;
}

static int write_metadata(int directory, const char *path,
                          uint64_t size, const char *policy) {
    char *encoded = b64_encode((const unsigned char *)path, strlen(path));
    char content[PATH_MAX * 2];
    int length;
    if (!encoded) return -ENOMEM;
    length = snprintf(content, sizeof(content),
                      "path=%s\nsize=%llu\npolicy=%s\n", encoded,
                      (unsigned long long)size, policy ? policy : "");
    free(encoded);
    if (length < 0 || (size_t)length >= sizeof(content)) return -ENAMETOOLONG;
    return write_text_at(directory, "metadata", content);
}

static int metadata_field(int directory, const char *field,
                          char *output, size_t capacity) {
    char text[PATH_MAX * 2];
    char *line, *save = NULL;
    int result = read_text_at(directory, "metadata", text, sizeof(text));
    if (result < 0) return result;
    for (line = strtok_r(text, "\n", &save); line;
         line = strtok_r(NULL, "\n", &save)) {
        char *equals = strchr(line, '=');
        if (!equals) continue;
        *equals = '\0';
        if (strcmp(line, field) == 0)
            return snprintf(output, capacity, "%s", equals + 1) < (int)capacity
                       ? 0 : -ENAMETOOLONG;
    }
    return -EINVAL;
}

static int metadata_path(int directory, char *output, size_t capacity) {
    char encoded[PATH_MAX * 2];
    struct bytes decoded;
    int result = metadata_field(directory, "path", encoded, sizeof(encoded));
    if (result < 0) return result;
    result = b64_decode(encoded, &decoded);
    if (result < 0) return result;
    if (decoded.length >= capacity) { bytes_free(&decoded); return -ENAMETOOLONG; }
    memcpy(output, decoded.data, decoded.length + 1U);
    bytes_free(&decoded);
    return valid_relative(output, 0, 0);
}

static int open_private_item(struct context *context, const char *root,
                             const char *id, int *root_fd, int *item_fd) {
    if (!valid_id(id)) return -EINVAL;
    *root_fd = open_directory(context->root_fd, root);
    if (*root_fd < 0) return *root_fd;
    *item_fd = openat(*root_fd, id,
                      O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (*item_fd < 0) {
        int saved = errno; close(*root_fd); *root_fd = -1; return -saved;
    }
    return 0;
}

static int decode_path(const char *argument, struct bytes *path,
                       int allow_root) {
    int result = b64_decode(argument, path);
    if (result < 0) return result;
    result = valid_relative((const char *)path->data, allow_root, 0);
    if (result < 0) bytes_free(path);
    return result;
}

static int command_protocol(void) {
    printf("{\"ok\":true,\"protocol\":%d,\"version\":\"%s\","
           "\"path_encoding\":\"base64url\",\"list_format\":\"jsonl\"}\n",
           PROTOCOL_VERSION, HELPER_VERSION);
    return 0;
}

static int command_health(void) {
    struct context context;
    struct statvfs info;
    int result = context_open(&context, 0);
    if (result == -ENODEV)
        return fail("SD_NOT_MOUNTED",
                    "No microSD card is mounted at the configured path.", ENODEV);
    if (result < 0) return fail_errno("The microSD card could not be opened.", -result);
    if (statvfs(context.root_path, &info) < 0) {
        result = errno; context_close(&context);
        return fail_errno("Storage information could not be read.", result);
    }
    fputs("{\"ok\":true,\"sd_root\":", stdout);
    json_string(context.root_path);
    printf(",\"total_bytes\":%llu,\"available_bytes\":%llu,\"writable\":%s}\n",
           (unsigned long long)info.f_blocks * info.f_frsize,
           (unsigned long long)info.f_bavail * info.f_frsize,
           access(context.root_path, W_OK) == 0 ? "true" : "false");
    context_close(&context);
    return 0;
}

static int command_list(const char *argument) {
    struct bytes path;
    struct context context;
    struct names names = {0};
    int directory = -1;
    DIR *stream = NULL;
    struct dirent *entry;
    size_t index;
    int result = decode_path(argument, &path, 1);
    if (result < 0) return fail("INVALID_PATH", "The requested path is invalid.", EINVAL);
    result = context_open(&context, 0);
    if (result < 0) goto error;
    directory = open_directory(context.root_fd, (const char *)path.data);
    if (directory < 0) { result = directory; goto error; }
    stream = fdopendir(directory);
    if (!stream) { result = -errno; close(directory); directory = -1; goto error; }
    directory = -1;
    errno = 0;
    while ((entry = readdir(stream)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0)
            continue;
        if (!path.length && strcmp(entry->d_name, PRIVATE_ROOT) == 0) continue;
        result = names_add(&names, entry->d_name);
        if (result < 0) break;
    }
    if (!entry && errno && result == 0) result = -errno;
    if (closedir(stream) < 0 && result == 0) result = -errno;
    stream = NULL;
    if (result < 0) goto error;
    qsort(names.items, names.count, sizeof(*names.items), compare_names);
    fputs("{\"ok\":true,\"type\":\"list\",\"path\":", stdout);
    json_bytes(path.data, path.length);
    fputs("}\n", stdout);
    for (index = 0; index < names.count; ++index) {
        int fd = open_directory(context.root_fd, (const char *)path.data);
        struct stat info;
        char relative[PATH_MAX];
        char *encoded;
        if (fd < 0) { result = fd; break; }
        if (fstatat(fd, names.items[index], &info, AT_SYMLINK_NOFOLLOW) < 0) {
            result = -errno; close(fd); break;
        }
        close(fd);
        if (join_path(relative, sizeof(relative), (const char *)path.data,
                      names.items[index]) < 0) { result = -ENAMETOOLONG; break; }
        encoded = b64_encode((const unsigned char *)relative, strlen(relative));
        if (!encoded) { result = -ENOMEM; break; }
        fputs("{\"entry\":true,\"name\":", stdout);
        json_string(names.items[index]);
        fputs(",\"path\":", stdout); json_string(relative);
        fputs(",\"path_b64\":", stdout); json_string(encoded);
        fputs(",\"kind\":", stdout); json_string(kind(info.st_mode));
        printf(",\"size\":%llu,\"mtime\":%lld,\"hidden\":%s}\n",
               (unsigned long long)(S_ISREG(info.st_mode) ? info.st_size : 0),
               (long long)info.st_mtime,
               names.items[index][0] == '.' ? "true" : "false");
        free(encoded);
    }
    if (result >= 0)
        printf("{\"done\":true,\"count\":%llu}\n",
               (unsigned long long)names.count);
error:
    if (stream) closedir(stream);
    if (directory >= 0) close(directory);
    if (context.root_fd >= 0) context_close(&context);
    names_free(&names);
    bytes_free(&path);
    if (result < 0) {
        if (result == -ENODEV)
            return fail("SD_NOT_MOUNTED", "No microSD card is mounted.", ENODEV);
        return fail_errno("The directory could not be listed.", -result);
    }
    return 0;
}

static int command_stat(const char *argument) {
    struct bytes path;
    struct context context;
    struct stat info;
    char name[NAME_MAX + 1U];
    int parent = -1;
    int result = decode_path(argument, &path, 0);
    if (result < 0) return fail("INVALID_PATH", "The requested path is invalid.", EINVAL);
    result = context_open(&context, 0);
    if (result < 0) goto error;
    parent = open_parent(context.root_fd, (const char *)path.data, name, sizeof(name));
    if (parent < 0) { result = parent; goto error; }
    if (fstatat(parent, name, &info, AT_SYMLINK_NOFOLLOW) < 0) {
        result = -errno; goto error;
    }
    fputs("{\"ok\":true,\"path\":", stdout); json_bytes(path.data, path.length);
    fputs(",\"kind\":", stdout); json_string(kind(info.st_mode));
    printf(",\"size\":%llu,\"mtime\":%lld}\n",
           (unsigned long long)(S_ISREG(info.st_mode) ? info.st_size : 0),
           (long long)info.st_mtime);
error:
    if (parent >= 0) close(parent);
    if (context.root_fd >= 0) context_close(&context);
    bytes_free(&path);
    return result < 0 ? fail_errno("The item information could not be read.", -result) : 0;
}

static int command_mkdir(const char *parent_arg, const char *name_arg) {
    struct bytes parent_path, name;
    struct context context;
    int parent = -1;
    int result = decode_path(parent_arg, &parent_path, 1);
    char created[PATH_MAX];
    if (result < 0) return fail("INVALID_PATH", "The parent path is invalid.", EINVAL);
    result = b64_decode(name_arg, &name);
    if (result < 0 || valid_new_name(name.data, name.length) < 0) {
        bytes_free(&parent_path); bytes_free(&name);
        return fail("INVALID_NAME", "The folder name contains unsupported characters.", EINVAL);
    }
    result = context_open(&context, 1);
    if (result < 0) goto error;
    parent = open_directory(context.root_fd, (const char *)parent_path.data);
    if (parent < 0) { result = parent; goto error; }
    if (mkdirat(parent, (const char *)name.data, 0755) < 0) { result = -errno; goto error; }
    if (join_path(created, sizeof(created), (const char *)parent_path.data,
                  (const char *)name.data) < 0) { result = -ENAMETOOLONG; goto error; }
    fputs("{\"ok\":true,\"path\":", stdout); json_string(created); fputs("}\n", stdout);
error:
    if (parent >= 0) close(parent);
    if (context.root_fd >= 0) context_close(&context);
    bytes_free(&parent_path); bytes_free(&name);
    return result < 0 ? fail_errno("The folder could not be created.", -result) : 0;
}

static int command_rename(const char *path_arg, const char *name_arg) {
    struct bytes path, name;
    struct context context;
    char source[NAME_MAX + 1U], parent_path[PATH_MAX], final[PATH_MAX];
    int parent = -1;
    int result = decode_path(path_arg, &path, 0);
    if (result < 0) return fail("INVALID_PATH", "The requested path is invalid.", EINVAL);
    result = b64_decode(name_arg, &name);
    if (result < 0 || valid_new_name(name.data, name.length) < 0) {
        bytes_free(&path); bytes_free(&name);
        return fail("INVALID_NAME", "The new name contains unsupported characters.", EINVAL);
    }
    result = split_parent((const char *)path.data, parent_path, sizeof(parent_path),
                          source, sizeof(source));
    if (result < 0) goto error_no_context;
    result = context_open(&context, 1);
    if (result < 0) goto error;
    parent = open_directory(context.root_fd, parent_path);
    if (parent < 0) { result = parent; goto error; }
    result = rename_noreplace(parent, source, parent, (const char *)name.data);
    if (result < 0) goto error;
    result = join_path(final, sizeof(final), parent_path, (const char *)name.data);
    if (result < 0) goto error;
    fputs("{\"ok\":true,\"path\":", stdout); json_string(final); fputs("}\n", stdout);
error:
    if (parent >= 0) close(parent);
    if (context.root_fd >= 0) context_close(&context);
error_no_context:
    bytes_free(&path); bytes_free(&name);
    return result < 0 ? fail_errno("The item could not be renamed.", -result) : 0;
}

static int command_move(const char *source_arg, const char *destination_arg,
                        const char *policy) {
    struct bytes source_path, destination_path;
    struct context context;
    char source[NAME_MAX + 1U], actual[NAME_MAX + 32U], final[PATH_MAX];
    int source_parent = -1, destination = -1, skipped = 0;
    int result;
    if (strcmp(policy, "fail") && strcmp(policy, "skip") &&
        strcmp(policy, "replace") && strcmp(policy, "keep"))
        return fail("INVALID_POLICY", "The conflict policy is not supported.", EINVAL);
    result = decode_path(source_arg, &source_path, 0);
    if (result < 0) return fail("INVALID_PATH", "The source path is invalid.", EINVAL);
    result = decode_path(destination_arg, &destination_path, 1);
    if (result < 0) { bytes_free(&source_path); return fail("INVALID_PATH", "The destination path is invalid.", EINVAL); }
    result = context_open(&context, 1);
    if (result < 0) goto error;
    source_parent = open_parent(context.root_fd, (const char *)source_path.data,
                                source, sizeof(source));
    if (source_parent < 0) { result = source_parent; goto error; }
    destination = open_directory(context.root_fd, (const char *)destination_path.data);
    if (destination < 0) { result = destination; goto error; }
    result = move_with_policy(source_parent, source, destination, source, policy,
                              actual, sizeof(actual), &skipped);
    if (result < 0) goto error;
    result = join_path(final, sizeof(final), (const char *)destination_path.data, actual);
    if (result < 0) goto error;
    fputs("{\"ok\":true,\"path\":", stdout); json_string(final);
    printf(",\"skipped\":%s}\n", skipped ? "true" : "false");
error:
    if (destination >= 0) close(destination);
    if (source_parent >= 0) close(source_parent);
    if (context.root_fd >= 0) context_close(&context);
    bytes_free(&source_path); bytes_free(&destination_path);
    return result < 0 ? fail_errno("The item could not be moved.", -result) : 0;
}

static int command_delete(const char *path_arg, const char *mode) {
    struct bytes path;
    struct context context;
    char name[NAME_MAX + 1U];
    int parent = -1;
    int recursive;
    int result;
    if (strcmp(mode, "empty") == 0) recursive = 0;
    else if (strcmp(mode, "recursive") == 0) recursive = 1;
    else return fail("INVALID_DELETE_MODE", "The delete mode is not supported.", EINVAL);
    result = decode_path(path_arg, &path, 0);
    if (result < 0) return fail("INVALID_PATH", "The requested path is invalid.", EINVAL);
    result = context_open(&context, 1);
    if (result < 0) goto error;
    parent = open_parent(context.root_fd, (const char *)path.data, name, sizeof(name));
    if (parent < 0) { result = parent; goto error; }
    result = remove_at(parent, name, recursive);
    if (result == 0) fputs("{\"ok\":true}\n", stdout);
error:
    if (parent >= 0) close(parent);
    if (context.root_fd >= 0) context_close(&context);
    bytes_free(&path);
    return result < 0 ? fail_errno("The item could not be deleted.", -result) : 0;
}

static int command_trash(const char *path_arg) {
    struct bytes path;
    struct context context;
    char source[NAME_MAX + 1U], id[128];
    int source_parent = -1, trash = -1, item = -1;
    int result = decode_path(path_arg, &path, 0);
    if (result < 0) return fail("INVALID_PATH", "The requested path is invalid.", EINVAL);
    result = context_open(&context, 1);
    if (result < 0) goto error;
    trash = ensure_directory(context.root_fd, TRASH_ROOT, 0700);
    if (trash < 0) { result = trash; goto error; }
    result = identifier(id, sizeof(id));
    if (result < 0) goto error;
    if (mkdirat(trash, id, 0700) < 0) { result = -errno; goto error; }
    item = openat(trash, id, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (item < 0) { result = -errno; goto rollback; }
    result = write_metadata(item, (const char *)path.data, 0, NULL);
    if (result < 0) goto rollback;
    source_parent = open_parent(context.root_fd, (const char *)path.data,
                                source, sizeof(source));
    if (source_parent < 0) { result = source_parent; goto rollback; }
    result = rename_noreplace(source_parent, source, item, "item");
    if (result < 0) goto rollback;
    fputs("{\"ok\":true,\"trash_id\":", stdout); json_string(id);
    fputs(",\"original_path\":", stdout); json_bytes(path.data, path.length);
    fputs("}\n", stdout);
    goto done;
rollback:
    if (item >= 0) { unlinkat(item, "metadata", 0); close(item); item = -1; }
    if (trash >= 0) unlinkat(trash, id, AT_REMOVEDIR);
error:
    if (result < 0) result = fail_errno("The item could not be moved to Trash.", -result);
done:
    if (source_parent >= 0) close(source_parent);
    if (item >= 0) close(item);
    if (trash >= 0) close(trash);
    if (context.root_fd >= 0) context_close(&context);
    bytes_free(&path);
    return result < 0 ? 1 : result;
}

static int command_trash_list(void) {
    struct context context;
    struct names ids = {0};
    int trash = -1;
    DIR *stream = NULL;
    struct dirent *entry;
    size_t index;
    int result = context_open(&context, 0);
    if (result < 0) goto error;
    trash = open_directory(context.root_fd, TRASH_ROOT);
    if (trash == -ENOENT) {
        fputs("{\"ok\":true,\"type\":\"trash-list\"}\n"
              "{\"done\":true,\"count\":0}\n", stdout);
        context_close(&context); return 0;
    }
    if (trash < 0) { result = trash; goto error; }
    stream = fdopendir(duplicate_fd(trash));
    if (!stream) { result = -errno; goto error; }
    while ((entry = readdir(stream)) != NULL)
        if (entry->d_name[0] != '.' && valid_id(entry->d_name)) {
            result = names_add(&ids, entry->d_name);
            if (result < 0) break;
        }
    closedir(stream); stream = NULL;
    if (result < 0) goto error;
    qsort(ids.items, ids.count, sizeof(*ids.items), compare_names);
    fputs("{\"ok\":true,\"type\":\"trash-list\"}\n", stdout);
    for (index = 0; index < ids.count; ++index) {
        int item = openat(trash, ids.items[index],
                          O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        char origin[PATH_MAX];
        struct stat info;
        if (item < 0) continue;
        if (metadata_path(item, origin, sizeof(origin)) == 0 &&
            fstatat(item, "item", &info, AT_SYMLINK_NOFOLLOW) == 0) {
            fputs("{\"entry\":true,\"trash_id\":", stdout); json_string(ids.items[index]);
            fputs(",\"original_path\":", stdout); json_string(origin);
            fputs(",\"kind\":", stdout); json_string(kind(info.st_mode));
            printf(",\"size\":%llu,\"mtime\":%lld}\n",
                   (unsigned long long)(S_ISREG(info.st_mode) ? info.st_size : 0),
                   (long long)info.st_mtime);
        }
        close(item);
    }
    printf("{\"done\":true,\"count\":%llu}\n", (unsigned long long)ids.count);
error:
    if (stream) closedir(stream);
    if (trash >= 0) close(trash);
    if (context.root_fd >= 0) context_close(&context);
    names_free(&ids);
    return result < 0 ? fail_errno("Trash could not be listed.", -result) : 0;
}

static int command_restore(const char *id) {
    struct context context;
    int trash = -1, item = -1, destination = -1;
    char origin[PATH_MAX], leaf[NAME_MAX + 1U];
    int result;
    if (!valid_id(id)) return fail("INVALID_TRASH_ID", "The Trash item identifier is invalid.", EINVAL);
    result = context_open(&context, 1);
    if (result < 0) goto error;
    result = open_private_item(&context, TRASH_ROOT, id, &trash, &item);
    if (result < 0) goto error;
    result = metadata_path(item, origin, sizeof(origin));
    if (result < 0) goto error;
    destination = ensure_parent(context.root_fd, origin, leaf, sizeof(leaf));
    if (destination < 0) { result = destination; goto error; }
    result = rename_noreplace(item, "item", destination, leaf);
    if (result < 0) goto error;
    unlinkat(item, "metadata", 0);
    close(item); item = -1;
    if (unlinkat(trash, id, AT_REMOVEDIR) < 0) { result = -errno; goto error; }
    fputs("{\"ok\":true,\"path\":", stdout); json_string(origin); fputs("}\n", stdout);
error:
    if (destination >= 0) close(destination);
    if (item >= 0) close(item);
    if (trash >= 0) close(trash);
    if (context.root_fd >= 0) context_close(&context);
    return result < 0 ? fail_errno("The Trash item could not be restored.", -result) : 0;
}

static int command_purge(const char *id) {
    struct context context;
    int trash = -1, item = -1;
    int result;
    if (!valid_id(id)) return fail("INVALID_TRASH_ID", "The Trash item identifier is invalid.", EINVAL);
    result = context_open(&context, 1);
    if (result < 0) goto error;
    result = open_private_item(&context, TRASH_ROOT, id, &trash, &item);
    if (result < 0) goto error;
    close(item); item = -1;
    result = remove_at(trash, id, 1);
    if (result == 0) fputs("{\"ok\":true}\n", stdout);
error:
    if (item >= 0) close(item);
    if (trash >= 0) close(trash);
    if (context.root_fd >= 0) context_close(&context);
    return result < 0 ? fail_errno("The Trash item could not be deleted permanently.", -result) : 0;
}

static int command_empty_trash(void) {
    struct context context;
    int trash = -1;
    DIR *stream = NULL;
    struct dirent *entry;
    unsigned deleted = 0;
    int result = context_open(&context, 1);
    if (result < 0) goto error;
    trash = open_directory(context.root_fd, TRASH_ROOT);
    if (trash == -ENOENT) { fputs("{\"ok\":true,\"deleted\":0}\n", stdout); context_close(&context); return 0; }
    if (trash < 0) { result = trash; goto error; }
    stream = fdopendir(duplicate_fd(trash));
    if (!stream) { result = -errno; goto error; }
    while ((entry = readdir(stream)) != NULL) {
        if (entry->d_name[0] == '.' || !valid_id(entry->d_name)) continue;
        result = remove_at(trash, entry->d_name, 1);
        if (result < 0) break;
        ++deleted;
    }
    closedir(stream); stream = NULL;
    if (result == 0) printf("{\"ok\":true,\"deleted\":%u}\n", deleted);
error:
    if (stream) closedir(stream);
    if (trash >= 0) close(trash);
    if (context.root_fd >= 0) context_close(&context);
    return result < 0 ? fail_errno("Trash could not be emptied.", -result) : 0;
}

static int parse_size(const char *text, uint64_t *value) {
    char *end = NULL;
    unsigned long long parsed;
    errno = 0;
    parsed = strtoull(text, &end, 10);
    if (errno || !end || *end) return -EINVAL;
    *value = parsed;
    return 0;
}

static int command_upload_prepare(const char *destination_arg,
                                  const char *size_arg, const char *policy) {
    struct bytes destination;
    struct context context;
    struct statvfs storage;
    uint64_t size, available;
    char id[128], relative[PATH_MAX], absolute[PATH_MAX];
    int incoming = -1, item = -1, payload = -1;
    int result;
    if (strcmp(policy, "fail") && strcmp(policy, "skip") &&
        strcmp(policy, "replace") && strcmp(policy, "keep"))
        return fail("INVALID_POLICY", "The conflict policy is not supported.", EINVAL);
    result = decode_path(destination_arg, &destination, 0);
    if (result < 0 || parse_size(size_arg, &size) < 0) {
        bytes_free(&destination);
        return fail("INVALID_UPLOAD", "The upload request is invalid.", EINVAL);
    }
    result = context_open(&context, 1);
    if (result < 0) goto error;
    if (statvfs(context.root_path, &storage) < 0) { result = -errno; goto error; }
    available = (uint64_t)storage.f_bavail * storage.f_frsize;
    if (size > available || available - size < SPACE_RESERVE) { result = -ENOSPC; goto error; }
    incoming = ensure_directory(context.root_fd, INCOMING_ROOT, 0700);
    if (incoming < 0) { result = incoming; goto error; }
    result = identifier(id, sizeof(id));
    if (result < 0) goto error;
    if (mkdirat(incoming, id, 0700) < 0) { result = -errno; goto error; }
    item = openat(incoming, id, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (item < 0) { result = -errno; goto rollback; }
    result = write_metadata(item, (const char *)destination.data, size, policy);
    if (result < 0) goto rollback;
    payload = openat(item, "payload.part", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (payload < 0) { result = -errno; goto rollback; }
    close(payload); payload = -1;
    if (snprintf(relative, sizeof(relative), "%s/%s/payload.part", INCOMING_ROOT, id) >= (int)sizeof(relative) ||
        snprintf(absolute, sizeof(absolute), "%s/%s", context.root_path, relative) >= (int)sizeof(absolute)) {
        result = -ENAMETOOLONG; goto rollback;
    }
    fputs("{\"ok\":true,\"upload_id\":", stdout); json_string(id);
    fputs(",\"staging_path\":", stdout); json_string(relative);
    fputs(",\"staging_absolute\":", stdout); json_string(absolute);
    printf(",\"expected_size\":%llu}\n", (unsigned long long)size);
    goto done;
rollback:
    if (payload >= 0) close(payload);
    if (item >= 0) { unlinkat(item, "payload.part", 0); unlinkat(item, "metadata", 0); close(item); item = -1; }
    if (incoming >= 0) unlinkat(incoming, id, AT_REMOVEDIR);
error:
    if (result < 0) result = fail_errno("The upload could not be prepared.", -result);
done:
    if (item >= 0) close(item);
    if (incoming >= 0) close(incoming);
    if (context.root_fd >= 0) context_close(&context);
    bytes_free(&destination);
    return result < 0 ? 1 : result;
}

static int upload_metadata(int item, char *destination, size_t destination_size,
                           uint64_t *size, char *policy, size_t policy_size) {
    char size_text[64];
    int result = metadata_path(item, destination, destination_size);
    if (result < 0) return result;
    result = metadata_field(item, "size", size_text, sizeof(size_text));
    if (result < 0 || parse_size(size_text, size) < 0) return -EINVAL;
    return metadata_field(item, "policy", policy, policy_size);
}

static int command_upload_status(const char *id) {
    struct context context;
    int incoming = -1, item = -1;
    char destination[PATH_MAX], policy[16];
    uint64_t expected;
    struct stat info;
    int result;
    if (!valid_id(id)) return fail("INVALID_UPLOAD_ID", "The upload identifier is invalid.", EINVAL);
    result = context_open(&context, 0);
    if (result < 0) goto error;
    result = open_private_item(&context, INCOMING_ROOT, id, &incoming, &item);
    if (result < 0) goto error;
    result = upload_metadata(item, destination, sizeof(destination), &expected, policy, sizeof(policy));
    if (result < 0) goto error;
    if (fstatat(item, "payload.part", &info, AT_SYMLINK_NOFOLLOW) < 0) { result = -errno; goto error; }
    fputs("{\"ok\":true,\"upload_id\":", stdout); json_string(id);
    fputs(",\"destination\":", stdout); json_string(destination);
    printf(",\"expected_size\":%llu,\"received_size\":%llu,\"policy\":",
           (unsigned long long)expected, (unsigned long long)info.st_size);
    json_string(policy); fputs("}\n", stdout);
error:
    if (item >= 0) close(item);
    if (incoming >= 0) close(incoming);
    if (context.root_fd >= 0) context_close(&context);
    return result < 0 ? fail_errno("The upload status could not be read.", -result) : 0;
}

static int command_upload_commit(const char *id) {
    struct context context;
    int incoming = -1, item = -1, destination_dir = -1, payload = -1;
    char destination[PATH_MAX], parent_path[PATH_MAX], leaf[NAME_MAX + 1U];
    char policy[16], actual[NAME_MAX + 32U], final[PATH_MAX];
    uint64_t expected;
    struct stat info;
    int skipped = 0;
    int result;
    if (!valid_id(id)) return fail("INVALID_UPLOAD_ID", "The upload identifier is invalid.", EINVAL);
    result = context_open(&context, 1);
    if (result < 0) goto error;
    result = open_private_item(&context, INCOMING_ROOT, id, &incoming, &item);
    if (result < 0) goto error;
    result = upload_metadata(item, destination, sizeof(destination), &expected, policy, sizeof(policy));
    if (result < 0) goto error;
    if (fstatat(item, "payload.part", &info, AT_SYMLINK_NOFOLLOW) < 0) { result = -errno; goto error; }
    if ((uint64_t)info.st_size != expected) {
        const char *code = (uint64_t)info.st_size < expected ? "UPLOAD_INCOMPLETE" : "UPLOAD_SIZE_MISMATCH";
        const char *message = (uint64_t)info.st_size < expected
                                  ? "The uploaded file is incomplete and was not committed."
                                  : "The uploaded file size does not match the prepared transfer.";
        close(item); close(incoming); context_close(&context);
        return fail(code, message, 0);
    }
    payload = openat(item, "payload.part", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (payload < 0 || fsync(payload) < 0) { result = -errno; goto error; }
    close(payload); payload = -1;
    result = split_parent(destination, parent_path, sizeof(parent_path), leaf, sizeof(leaf));
    if (result < 0) goto error;
    destination_dir = ensure_parent(context.root_fd, destination, leaf, sizeof(leaf));
    if (destination_dir < 0) { result = destination_dir; goto error; }
    result = move_with_policy(item, "payload.part", destination_dir, leaf, policy,
                              actual, sizeof(actual), &skipped);
    if (result < 0) goto error;
    if (skipped && unlinkat(item, "payload.part", 0) < 0) { result = -errno; goto error; }
    unlinkat(item, "metadata", 0);
    result = join_path(final, sizeof(final), parent_path, actual);
    if (result < 0) goto error;
    close(item); item = -1;
    if (unlinkat(incoming, id, AT_REMOVEDIR) < 0) { result = -errno; goto error; }
    fputs("{\"ok\":true,\"path\":", stdout); json_string(final);
    printf(",\"skipped\":%s}\n", skipped ? "true" : "false");
error:
    if (payload >= 0) close(payload);
    if (destination_dir >= 0) close(destination_dir);
    if (item >= 0) close(item);
    if (incoming >= 0) close(incoming);
    if (context.root_fd >= 0) context_close(&context);
    return result < 0 ? fail_errno("The upload could not be committed.", -result) : 0;
}

static int command_upload_cancel(const char *id) {
    struct context context;
    int incoming = -1, item = -1;
    int result;
    if (!valid_id(id)) return fail("INVALID_UPLOAD_ID", "The upload identifier is invalid.", EINVAL);
    result = context_open(&context, 1);
    if (result < 0) goto error;
    result = open_private_item(&context, INCOMING_ROOT, id, &incoming, &item);
    if (result < 0) goto error;
    close(item); item = -1;
    result = remove_at(incoming, id, 1);
    if (result == 0) fputs("{\"ok\":true}\n", stdout);
error:
    if (item >= 0) close(item);
    if (incoming >= 0) close(incoming);
    if (context.root_fd >= 0) context_close(&context);
    return result < 0 ? fail_errno("The upload could not be cancelled.", -result) : 0;
}

static void usage(const char *program) {
    fprintf(stderr,
            "Usage: %s protocol|health|list|stat|mkdir|rename|move|delete|"
            "trash|trash-list|restore|purge|empty-trash|upload-prepare|"
            "upload-status|upload-commit|upload-cancel ...\n", program);
}

int main(int argc, char **argv) {
    if (argc < 2) { usage(argv[0]); return 2; }
    if (!strcmp(argv[1], "protocol") && argc == 2) return command_protocol();
    if (!strcmp(argv[1], "health") && argc == 2) return command_health();
    if (!strcmp(argv[1], "list") && argc == 3) return command_list(argv[2]);
    if (!strcmp(argv[1], "stat") && argc == 3) return command_stat(argv[2]);
    if (!strcmp(argv[1], "mkdir") && argc == 4) return command_mkdir(argv[2], argv[3]);
    if (!strcmp(argv[1], "rename") && argc == 4) return command_rename(argv[2], argv[3]);
    if (!strcmp(argv[1], "move") && argc == 5) return command_move(argv[2], argv[3], argv[4]);
    if (!strcmp(argv[1], "delete") && argc == 4) return command_delete(argv[2], argv[3]);
    if (!strcmp(argv[1], "trash") && argc == 3) return command_trash(argv[2]);
    if (!strcmp(argv[1], "trash-list") && argc == 2) return command_trash_list();
    if (!strcmp(argv[1], "restore") && argc == 3) return command_restore(argv[2]);
    if (!strcmp(argv[1], "purge") && argc == 3) return command_purge(argv[2]);
    if (!strcmp(argv[1], "empty-trash") && argc == 2) return command_empty_trash();
    if (!strcmp(argv[1], "upload-prepare") && argc == 5)
        return command_upload_prepare(argv[2], argv[3], argv[4]);
    if (!strcmp(argv[1], "upload-status") && argc == 3) return command_upload_status(argv[2]);
    if (!strcmp(argv[1], "upload-commit") && argc == 3) return command_upload_commit(argv[2]);
    if (!strcmp(argv[1], "upload-cancel") && argc == 3) return command_upload_cancel(argv[2]);
    usage(argv[0]);
    return 2;
}
