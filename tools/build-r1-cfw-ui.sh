#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
zig_dir=${ZIG_DIR:-$repo_dir/work/host-tools/zig-x86_64-linux-0.16.0}
zig=$zig_dir/zig
source_dir=$repo_dir/mods/cfw-ui/src
output_dir=${1:-$repo_dir/work/r1-cfw-ui-build}
cache_root=${R1_CFW_ZIG_CACHE_DIR:-$repo_dir/work/zig-cache/r1-cfw-ui}

if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [OUTPUT_DIR]" >&2
    exit 2
fi
if [ ! -x "$zig" ]; then
    echo "missing Zig compiler: $zig" >&2
    exit 1
fi

mkdir -p "$output_dir" "$cache_root/global" "$cache_root/local"
build_dir=$(mktemp -d "$output_dir/.build.XXXXXX")
cleanup() {
    rm -rf -- "$build_dir"
}
trap cleanup EXIT HUP INT TERM

export ZIG_GLOBAL_CACHE_DIR=$cache_root/global
export ZIG_LOCAL_CACHE_DIR=$cache_root/local

common_flags="-target mipsel-linux-gnueabihf.2.22 -march=mips32r2 \
    -mabi=32 -Os -ffunction-sections -fdata-sections -fstack-protector-strong \
    -D_FORTIFY_SOURCE=2 -Wall -Wextra -Werror"
common_link_flags="-Wl,--gc-sections -Wl,-z,relro -Wl,-z,now -s"

# shellcheck disable=SC2086
"$zig" cc $common_flags -fno-pie -no-pie \
    "$source_dir/r1_cfw_ui.c" \
    "$source_dir/r1_cfw_data.c" \
    "$source_dir/r1_cfw_platform.c" \
    $common_link_flags -o "$build_dir/r1-cfw-ui"

# shellcheck disable=SC2086
"$zig" cc $common_flags -shared -fPIC -fvisibility=hidden \
    "$source_dir/r1_cfw_hook.c" \
    $common_link_flags -Wl,-soname,libr1-cfw-hook.so \
    -ldl \
    -o "$build_dir/libr1-cfw-hook.so"

install -m 0755 "$build_dir/r1-cfw-ui" "$output_dir/r1-cfw-ui"
install -m 0755 "$build_dir/libr1-cfw-hook.so" \
    "$output_dir/libr1-cfw-hook.so"

for artifact in "$output_dir/r1-cfw-ui" "$output_dir/libr1-cfw-hook.so"; do
    if ! readelf -h "$artifact" | grep -q 'Machine:.*MIPS'; then
        echo "not a MIPS artifact: $artifact" >&2
        exit 1
    fi
    if ! readelf -h "$artifact" | grep -q 'mips32r2'; then
        echo "artifact is not MIPS32r2: $artifact" >&2
        exit 1
    fi
done

echo "built $output_dir/r1-cfw-ui"
echo "built $output_dir/libr1-cfw-hook.so"
file "$output_dir/r1-cfw-ui" "$output_dir/libr1-cfw-hook.so"
