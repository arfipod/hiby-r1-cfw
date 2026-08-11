#!/bin/sh
set -eu

# Run the proprietary player with emulator-only framebuffer, DMA, alignment,
# and touch compatibility. This is an application harness, not X1600 board
# emulation and not a firmware payload.

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_rootfs=${R1_QEMU_ROOTFS:-}
squashfs=${R1_QEMU_SQUASHFS:-$repo_dir/work/r1-1.6/images/rootfs.squashfs}
runtime=${R1_QEMU_UI_RUNTIME:-$repo_dir/work/gui-qemu/runtime}
profile_template=${R1_QEMU_USER_INI:-}
sd_present=${R1_QEMU_SD_PRESENT:-1}
cfw_crash_after_ms=${R1_QEMU_CFW_CRASH_AFTER_MS:-}
root_backend=${R1_QEMU_ROOT_BACKEND:-auto}
skip_sys_server=${R1_QEMU_SKIP_SYS_SERVER:-0}
rootfs=$runtime/rootfs
user_data=$runtime/usr-data
runtime_run=$runtime/run
runtime_dev=$runtime/dev
qemu=$repo_dir/work/host-tools/qemu-user/usr/bin/qemu-mipsel
proot=$repo_dir/work/host-tools/proot/usr/bin/proot
proot_lib=$repo_dir/work/host-tools/proot/usr/lib/x86_64-linux-gnu
bwrap=${R1_BWRAP:-$(command -v bwrap 2>/dev/null || true)}
unsquashfs=${R1_UNSQUASHFS:-$repo_dir/work/host-tools/usr/bin/unsquashfs}
shim_dir=$repo_dir/work/gui-qemu/build
input_nodes=$runtime/dev-input
input_node=$input_nodes/event0
touch_fifo=$runtime/touch/event0

case "$sd_present" in
    0|1)
        ;;
    *)
        echo "R1_QEMU_SD_PRESENT must be 0 or 1, got: $sd_present" >&2
        exit 1
        ;;
esac
case "$cfw_crash_after_ms" in
    "")
        ;;
    *[!0-9]*)
        echo "R1_QEMU_CFW_CRASH_AFTER_MS must be an integer from 1 to 3600000" >&2
        exit 1
        ;;
    *)
        if [ "$cfw_crash_after_ms" -lt 1 ] || [ "$cfw_crash_after_ms" -gt 3600000 ]; then
            echo "R1_QEMU_CFW_CRASH_AFTER_MS must be an integer from 1 to 3600000" >&2
            exit 1
        fi
        ;;
esac
case "$root_backend" in
    auto|proot|bwrap)
        ;;
    *)
        echo "R1_QEMU_ROOT_BACKEND must be auto, proot, or bwrap" >&2
        exit 1
        ;;
esac
case "$skip_sys_server" in
    0|1)
        ;;
    *)
        echo "R1_QEMU_SKIP_SYS_SERVER must be 0 or 1" >&2
        exit 1
        ;;
esac

# PRoot is the portable fallback, but it relies on ptrace and is unavailable
# in some managed development sandboxes. Bubblewrap executes the same static
# qemu-user binary inside a disposable mount namespace without ptrace.
if [ "$root_backend" = auto ]; then
    if [ -n "$bwrap" ] && command -v unshare >/dev/null 2>&1 &&
       unshare --user --map-root-user \
           "$bwrap" --ro-bind / / /bin/true >/dev/null 2>&1; then
        root_backend=bwrap
    else
        root_backend=proot
    fi
fi

# Every player process numbers its private evdev endpoints from zero. Sharing
# one runtime would therefore make two emulators read and split the same FIFO,
# producing deceptively corrupted gestures. Keep the complete session state
# single-owner and ask callers to select another R1_QEMU_UI_RUNTIME when they
# intentionally need a parallel instance.
if ! command -v flock >/dev/null 2>&1; then
    echo "flock is required to protect the QEMU UI runtime" >&2
    exit 1
