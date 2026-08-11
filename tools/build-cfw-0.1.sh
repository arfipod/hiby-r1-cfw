#!/bin/sh
# Build, independently verify, and evidence-gate the experimental R1 CFW v0.1.
set -eu
umask 022

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1767003664}
export SOURCE_DATE_EPOCH
if [ -d "$repo_dir/work/host-tools/usr/bin" ]; then
    PATH=$repo_dir/work/host-tools/usr/bin:$PATH
    export PATH
fi

mode=${1:-all}
case "$mode" in
    prepare|publish|all) ;;
    *)
        echo "Usage: $0 [prepare|publish|all] [STOCK_UPT]" >&2
        exit 2
        ;;
esac
firmware=${2:-$repo_dir/r1.upt}
if [ "$#" -gt 2 ]; then
    echo "Usage: $0 [prepare|publish|all] [STOCK_UPT]" >&2
    exit 2
fi

stock_upt_sha256=9aada81995d8d2b2ed80d6cf292c62bc5f0f705e51e4f69c7e766ee67536ba60
rootfs_max_size=47185920
work_dir=${R1_CFW_WORK_DIR:-$repo_dir/work/r1-cfw-0.1}
candidate=${R1_CFW_CANDIDATE:-$work_dir/r1-cfw-0.1-experimental.candidate.upt}
verified_candidate=$work_dir/verified-candidate.upt
output=${R1_CFW_OUTPUT:-$repo_dir/dist/r1-cfw-0.1-experimental.upt}
validation_manifest=${R1_CFW_VALIDATION_MANIFEST:-$repo_dir/artifacts/ui/cfw-v0.1/validation-manifest.json}
stock_unpack=$work_dir/stock
stock_root=$work_dir/stock-rootfs
root=$work_dir/rootfs
rootfs_image=$work_dir/rootfs.squashfs
check_dir=$work_dir/check
check_root=$work_dir/check-rootfs
ui_build=$work_dir/ui-build
ui_verify_build=$work_dir/ui-verify-build
retro_theme_work=$work_dir/retro-theme
retro_verify_work=$work_dir/retro-theme-verify
retro_expected_paths=$work_dir/retro-theme-expected-paths.txt
dropbear=$repo_dir/work/ssh-build/output/dropbearmulti
ssh_overlay=$repo_dir/mods/ssh-dropbear/rootfs-overlay
cfw_overlay=$repo_dir/mods/cfw-ui/rootfs-overlay
retro_theme_tool=$repo_dir/tools/patch_r1_retro_theme.py
retro_launcher_tool=$repo_dir/tools/patch_r1_retro_launcher.py

# Every mode reuses and, during verification, replaces paths below work_dir.
# Serialize the complete lifecycle so an accidental parallel prepare/publish
# cannot mix two candidates or invalidate a just-produced evidence digest.
if ! command -v flock >/dev/null 2>&1; then
    echo "flock is required to protect the CFW build workspace" >&2
    exit 1
fi
mkdir -p "$work_dir"
exec 8>"$work_dir/pipeline.lock"
if ! flock -n 8; then
    echo "another CFW v0.1 build owns the workspace: $work_dir" >&2
    exit 1
fi

verify_stock_input() {
    python3 "$repo_dir/tools/r1fw.py" verify-file "$firmware" \
        --sha256 "$stock_upt_sha256"
}

require_build_inputs() {
    if [ ! -x "$dropbear" ]; then
        echo "missing Dropbear build: $dropbear" >&2
        echo "run tools/bootstrap-ssh-toolchain.sh and tools/build-dropbear-r1.sh" >&2
        exit 1
    fi
    for required in "$ssh_overlay" "$cfw_overlay"; do
        if [ ! -d "$required" ]; then
            echo "missing rootfs overlay: $required" >&2
            exit 1
        fi
    done
    for required in \
        "$retro_theme_tool" \
        "$retro_launcher_tool" \
        "$repo_dir/tools/build_retro_theme.py" \
        "$repo_dir/tools/retro_theme_common.py" \
        "$repo_dir/tools/retro_theme_assets.py" \
        "$repo_dir/tools/retro_theme_package.py" \
        "$repo_dir/tools/retro_theme_integration.py"
    do
        if [ ! -f "$required" ]; then
            echo "missing Retro Handheld build input: $required" >&2
            exit 1
        fi
    done
    if ! python3 -c 'import PIL' >/dev/null 2>&1; then
        echo "Pillow is required to generate the Retro Handheld theme" >&2
        exit 1
    fi
}

