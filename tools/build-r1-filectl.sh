#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
zig_dir=${ZIG_DIR:-$repo_dir/work/host-tools/zig-x86_64-linux-0.16.0}
zig=$zig_dir/zig
source=$repo_dir/mods/r1-filectl/src/r1_filectl.c
output_dir=${1:-$repo_dir/work/r1-filectl-build}
cache_root=${R1_FILECTL_ZIG_CACHE_DIR:-$repo_dir/work/zig-cache/r1-filectl}

[ "$#" -le 1 ] || { echo "Usage: $0 [OUTPUT_DIR]" >&2; exit 2; }
[ -x "$zig" ] || { echo "missing Zig compiler: $zig" >&2; exit 1; }

mkdir -p "$output_dir" "$cache_root/global" "$cache_root/local"
build_dir=$(mktemp -d "$output_dir/.build.XXXXXX")
trap 'rm -rf -- "$build_dir"' EXIT HUP INT TERM
export ZIG_GLOBAL_CACHE_DIR=$cache_root/global
export ZIG_LOCAL_CACHE_DIR=$cache_root/local

"$zig" cc -target mipsel-linux-gnueabihf.2.22 -march=mips32r2 -mabi=32 \
    -Os -ffunction-sections -fdata-sections -fstack-protector-strong \
    -D_FORTIFY_SOURCE=2 -Wall -Wextra -Werror -fno-pie -no-pie \
    -Wl,--gc-sections -Wl,-z,relro -Wl,-z,now -s \
    "$source" -o "$build_dir/r1-filectl"
install -m 0755 "$build_dir/r1-filectl" "$output_dir/r1-filectl"

readelf -h "$output_dir/r1-filectl" | grep -q 'Machine:.*MIPS'
readelf -h "$output_dir/r1-filectl" | grep -q 'mips32r2'
if readelf -d "$output_dir/r1-filectl" | grep -q 'libstdc++'; then
    echo "r1-filectl unexpectedly depends on libstdc++" >&2
    exit 1
fi
file "$output_dir/r1-filectl"