fi
mkdir -p "$runtime"
exec 9>"$runtime/session.lock"
if ! flock -n 9; then
    echo "another QEMU UI session owns this runtime: $runtime" >&2
    echo "set R1_QEMU_UI_RUNTIME to a different directory for a parallel run" >&2
    exit 1
fi

set -- "$qemu"
if [ "$root_backend" = proot ]; then
    set -- "$@" "$proot"
else
    set -- "$@" "$bwrap" "$(command -v unshare)"
fi
for required in "$@"; do
    if [ ! -e "$required" ]; then
        echo "missing QEMU UI dependency: $required" >&2
        echo "extract the rootfs and run tools/bootstrap-ssh-toolchain.sh first" >&2
        exit 1
    fi
done
if [ -n "$source_rootfs" ]; then
    for required in "$source_rootfs/lib/ld.so.1" \
                    "$source_rootfs/usr/bin/hiby_player"; do
        if [ ! -e "$required" ]; then
            echo "invalid R1_QEMU_ROOTFS: $required is missing" >&2
            exit 1
        fi
    done
elif [ ! -f "$squashfs" ]; then
    echo "missing SquashFS image: $squashfs" >&2
    exit 1
elif [ ! -x "$unsquashfs" ]; then
    echo "unsquashfs is required to create the disposable QEMU root" >&2
    exit 1
fi

"$repo_dir/tools/build-r1-ui-shim.sh"
mkdir -p "$input_nodes" "$runtime/touch" "$runtime_run" "$runtime_dev" \
    "$user_data"
if [ "$sd_present" = 1 ]; then
    mkdir -p "$user_data/mnt/sd_0/Music/Lukrembo/Jay"
fi
if [ ! -e "$rootfs/.r1-qemu-session-root" ]; then
    if [ -e "$rootfs" ]; then
        echo "unrecognized existing session root: $rootfs" >&2
        echo "move it aside, then rerun this command" >&2
        exit 1
    fi
    if [ -n "$source_rootfs" ]; then
        mkdir -p "$rootfs"
        cp -a "$source_rootfs/." "$rootfs/"
    else
        "$unsquashfs" -no-progress -d "$rootfs" "$squashfs" >/dev/null
    fi
    touch "$rootfs/.r1-qemu-session-root"
fi
# Pre-create PRoot bind targets so interrupted sessions cannot leave mode-000
# placeholders in the disposable root. The extracted stock tree remains clean.
mkdir -p "$rootfs/dev/input" "$rootfs/run" "$rootfs/tmp/r1-ui-lib" \
    "$rootfs/tmp/r1-ui-run"
chmod 0755 "$rootfs/dev/input" "$rootfs/tmp/r1-ui-lib" "$rootfs/tmp/r1-ui-run"
if [ "$root_backend" = bwrap ]; then
    # File bind targets must exist before bubblewrap constructs the private
    # mount namespace. This placeholder lives only in the disposable root.
    : > "$rootfs/tmp/r1-host-qemu"
    chmod 0755 "$rootfs/tmp/r1-host-qemu"
fi
for device_name in null zero random urandom; do
    if [ ! -e "$rootfs/dev/$device_name" ]; then
        touch "$rootfs/dev/$device_name"
    fi
    chmod 0666 "$rootfs/dev/$device_name"
done
for device_path in "$rootfs/dev/mmcblk0p1" "$runtime_dev/mmcblk0p1"; do
    if [ -L "$device_path" ] || { [ -e "$device_path" ] && [ ! -f "$device_path" ]; }; then
        echo "refusing unexpected disposable MMC sentinel: $device_path" >&2
        exit 1
    fi
    if [ "$sd_present" = 1 ]; then
        touch "$device_path"
    else
        rm -f "$device_path"
    fi
done
if [ ! -e "$rootfs/etc/ld.so.preload" ]; then
    touch "$rootfs/etc/ld.so.preload"
