#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=2026.94
source_dir=${DROPBEAR_SOURCE_DIR:-$repo_dir/work/ssh-build/src/dropbear-$version}
zig_dir=${ZIG_DIR:-$repo_dir/work/host-tools/zig-x86_64-linux-0.16.0}
build_dir=${DROPBEAR_BUILD_DIR:-$repo_dir/work/ssh-build/build-dropbear-$version}
output_dir=${DROPBEAR_OUTPUT_DIR:-$repo_dir/work/ssh-build/output}
link_dir=$repo_dir/work/ssh-build/r1-link
binutils_dir=${MIPSEL_BINUTILS_DIR:-$repo_dir/work/host-tools/binutils-mipsel}
zig=$zig_dir/zig
strip=$binutils_dir/usr/bin/mipsel-linux-gnu-strip
binutils_lib=$binutils_dir/usr/lib/x86_64-linux-gnu

if [ ! -x "$zig" ]; then
    echo "missing Zig compiler: $zig" >&2
    exit 1
fi
if [ ! -x "$source_dir/configure" ]; then
    echo "missing Dropbear source: $source_dir" >&2
    exit 1
fi
if [ ! -x "$strip" ]; then
    echo "missing MIPS strip utility: $strip" >&2
    echo "run tools/bootstrap-ssh-toolchain.sh first" >&2
    exit 1
fi

mkdir -p "$build_dir" "$output_dir" "$link_dir" \
    "$repo_dir/work/zig-cache/global" "$repo_dir/work/zig-cache/local"

# Zig provides the pinned glibc 2.22 sysroot, while password authentication
# needs libcrypt from the actual R1 rootfs. The build-only linker name is not
# shipped on the player, so create it alongside the untouched vendor library.
ln -sf "$repo_dir/work/r1-1.6/rootfs-full/lib/libcrypt-2.22.so" \
    "$link_dir/libcrypt.so"

cp "$repo_dir/mods/ssh-dropbear/dropbear-localoptions.h" \
    "$build_dir/localoptions.h"

zig_cc="$zig cc -target mipsel-linux-gnueabihf.2.22"
export ZIG_GLOBAL_CACHE_DIR="$repo_dir/work/zig-cache/global"
export ZIG_LOCAL_CACHE_DIR="$repo_dir/work/zig-cache/local"

(
    cd "$build_dir"
    CC="$zig_cc" \
    AR="$zig ar" \
    RANLIB="$zig ranlib" \
    CFLAGS="-march=mips32r2 -mabi=32 -Os -ffunction-sections -fdata-sections" \
    LDFLAGS="-L$link_dir -Wl,--gc-sections -Wl,--allow-shlib-undefined" \
    "$source_dir/configure" \
        --host=mipsel-linux-gnu \
        --disable-zlib \
        --disable-lastlog \
        --disable-utmp \
        --disable-utmpx \
        --disable-wtmp \
        --disable-wtmpx \
        --disable-loginfunc \
        --disable-pututline \
        --disable-pututxline \
        --disable-syslog \
        --enable-bundled-libtom
)

(
    cd "$build_dir"
    make clean
    make -j4 PROGRAMS="dropbear dropbearkey scp" MULTI=1
)

# Zig 0.16 can link this MIPS ELF but its objcopy backend does not implement
# stripping it. Use the matching GNU cross-binutils package for that final step.
LD_LIBRARY_PATH="$binutils_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$strip" --strip-all -o "$output_dir/dropbearmulti" \
    "$build_dir/dropbearmulti"
chmod 0755 "$output_dir/dropbearmulti"

echo "built $output_dir/dropbearmulti"
file "$output_dir/dropbearmulti"
readelf -h "$output_dir/dropbearmulti" | sed -n '1,30p'
readelf -d "$output_dir/dropbearmulti" | grep NEEDED || true
highest_glibc=$(
    readelf -V "$output_dir/dropbearmulti" |
        grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -n 1
)
echo "highest referenced glibc symbol version: ${highest_glibc:-none}"
