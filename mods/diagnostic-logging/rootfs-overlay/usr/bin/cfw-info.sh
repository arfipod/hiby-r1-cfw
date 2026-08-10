#!/bin/sh

output_dir="/data/mnt/sd_0/cfw-logs"
output_file="$output_dir/system-$(date +%Y%m%d-%H%M%S).txt"
mkdir -p "$output_dir" || exit 1

{
    echo "HiBy R1 diagnostic snapshot"
    date
    echo
    echo "## uname"
    uname -a
    echo
    echo "## kernel command line"
    cat /proc/cmdline
    echo
    echo "## MTD partitions"
    cat /proc/mtd
    echo
    echo "## mounts"
    mount
    echo
    echo "## memory"
    cat /proc/meminfo
    echo
    echo "## processes"
    ps
    echo
    echo "## modules"
    lsmod
    echo
    echo "## recent kernel log"
    dmesg | tail -n 500
} > "$output_file" 2>&1

echo "$output_file"
