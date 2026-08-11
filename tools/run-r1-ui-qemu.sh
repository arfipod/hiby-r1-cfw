#!/bin/sh
set -eu

# Run the proprietary player with emulator-only framebuffer, DMA, alignment,
# and touch compatibility. This is an application harness, not X1600 board
# emulation and not a firmware payload.

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_rootfs=${R1_QEMU_ROOTFS:-}
squashfs=${R1_QEMU_SQUASHFS:-$repo_dir/work/r1-1.6/images/rootfs.squashfs}
runtime=${R1_QEMU_UI_RUNTIME:-$repo_dir/work/gui-qemu/runtime}
rootfs=$runtime/rootfs
user_data=$runtime/usr-data
runtime_run=$runtime/run
runtime_dev=$runtime/dev
qemu=$repo_dir/work/host-tools/qemu-user/usr/bin/qemu-mipsel
proot=$repo_dir/work/host-tools/proot/usr/bin/proot
proot_lib=$repo_dir/work/host-tools/proot/usr/lib/x86_64-linux-gnu
unsquashfs=${R1_UNSQUASHFS:-$repo_dir/work/host-tools/usr/bin/unsquashfs}
shim_dir=$repo_dir/work/gui-qemu/build
input_nodes=$runtime/dev-input
input_node=$input_nodes/event0
touch_fifo=$runtime/touch/event0

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

for required in "$qemu" "$proot"; do
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
    "$user_data/mnt/sd_0/Music/Lukrembo/Jay"
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
for device_name in null zero random urandom; do
    if [ ! -e "$rootfs/dev/$device_name" ]; then
        touch "$rootfs/dev/$device_name"
    fi
    chmod 0666 "$rootfs/dev/$device_name"
done
if [ ! -e "$rootfs/dev/mmcblk0p1" ]; then
    touch "$rootfs/dev/mmcblk0p1"
fi
touch "$runtime_dev/mmcblk0p1"
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
profile_source=$user_data/user.ini
if [ ! -f "$profile_source" ]; then
    profile_source=$rootfs/usr/data/user.ini
fi
if [ ! -f "$profile_source" ]; then
    echo "missing stock user.ini template: $profile_source" >&2
    exit 1
fi
prepared_profile=$user_data/.user.ini.qemu-prepared
python3 "$repo_dir/tools/r1-qemu-ui/userdata.py" prepare \
    "$profile_source" "$prepared_profile" >/dev/null
mv -f "$prepared_profile" "$user_data/user.ini"
if [ -f "$repo_dir/misc/test-audio.mp3" ]; then
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
    "$runtime/hgl-dma.raw"

guest_environment="LD_PRELOAD=/tmp/r1-ui-lib/libr1-qemu-fbshim.so"
guest_environment="$guest_environment,R1_QEMU_FB_PATH=/tmp/r1-ui-run/framebuffer.raw"
guest_environment="$guest_environment,R1_QEMU_DMA_PATH=/tmp/r1-ui-run/hgl-dma.raw"
guest_environment="$guest_environment,R1_QEMU_STATE_PATH=/tmp/r1-ui-run/frame-state.bin"
guest_environment="$guest_environment,R1_QEMU_TOUCH_PATH=/tmp/r1-ui-run/touch/event0"
guest_environment="$guest_environment,R1_QEMU_INPUT_LOG_PATH=/tmp/r1-ui-run/input-ioctl.bin"
guest_environment="$guest_environment,R1_QEMU_INPUT_EVENT_LOG_PATH=/tmp/r1-ui-run/input-events.bin"
guest_environment="$guest_environment,R1_QEMU_CRASH_LOG_PATH=/tmp/r1-ui-run/crash-state.bin"

stub_pid=
cleanup_stub() {
    if [ -n "$stub_pid" ]; then
        kill "$stub_pid" 2>/dev/null || true
        wait "$stub_pid" 2>/dev/null || true
    fi
}
trap cleanup_stub EXIT
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

echo "runtime directory: $runtime"
echo "start the viewer in another terminal:"
echo "  python3 tools/r1-qemu-ui/bridge.py serve"
echo "    work/gui-qemu/runtime/framebuffer.raw work/gui-qemu/runtime/touch/event0"
echo "    --state work/gui-qemu/runtime/frame-state.bin"

run_player() {
    env \
        LD_LIBRARY_PATH="$proot_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        PROOT_NO_SECCOMP=1 \
        QEMU_CPU=XBurstR2 \
        QEMU_SET_ENV="$guest_environment" \
        "$proot" -0 -r "$rootfs" \
        -b "$shim_dir:/tmp/r1-ui-lib" \
        -b "$runtime:/tmp/r1-ui-run" \
        -b "$input_nodes:/dev/input" \
        -b "$runtime_run:/run" \
        -b "$runtime_dev/mmcblk0p1:/dev/mmcblk0p1" \
        -b "$user_data:/usr/data" \
        -b /dev/null:/dev/null -b /dev/zero:/dev/zero \
        -b /dev/random:/dev/random -b /dev/urandom:/dev/urandom \
        -b /proc:/proc \
        -w /usr/bin -q "$qemu" /usr/bin/hiby_player
}

if [ "${R1_QEMU_ALLOW_NETWORK:-0}" = 1 ]; then
    run_player
else
    if ! command -v unshare >/dev/null 2>&1; then
        echo "unshare is required for the default network-isolated run" >&2
        exit 1
    fi
    env \
        LD_LIBRARY_PATH="$proot_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        PROOT_NO_SECCOMP=1 \
        QEMU_CPU=XBurstR2 \
        QEMU_SET_ENV="$guest_environment" \
        unshare --net --map-root-user \
        "$proot" -0 -r "$rootfs" \
        -b "$shim_dir:/tmp/r1-ui-lib" \
        -b "$runtime:/tmp/r1-ui-run" \
        -b "$input_nodes:/dev/input" \
        -b "$runtime_run:/run" \
        -b "$runtime_dev/mmcblk0p1:/dev/mmcblk0p1" \
        -b "$user_data:/usr/data" \
        -b /dev/null:/dev/null -b /dev/zero:/dev/zero \
        -b /dev/random:/dev/random -b /dev/urandom:/dev/urandom \
        -b /proc:/proc \
        -w /usr/bin -q "$qemu" /usr/bin/hiby_player
fi
