#!/bin/sh
set -eu

# QEMU user mode validates the CPU ABI and userland interactions. It does not
# emulate the X1600 board, its NAND, display, audio, Wi-Fi, PMIC, or bootloader.

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
rootfs=${R1_QEMU_ROOTFS:-$repo_dir/work/r1-1.6/rootfs-full}
dropbear=${R1_DROPBEAR_BINARY:-$repo_dir/work/ssh-build/output/dropbearmulti}
qemu=$repo_dir/work/host-tools/qemu-user/usr/bin/qemu-mipsel
proot=$repo_dir/work/host-tools/proot/usr/bin/proot
proot_lib=$repo_dir/work/host-tools/proot/usr/lib/x86_64-linux-gnu
test_dir=$repo_dir/work/ssh-build/qemu-test
test_root=$test_dir/root
port=${R1_QEMU_SSH_PORT:-$((22000 + ($$ % 1000)))}

for required in "$rootfs/lib/ld.so.1" "$dropbear" "$qemu" "$proot"; do
    if [ ! -e "$required" ]; then
        echo "missing test dependency: $required" >&2
        exit 1
    fi
done
if ! command -v socat >/dev/null 2>&1; then
    echo "socat is required for the QEMU inetd-mode SSH test" >&2
    exit 1
fi

rm -rf "$test_dir"
mkdir -p "$test_root" "$test_dir/keys"
cp -a "$rootfs/." "$test_root/"
install -m 0755 "$dropbear" "$test_root/usr/sbin/dropbearmulti"
ln -sf dropbearmulti "$test_root/usr/sbin/dropbear"
ln -sf ../sbin/dropbearmulti "$test_root/usr/bin/dropbearkey"
mkdir -p "$test_root/usr/data/dropbear" "$test_root/root/.ssh" "$test_root/run"
chmod 0700 "$test_root/usr/data/dropbear" "$test_root/root/.ssh"
cp "$repo_dir/mods/ssh-dropbear/rootfs-overlay/etc/shadow" "$test_root/etc/shadow"
chmod 0600 "$test_root/etc/shadow"

echo "[1/5] ELF loader and version"
"$qemu" -L "$rootfs" "$dropbear" dropbear -V

echo "[2/5] Guest ED25519 host-key generation"
"$qemu" -L "$test_root" "$dropbear" dropbearkey -t ed25519 \
    -f "$test_root/usr/data/dropbear/dropbear_ed25519_host_key"
chmod 0600 "$test_root/usr/data/dropbear/dropbear_ed25519_host_key"

echo "[3/5] Public-key SSH session through PRoot + QEMU"
ssh-keygen -q -t ed25519 -N '' -f "$test_dir/keys/client"
cp "$test_dir/keys/client.pub" "$test_root/usr/data/dropbear/authorized_keys"
chmod 0600 "$test_root/usr/data/dropbear/authorized_keys"
ln -sf /usr/data/dropbear/authorized_keys \
    "$test_root/root/.ssh/authorized_keys"

# A normal Dropbear listener forks per client. PRoot's QEMU handler cannot
# re-open its internal /proc/self/fd descriptor after that fork. Inetd mode
# avoids the emulator limitation while exercising the same protocol, auth,
# privilege setup, and guest shell code paths.
guest_command="$proot -0 -r $test_root -q $qemu -b /dev -b /proc -w /root /usr/sbin/dropbear -i -m -r /usr/data/dropbear/dropbear_ed25519_host_key"
LD_LIBRARY_PATH="$proot_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
PROOT_NO_SECCOMP=1 socat \
    "TCP-LISTEN:$port,bind=127.0.0.1,reuseaddr,fork" \
    "EXEC:$guest_command" > "$test_dir/server.log" 2>&1 &
server_pid=$!
cleanup() {
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
attempt=0
while [ "$attempt" -lt 20 ]; do
    if ssh -F /dev/null -p "$port" -i "$test_dir/keys/client" \
        -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=1 \
        root@127.0.0.1 'printf "qemu-ssh-ok\\n"; id' \
        > "$test_dir/client.log" 2>&1; then
        ready=1
        break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
        break
    fi
    sleep 1
    attempt=$((attempt + 1))
done

if [ "$ready" -ne 1 ]; then
    echo "QEMU SSH session failed" >&2
    sed -n '1,160p' "$test_dir/server.log" >&2
    sed -n '1,160p' "$test_dir/client.log" >&2
    exit 1
fi
cat "$test_dir/client.log"

echo "[4/5] Password SSH session through PRoot + QEMU"
if ! DISPLAY=:0 SSH_ASKPASS_REQUIRE=force \
    SSH_ASKPASS="$repo_dir/tests/fixtures/ssh-askpass.sh" \
    setsid -w ssh -F /dev/null -p "$port" \
        -o PreferredAuthentications=password -o PubkeyAuthentication=no \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 root@127.0.0.1 \
        'printf "qemu-password-ok\\n"; id' \
        > "$test_dir/password-client.log" 2>&1; then
    echo "QEMU password SSH session failed" >&2
    sed -n '1,200p' "$test_dir/server.log" >&2
    sed -n '1,120p' "$test_dir/password-client.log" >&2
    exit 1
fi
cat "$test_dir/password-client.log"

echo "[5/5] Reproducible password-hash check"
hash='$6$r1cfw160$.kVv85eqMXhzIop5ewK8hSitkkqGNqyl.Wz4BR6l0i.U5td/AkQ39rZ6FXzsYGyDMCIVpGrI1K4ArjRmUZJKt1'
actual=$(openssl passwd -6 -salt r1cfw160 hibyr1)
test "$actual" = "$hash"
echo "password hash matches the intended lab password"
