#define _GNU_SOURCE

/* Dropbear inetd mode expects stdin/stdout to be an IPv4 or IPv6 socket.
 * Managed test sandboxes may prohibit AF_INET creation, so the validator uses
 * a private AF_UNIX socket and this target-side preload reports deterministic
 * loopback endpoints to Dropbear.  It changes address metadata only; all SSH
 * protocol bytes still pass through the inherited socket unchanged. */

#include <arpa/inet.h>
#include <errno.h>
#include <grp.h>
#include <netinet/in.h>
#include <stddef.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>

static int translated_socket_name(int descriptor, struct sockaddr *address,
                                  socklen_t *length, int peer) {
    struct sockaddr_storage actual;
    struct sockaddr_in loopback;
    socklen_t actual_length = sizeof(actual);
    long result;

    if (!address || !length) {
        errno = EFAULT;
        return -1;
    }
    result = syscall(peer ? SYS_getpeername : SYS_getsockname, descriptor,
                     &actual, &actual_length);
    if (result == 0 && actual.ss_family != AF_UNIX) {
        socklen_t copied = *length < actual_length ? *length : actual_length;
        memcpy(address, &actual, copied);
        *length = actual_length;
        return 0;
    }
    /* Some syscall sandboxes reject socket-address inspection even for an
     * already inherited AF_UNIX stream. This preload is loaded only by the
     * validator's inetd process, whose descriptors 0 and 1 are that private
     * socket pair. Fail closed for every other descriptor/error. */
    if (result < 0 && descriptor != STDIN_FILENO && descriptor != STDOUT_FILENO)
        return -1;

    memset(&loopback, 0, sizeof(loopback));
    loopback.sin_family = AF_INET;
    loopback.sin_port = htons(peer ? 40000U : 2222U);
    loopback.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    {
        socklen_t copied = *length < sizeof(loopback) ? *length : sizeof(loopback);
        memcpy(address, &loopback, copied);
    }
    *length = sizeof(loopback);
    return 0;
}

int getpeername(int descriptor, struct sockaddr *address, socklen_t *length) {
    return translated_socket_name(descriptor, address, length, 1);
}

int getsockname(int descriptor, struct sockaddr *address, socklen_t *length) {
    return translated_socket_name(descriptor, address, length, 0);
}

int setgroups(size_t count, const gid_t *groups) {
    /* unshare(1) deliberately disables setgroups after installing the
     * single-ID user-namespace map. Root already is gid 0 in that namespace,
     * so Dropbear's redundant one-element root group transition is safe to
     * acknowledge. Keep every other call on the real syscall path. */
    if (count == 1U && groups && groups[0] == 0) return 0;
    return (int)syscall(SYS_setgroups, count, groups);
}

int initgroups(const char *user, gid_t group) {
    /* glibc may issue setgroups through a hidden internal symbol, bypassing
     * the public interposer above. The validation login is deliberately only
     * root, whose sole mapped group is already active. */
    if (user && strcmp(user, "root") == 0 && group == 0) return 0;
    errno = EPERM;
    return -1;
}
