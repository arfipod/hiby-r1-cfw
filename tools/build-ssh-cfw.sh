#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Use the stock kernel timestamp as a deterministic build epoch. Mksquashfs
# clamps only newer overlay timestamps; stock file timestamps remain intact.
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1767003664}
export SOURCE_DATE_EPOCH
if [ -d "$repo_dir/work/host-tools/usr/bin" ]; then
    PATH=$repo_dir/work/host-tools/usr/bin:$PATH
    export PATH
fi
firmware=${1:-$repo_dir/r1.upt}
work_dir=${R1_SSH_CFW_WORK_DIR:-$repo_dir/work/r1-ssh-cfw}
output=${R1_SSH_CFW_OUTPUT:-$repo_dir/dist/r1-cfw-ssh-toggle-1.6.upt}
stock_unpack=$work_dir/stock
stock_root=$work_dir/stock-rootfs
root=$work_dir/rootfs
rootfs_image=$work_dir/rootfs.squashfs
check_dir=$work_dir/check
check_root=$work_dir/check-rootfs
dropbear=$repo_dir/work/ssh-build/output/dropbearmulti
overlay=$repo_dir/mods/ssh-dropbear/rootfs-overlay

if [ ! -f "$firmware" ]; then
    echo "missing stock firmware: $firmware" >&2
    exit 1
fi
if [ ! -x "$dropbear" ]; then
    echo "missing Dropbear build: $dropbear" >&2
    echo "run tools/bootstrap-ssh-toolchain.sh and tools/build-dropbear-r1.sh" >&2
    exit 1
fi

mkdir -p "$work_dir" "$(dirname "$output")"
python3 "$repo_dir/tools/r1fw.py" unpack "$firmware" "$stock_unpack" --force
python3 "$repo_dir/tools/r1fw.py" extract-rootfs \
    "$stock_unpack/images/rootfs.squashfs" "$stock_root" --force
python3 "$repo_dir/tools/r1fw.py" extract-rootfs \
    "$stock_unpack/images/rootfs.squashfs" "$root" --force
python3 "$repo_dir/tools/r1fw.py" apply-overlay "$root" "$overlay"

install -m 0755 "$dropbear" "$root/usr/sbin/dropbearmulti"
ln -sf dropbearmulti "$root/usr/sbin/dropbear"
ln -sf ../sbin/dropbearmulti "$root/usr/bin/dropbearkey"
ln -sf ../sbin/dropbearmulti "$root/usr/bin/scp"
chmod 0700 "$root/root/.ssh"
chmod 0600 "$root/etc/shadow"

python3 "$repo_dir/tools/patch_r1_branding.py" "$root"
python3 "$repo_dir/tools/patch_r1_ssh_toggle.py" apply "$root"

python3 "$repo_dir/tools/r1fw.py" build-rootfs "$root" "$rootfs_image" --force
python3 "$repo_dir/tools/r1fw.py" pack \
    --ximage "$stock_unpack/images/xImage" \
    --rootfs "$rootfs_image" --output "$output" --force

# Re-extract the final container. This repeats the chain/MD5/size checks and
# proves the stock kernel was copied byte-for-byte.
python3 "$repo_dir/tools/r1fw.py" unpack "$output" "$check_dir" --force
cmp "$stock_unpack/images/xImage" "$check_dir/images/xImage"
cmp "$rootfs_image" "$check_dir/images/rootfs.squashfs"
python3 "$repo_dir/tools/r1fw.py" extract-rootfs \
    "$check_dir/images/rootfs.squashfs" "$check_root" --force
python3 "$repo_dir/tools/patch_r1_branding.py" "$check_root" --check
python3 "$repo_dir/tools/patch_r1_ssh_toggle.py" verify "$check_root"

set -- python3 "$repo_dir/tools/r1fw.py" diff-rootfs \
    "$stock_root" "$check_root" \
    --strict \
    --expect-added root/.ssh \
    --expect-added root/.ssh/authorized_keys \
    --expect-added etc/init.d/S91dropbear \
    --expect-added usr/bin/r1-ssh-control \
    --expect-added usr/bin/dropbearkey \
    --expect-added usr/bin/scp \
    --expect-added usr/sbin/dropbear \
    --expect-added usr/sbin/dropbearmulti \
    --expect-changed etc/shadow \
    --expect-changed usr/bin/hiby_player \
    --expect-changed usr/resource/layout/theme1/hiby_about_dev.view \
    --expect-changed usr/resource/layout/theme2/hiby_about_dev.view \
    --expect-changed usr/resource/layout/midi/theme1/hiby_about_dev.view

resource_languages="
english
french
german
italy
japanese
korean
poland
russian
simplified_chinese
spain
thai
traditional_chinese
ukrainian
"
for language in $resource_languages; do
    set -- "$@" \
        --expect-changed "usr/resource/str/$language/about_dev.ini" \
        --expect-changed "usr/resource/str/$language/developer_options.ini"
done
"$@"

echo "built and re-verified: $output"
sha256sum "$output"