require_mode() {
    expected_mode=$1
    mode_path=$2
    actual_mode=$(stat -c '%a' "$mode_path")
    if [ "$actual_mode" != "$expected_mode" ]; then
        echo "unexpected mode for $mode_path: $actual_mode (expected $expected_mode)" >&2
        exit 1
    fi
}

strict_rootfs_diff() {
    set -- python3 "$repo_dir/tools/r1fw.py" diff-rootfs \
        "$stock_root" "$check_root" --strict \
        --expect-added root/.ssh \
        --expect-added root/.ssh/authorized_keys \
        --expect-added etc/init.d/S90r1-cfw \
        --expect-added etc/init.d/S91dropbear \
        --expect-added usr/bin/dropbearkey \
        --expect-added usr/bin/r1-cfw-ui \
        --expect-added usr/bin/r1-ssh-control \
        --expect-added usr/bin/scp \
        --expect-added usr/lib/libr1-cfw-hook.so \
        --expect-added usr/resource/r1-cfw \
        --expect-added usr/resource/r1-cfw/launcher \
        --expect-added usr/resource/r1-cfw/launcher/theme1 \
        --expect-added usr/resource/r1-cfw/launcher/theme2 \
        --expect-added usr/resource/r1-cfw/launcher/midi-theme1 \
        --expect-added usr/resource/r1-cfw/launcher/retro \
        --expect-added usr/resource/litegui/theme1/launcher/cfw.png \
        --expect-added usr/resource/litegui/theme1/launcher/cfw_s.png \
        --expect-added usr/resource/litegui/theme2/launcher/cfw.png \
        --expect-added usr/resource/litegui/theme2/launcher/cfw_s.png \
        --expect-added usr/resource/litegui/midi/theme1/launcher/cfw.png \
        --expect-added usr/resource/litegui/midi/theme1/launcher/cfw_s.png \
        --expect-added usr/sbin/dropbear \
        --expect-added usr/sbin/dropbearmulti \
        --expect-changed etc/shadow \
        --expect-changed usr/bin/hiby_player \
        --expect-changed usr/bin/hiby_player.sh \
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
            --expect-changed "usr/resource/str/$language/developer_options.ini" \
            --expect-changed "usr/resource/str/$language/launcher.ini"
    done

    launcher_masks="
27 2b 2d 2e 2f 33 35 36 37 39 3a 3b 3c 3d 3e 3f
63 65 66 67 69 6a 6b 6c 6d 6e 6f 71 72 73 74 75
76 77 78 79 7a 7b 7c 7d 7e
"
    for theme in theme1 theme2 midi-theme1 retro; do
        for mask in $launcher_masks; do
            set -- "$@" \
                --expect-added "usr/resource/r1-cfw/launcher/$theme/$mask.view"
        done
    done

    python3 "$retro_theme_tool" expected-paths \
        "$retro_verify_work/package/retro" > "$retro_expected_paths"
    while IFS= read -r expected_path; do
        [ -n "$expected_path" ] || continue
        set -- "$@" --expect-added "$expected_path"
    done < "$retro_expected_paths"
    "$@"
}

