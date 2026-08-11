#!/bin/sh

# Register the repository's static qemu-mipsel interpreter only inside the
# caller's disposable user/mount namespace, then run the requested sandbox.
# This lets an emulated BusyBox shell exec other exact-candidate MIPS applets
# without requiring a host-wide binfmt_misc registration.

set -eu

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 QEMU-MIPSEL COMMAND [ARGUMENT ...]" >&2
    exit 2
fi

qemu=$1
shift
case "$qemu" in
    /*) ;;
    *)
        echo "qemu-mipsel path must be absolute: $qemu" >&2
        exit 2
        ;;
esac
if [ ! -x "$qemu" ]; then
    echo "qemu-mipsel is missing or not executable: $qemu" >&2
    exit 2
fi

binfmt_dir=$(mktemp -d /tmp/r1-binfmt.XXXXXX)
mount -t binfmt_misc binfmt_misc "$binfmt_dir"

# ELF32, little-endian, EM_MIPS.  The masked e_type bit admits both ET_EXEC
# and ET_DYN, which is required for the candidate's PIE Dropbear binary.
# O passes an already-open target fd, C preserves target credential handling,
# and F pins the static interpreter before bubblewrap changes the root.
printf ':r1-mipsel:M::\177ELF\001\001\001\000\000\000\000\000\000\000\000\000\002\000\010\000:\377\377\377\377\377\377\377\000\377\377\377\377\377\377\377\377\376\377\377\377:%s:OCF' \
    "$qemu" > "$binfmt_dir/register"

exec "$@"