fi
chmod 0644 "$rootfs/etc/ld.so.preload"
if [ ! -e "$user_data/asound.conf" ] && [ -e "$rootfs/usr/data/asound.conf" ]; then
    cp -p "$rootfs/usr/data/asound.conf" "$user_data/asound.conf"
fi
# user.ini is a 2,768-byte binary structure. Prepare a separate copy, then
# atomically replace only the disposable runtime profile. This bypasses the
# language onboarding page and disables idle/sleep shutdown without discarding
# any player-maintained opaque fields from a previous emulator session.
if [ -n "$profile_template" ]; then
    profile_source=$profile_template
else
    profile_source=$user_data/user.ini
    if [ ! -f "$profile_source" ]; then
        profile_source=$rootfs/usr/data/user.ini
    fi
fi
if [ ! -f "$profile_source" ]; then
    echo "missing stock user.ini template: $profile_source" >&2
    exit 1
fi
profile_size=$(wc -c < "$profile_source")
if [ "$profile_size" -ne 2768 ]; then
    echo "invalid user.ini template size: $profile_size bytes; expected 2768" >&2
    exit 1
fi
prepared_profile=$user_data/.user.ini.qemu-prepared
python3 "$repo_dir/tools/r1-qemu-ui/userdata.py" prepare \
    "$profile_source" "$prepared_profile" >/dev/null
mv -f "$prepared_profile" "$user_data/user.ini"