verify_overlay_payloads() {
    cmp "$dropbear" "$check_root/usr/sbin/dropbearmulti"
    cmp "$ssh_overlay/etc/init.d/S91dropbear" "$check_root/etc/init.d/S91dropbear"
    cmp "$ssh_overlay/etc/shadow" "$check_root/etc/shadow"
    cmp "$ssh_overlay/usr/bin/r1-ssh-control" "$check_root/usr/bin/r1-ssh-control"
    cmp "$cfw_overlay/etc/init.d/S90r1-cfw" "$check_root/etc/init.d/S90r1-cfw"
    cmp "$ui_verify_build/r1-cfw-ui" "$check_root/usr/bin/r1-cfw-ui"
    cmp "$ui_verify_build/libr1-cfw-hook.so" "$check_root/usr/lib/libr1-cfw-hook.so"
    require_mode 755 "$check_root/etc/init.d/S90r1-cfw"
    require_mode 755 "$check_root/etc/init.d/S91dropbear"
    require_mode 600 "$check_root/etc/shadow"
    require_mode 700 "$check_root/root/.ssh"
    require_mode 755 "$check_root/usr/bin/r1-cfw-ui"
    require_mode 755 "$check_root/usr/bin/r1-ssh-control"
    require_mode 755 "$check_root/usr/lib/libr1-cfw-hook.so"
    require_mode 755 "$check_root/usr/sbin/dropbearmulti"
    test "$(readlink "$check_root/root/.ssh/authorized_keys")" = "/usr/data/dropbear/authorized_keys"
    test "$(readlink "$check_root/usr/sbin/dropbear")" = "dropbearmulti"
    test "$(readlink "$check_root/usr/bin/dropbearkey")" = "../sbin/dropbearmulti"
    test "$(readlink "$check_root/usr/bin/scp")" = "../sbin/dropbearmulti"
}

verify_candidate() {
    verify_stock_input
    require_build_inputs
    if [ ! -f "$candidate" ]; then
        echo "missing release candidate: $candidate" >&2
        echo "run: $0 prepare '$firmware'" >&2
        exit 1
    fi
    if [ ! -f "$rootfs_image" ]; then
        echo "missing exact rootfs image used by the candidate: $rootfs_image" >&2
        echo "run: $0 prepare '$firmware'" >&2
        exit 1
    fi

    staged_tmp=$(mktemp "$work_dir/.verified-candidate.XXXXXX")
    cp "$candidate" "$staged_tmp"
    chmod 0644 "$staged_tmp"
    mv -f "$staged_tmp" "$verified_candidate"
    candidate_upt_sha256=$(sha256sum "$verified_candidate" | awk '{print $1}')

    "$repo_dir/tools/build-r1-cfw-ui.sh" "$ui_verify_build"

    python3 "$repo_dir/tools/r1fw.py" unpack "$firmware" "$stock_unpack" --force
    python3 "$repo_dir/tools/r1fw.py" extract-rootfs \
        "$stock_unpack/images/rootfs.squashfs" "$stock_root" --force
    python3 "$repo_dir/tools/r1fw.py" unpack "$verified_candidate" "$check_dir" --force

    cmp "$stock_unpack/images/xImage" "$check_dir/images/xImage"
    cmp "$rootfs_image" "$check_dir/images/rootfs.squashfs"
    python3 "$repo_dir/tools/r1fw.py" verify-file \
        "$check_dir/images/rootfs.squashfs" --max-size "$rootfs_max_size"
    python3 "$repo_dir/tools/r1fw.py" extract-rootfs \
        "$check_dir/images/rootfs.squashfs" "$check_root" --force

    python3 "$repo_dir/tools/patch_r1_branding.py" "$check_root" --check
    python3 "$repo_dir/tools/patch_r1_ssh_toggle.py" verify "$check_root"
    python3 "$repo_dir/tools/patch_r1_launcher.py" verify "$check_root"
    python3 "$repo_dir/tools/patch_r1_cfw_integration.py" "$check_root" --check
    python3 "$retro_theme_tool" verify "$check_root" "$retro_verify_work"
    python3 "$retro_launcher_tool" verify "$check_root"
    verify_overlay_payloads
    strict_rootfs_diff

    candidate_rootfs_sha256=$(sha256sum "$check_dir/images/rootfs.squashfs" | awk '{print $1}')
    verified_upt_sha256=$(sha256sum "$verified_candidate" | awk '{print $1}')
    if [ "$verified_upt_sha256" != "$candidate_upt_sha256" ]; then
        echo "staged candidate changed during verification; refusing publication" >&2
        exit 1
    fi
    echo "verified candidate snapshot: $verified_candidate"
    echo "candidate UPT sha256: $candidate_upt_sha256"
    echo "candidate rootfs sha256: $candidate_rootfs_sha256"
}

