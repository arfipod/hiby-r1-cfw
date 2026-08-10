#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
zig=${ZIG_DIR:-$repo_dir/work/host-tools/zig-x86_64-linux-0.16.0}/zig
source=$repo_dir/tools/r1-qemu-ui/fbshim.c
output_dir=${R1_UI_SHIM_OUTPUT_DIR:-$repo_dir/work/gui-qemu/build}

if [ ! -x "$zig" ]; then
    echo "missing Zig compiler; run tools/bootstrap-ssh-toolchain.sh" >&2
    exit 1
fi

mkdir -p "$output_dir" "$repo_dir/work/zig-cache/global" \
    "$repo_dir/work/zig-cache/local"
export ZIG_GLOBAL_CACHE_DIR=$repo_dir/work/zig-cache/global
export ZIG_LOCAL_CACHE_DIR=$repo_dir/work/zig-cache/local

"$zig" cc -target mipsel-linux-gnueabihf.2.22 \
    -march=mips32r2 -mabi=32 -Os -fPIC -shared \
    -Wl,-soname,libr1-qemu-fbshim.so \
    -o "$output_dir/libr1-qemu-fbshim.so" "$source" -ldl

file "$output_dir/libr1-qemu-fbshim.so"