# The hardware selector bind-mounts one generated launcher at boot. PRoot does
# not provide that boot sequence, so materialize the same allowlisted variant
# only inside the disposable session root. The extracted source rootfs remains
# untouched. Persist a single normalized mask for deterministic restarts.
launcher_variants=$rootfs/usr/resource/r1-cfw/launcher
if [ -d "$launcher_variants" ]; then
    launcher_state_dir=$user_data/r1-cfw
    launcher_config=$launcher_state_dir/launcher.conf
    launcher_mask=$(python3 "$repo_dir/tools/patch_r1_launcher.py" \
        select-config "$launcher_config")
    case "$launcher_mask" in
        [0-9a-f][0-9a-f])
            ;;
        *)
            echo "invalid normalized CFW launcher mask: $launcher_mask" >&2
            exit 1
            ;;
    esac

    launcher_variant_complete() {
        selected_mask=$1
        [ -f "$launcher_variants/theme1/$selected_mask.view" ] &&
            [ -f "$launcher_variants/theme2/$selected_mask.view" ] &&
            [ -f "$launcher_variants/midi-theme1/$selected_mask.view" ]
    }
    if ! launcher_variant_complete "$launcher_mask"; then
        launcher_mask=71
    fi
    if ! launcher_variant_complete "$launcher_mask"; then
        echo "missing complete default CFW launcher variant: 71" >&2
        exit 1
    fi

    mkdir -p "$launcher_state_dir"
    normalized_config=$launcher_state_dir/.launcher.conf.qemu-normalized
    printf 'launcher_mask=%s\n' "$launcher_mask" > "$normalized_config"
    chmod 0600 "$normalized_config"
    mv -f "$normalized_config" "$launcher_config"

    for launcher_mapping in \
        "theme1:theme1" \
        "theme2:theme2" \
        "midi-theme1:midi/theme1"
    do
        source_theme=${launcher_mapping%%:*}
        target_theme=${launcher_mapping#*:}
        launcher_source=$launcher_variants/$source_theme/$launcher_mask.view
        launcher_target=$rootfs/usr/resource/layout/$target_theme/launcher/hiby_launcher_apps.view
        if [ -L "$launcher_target" ] || [ ! -f "$launcher_target" ]; then
            echo "invalid disposable stock launcher target: $launcher_target" >&2
            exit 1
        fi
        cp -p "$launcher_source" "$launcher_target"
    done
    echo "CFW launcher mask: $launcher_mask"
fi

if [ "$sd_present" = 1 ] && [ -f "$repo_dir/misc/test-audio.mp3" ]; then
    cp -p "$repo_dir/misc/test-audio.mp3" \
        "$user_data/mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3"
fi
for fifo_path in "$input_node" "$touch_fifo"; do
    if [ -e "$fifo_path" ] && [ ! -p "$fifo_path" ]; then
        echo "refusing to replace non-FIFO path: $fifo_path" >&2
        exit 1
    fi
    if [ ! -p "$fifo_path" ]; then
        mkfifo -m 0600 "$fifo_path"
    fi
done
# Child programs inherit the preload, so only the host runner may reset shared
# diagnostics.  Exact disposable targets are used; the extracted firmware and
# the source MP3 are never modified.
truncate -s 0 "$runtime/frame-state.bin" "$runtime/input-ioctl.bin" \
    "$runtime/input-events.bin" "$runtime/crash-state.bin" "$runtime/framebuffer.raw" \
    "$runtime/hgl-dma.raw" "$runtime/cfw-state.bin"

guest_preload=/tmp/r1-ui-lib/libr1-qemu-fbshim.so
if [ -f "$rootfs/usr/lib/libr1-cfw-hook.so" ]; then
    guest_preload=/usr/lib/libr1-cfw-hook.so:$guest_preload
fi
guest_environment="LD_PRELOAD=$guest_preload"
guest_environment="$guest_environment,R1_QEMU_FB_PATH=/tmp/r1-ui-run/framebuffer.raw"
guest_environment="$guest_environment,R1_QEMU_DMA_PATH=/tmp/r1-ui-run/hgl-dma.raw"
guest_environment="$guest_environment,R1_QEMU_STATE_PATH=/tmp/r1-ui-run/frame-state.bin"
guest_environment="$guest_environment,R1_QEMU_TOUCH_PATH=/tmp/r1-ui-run/touch/event0"
guest_environment="$guest_environment,R1_QEMU_INPUT_LOG_PATH=/tmp/r1-ui-run/input-ioctl.bin"
guest_environment="$guest_environment,R1_QEMU_INPUT_EVENT_LOG_PATH=/tmp/r1-ui-run/input-events.bin"
guest_environment="$guest_environment,R1_QEMU_CRASH_LOG_PATH=/tmp/r1-ui-run/crash-state.bin"
guest_environment="$guest_environment,R1_CFW_FB_FORMAT=rgb565-padded"
guest_environment="$guest_environment,R1_CFW_FB_PACKED_RGB565=1"
guest_environment="$guest_environment,R1_CFW_TEST_STATE_PATH=/tmp/r1-ui-run/cfw-state.bin"
if [ -n "$cfw_crash_after_ms" ]; then
    guest_environment="$guest_environment,R1_CFW_TEST_CRASH_AFTER_MS=$cfw_crash_after_ms"
fi
if [ "$sd_present" = 1 ]; then
    guest_environment="$guest_environment,R1_CFW_ASSUME_SD_MOUNTED=1"
fi

stub_pid=
cleanup_stub() {
    if [ -n "$stub_pid" ]; then
        kill "$stub_pid" 2>/dev/null || true
        wait "$stub_pid" 2>/dev/null || true
    fi
}
trap cleanup_stub EXIT
if [ "$skip_sys_server" = 0 ]; then
    python3 "$repo_dir/tools/r1-qemu-ui/sys_server_stub.py" \
        "$runtime_run/sys_server" &
    stub_pid=$!
    stub_wait=0
    while [ ! -S "$runtime_run/sys_server" ] && [ "$stub_wait" -lt 100 ]; do
        if ! kill -0 "$stub_pid" 2>/dev/null; then
            echo "sys_server stub exited before creating its socket" >&2
            exit 1
        fi
        sleep 0.05
        stub_wait=$((stub_wait + 1))
    done
    if [ ! -S "$runtime_run/sys_server" ]; then
        echo "timed out waiting for the sys_server stub" >&2
        exit 1
    fi
else
    echo "sys_server stub: explicitly disabled for this QEMU session"
fi

echo "runtime directory: $runtime"
echo "session rootfs: $rootfs"
echo "persistent user data: $user_data"
echo "simulated microSD present: $sd_present"
echo "root backend: $root_backend"
echo "framebuffer: $runtime/framebuffer.raw"
echo "frame state: $runtime/frame-state.bin"
echo "CFW semantic state: $runtime/cfw-state.bin"
echo "touch FIFO: $touch_fifo"
echo "start the viewer in another terminal:"
echo "  python3 \"$repo_dir/tools/r1-qemu-ui/bridge.py\" serve \"$runtime/framebuffer.raw\" \"$touch_fifo\""
echo "    --state \"$runtime/frame-state.bin\""

run_player_proot() {
    isolate_network=$1
    set -- "$proot" -0 -r "$rootfs" \
        -b "$shim_dir:/tmp/r1-ui-lib" \
        -b "$runtime:/tmp/r1-ui-run" \
        -b "$input_nodes:/dev/input" \
        -b "$runtime_run:/run"
    if [ "$sd_present" = 1 ]; then
        set -- "$@" -b "$runtime_dev/mmcblk0p1:/dev/mmcblk0p1"
    fi
    set -- "$@" \
        -b "$user_data:/usr/data" \
        -b /dev/null:/dev/null -b /dev/zero:/dev/zero \
        -b /dev/random:/dev/random -b /dev/urandom:/dev/urandom \
        -b /proc:/proc \
        -w /usr/bin -q "$qemu" /usr/bin/hiby_player

    if [ "$isolate_network" = 1 ]; then
        env \
            LD_LIBRARY_PATH="$proot_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            PROOT_NO_SECCOMP=1 \
            QEMU_CPU=XBurstR2 \
            QEMU_SET_ENV="$guest_environment" \
            unshare --net --map-root-user "$@"
    else
        env \
            LD_LIBRARY_PATH="$proot_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            PROOT_NO_SECCOMP=1 \
            QEMU_CPU=XBurstR2 \
            QEMU_SET_ENV="$guest_environment" \
            "$@"
    fi
}

run_player_bwrap() {
    isolate_network=$1
    set -- "$bwrap" --die-with-parent \
        --bind "$rootfs" / \
        --ro-bind "$qemu" /tmp/r1-host-qemu \
        --ro-bind "$shim_dir" /tmp/r1-ui-lib \
        --bind "$runtime" /tmp/r1-ui-run \
        --bind "$input_nodes" /dev/input \
        --bind "$runtime_run" /run
    if [ "$sd_present" = 1 ]; then
        set -- "$@" --bind "$runtime_dev/mmcblk0p1" /dev/mmcblk0p1
    fi
    set -- "$@" \
        --bind "$user_data" /usr/data \
        --dev-bind /dev/null /dev/null \
        --dev-bind /dev/zero /dev/zero \
        --dev-bind /dev/random /dev/random \
        --dev-bind /dev/urandom /dev/urandom \
        --proc /proc \
        --chdir /usr/bin \
        /tmp/r1-host-qemu /usr/bin/hiby_player

    if [ "$isolate_network" = 1 ]; then
        env \
            QEMU_CPU=XBurstR2 \
            QEMU_SET_ENV="$guest_environment" \
            unshare --user --net --map-root-user "$@"
    else
        env \
            QEMU_CPU=XBurstR2 \
            QEMU_SET_ENV="$guest_environment" \
            unshare --user --map-root-user "$@"
    fi
}

run_player() {
    if [ "$root_backend" = bwrap ]; then
        run_player_bwrap "$1"
    else
        run_player_proot "$1"
    fi
}

if [ "${R1_QEMU_ALLOW_NETWORK:-0}" = 1 ]; then
    run_player 0
else
    if ! command -v unshare >/dev/null 2>&1; then
        echo "unshare is required for the default network-isolated run" >&2
        exit 1
    fi
    run_player 1
fi