prepare_candidate() {
    verify_stock_input
    require_build_inputs
    mkdir -p "$work_dir"

    python3 "$repo_dir/tools/r1fw.py" unpack "$firmware" "$stock_unpack" --force
    python3 "$repo_dir/tools/r1fw.py" extract-rootfs \
        "$stock_unpack/images/rootfs.squashfs" "$root" --force

    python3 "$repo_dir/tools/r1fw.py" apply-overlay "$root" "$ssh_overlay"
    python3 "$repo_dir/tools/r1fw.py" apply-overlay "$root" "$cfw_overlay"
    "$repo_dir/tools/build-r1-cfw-ui.sh" "$ui_build"

    install -m 0755 "$dropbear" "$root/usr/sbin/dropbearmulti"
    ln -sf dropbearmulti "$root/usr/sbin/dropbear"
    ln -sf ../sbin/dropbearmulti "$root/usr/bin/dropbearkey"
    ln -sf ../sbin/dropbearmulti "$root/usr/bin/scp"
    install -m 0755 "$ui_build/r1-cfw-ui" "$root/usr/bin/r1-cfw-ui"
    install -m 0755 "$ui_build/libr1-cfw-hook.so" \
        "$root/usr/lib/libr1-cfw-hook.so"
    chmod 0700 "$root/root/.ssh"
    chmod 0600 "$root/etc/shadow"

    python3 "$repo_dir/tools/patch_r1_branding.py" "$root"
    python3 "$repo_dir/tools/patch_r1_ssh_toggle.py" apply "$root"
    python3 "$repo_dir/tools/patch_r1_cfw_integration.py" "$root"
    python3 "$retro_theme_tool" generate "$root" "$retro_theme_work"
    python3 "$repo_dir/tools/patch_r1_launcher.py" generate "$root"
    python3 "$retro_launcher_tool" generate "$root"

    python3 "$repo_dir/tools/r1fw.py" build-rootfs "$root" "$rootfs_image" --force
    python3 "$repo_dir/tools/r1fw.py" verify-file \
        "$rootfs_image" --max-size "$rootfs_max_size"
    python3 "$repo_dir/tools/r1fw.py" pack \
        --ximage "$stock_unpack/images/xImage" \
        --rootfs "$rootfs_image" --output "$candidate" --force

    verify_candidate
    echo "candidate prepared but not published; QEMU validation is required"
}

publish_candidate() {
    verify_candidate
    python3 "$repo_dir/tools/verify_cfw_validation.py" \
        "$validation_manifest" "$candidate_rootfs_sha256" \
        "$check_dir/images/rootfs.squashfs"

    output_dir=$(dirname "$output")
    mkdir -p "$output_dir"
    output_name=$(basename "$output")
    publish_tmp=$(mktemp "$output_dir/.$output_name.XXXXXX")
    cleanup_publish_tmp() {
        rm -f -- "$publish_tmp"
    }
    trap cleanup_publish_tmp EXIT HUP INT TERM
    cp "$verified_candidate" "$publish_tmp"
    chmod 0644 "$publish_tmp"
    cmp "$verified_candidate" "$publish_tmp"
    published_upt_sha256=$(sha256sum "$publish_tmp" | awk '{print $1}')
    if [ "$published_upt_sha256" != "$candidate_upt_sha256" ]; then
        echo "candidate changed after verification; refusing publication" >&2
        exit 1
    fi
    mv -f "$publish_tmp" "$output"
    trap - EXIT HUP INT TERM

    echo "published evidence-gated firmware: $output"
    sha256sum "$output"
}

case "$mode" in
    prepare)
        prepare_candidate
        ;;
    publish)
        publish_candidate
        ;;
    all)
        prepare_candidate
        publish_candidate
        ;;
esac
