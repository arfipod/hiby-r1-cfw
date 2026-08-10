#!/bin/sh
set -eu

# Download and verify a self-contained host toolchain under work/. Nothing is
# installed system-wide. Debian packages are extracted, not installed.

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work_dir=$repo_dir/work
host_dir=$work_dir/host-tools
source_dir=$work_dir/ssh-build/src
download_dir=${TMPDIR:-/tmp}/hiby-r1-cfw-downloads

dropbear_version=2026.94
dropbear_sha256=e098034a843699200c8c977a991fff73159735bf795d5f72ef672c41a6b1ae81
dropbear_sig_sha256=e5971e62ee25f3cce66a09bf5ab2411e76c258b960be832159473f3395116695
dropbear_fingerprint='F7347EF2EE2E07A267628CA944931494F29C6773'
dropbear_base=https://matt.ucc.asn.au/dropbear/releases

zig_version=0.16.0
zig_sha256=70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00
zig_archive=zig-x86_64-linux-$zig_version.tar.xz
zig_url=https://ziglang.org/download/$zig_version/$zig_archive

mkdir -p "$download_dir" "$host_dir" "$source_dir"

fetch() {
    url=$1
    destination=$2
    if [ ! -f "$destination" ]; then
        curl -fL --retry 3 -o "$destination" "$url"
    fi
}

verify_sha256() {
    expected=$1
    file=$2
    actual=$(sha256sum "$file" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        echo "SHA-256 mismatch for $file" >&2
        echo "expected: $expected" >&2
        echo "actual:   $actual" >&2
        exit 1
    fi
}

dropbear_archive=dropbear-$dropbear_version.tar.bz2
dropbear_tar=$download_dir/$dropbear_archive
dropbear_sig=$dropbear_tar.asc
dropbear_key=$download_dir/dropbear-key-2015.asc
fetch "$dropbear_base/$dropbear_archive" "$dropbear_tar"
fetch "$dropbear_base/$dropbear_archive.asc" "$dropbear_sig"
fetch "$dropbear_base/dropbear-key-2015.asc" "$dropbear_key"
verify_sha256 "$dropbear_sha256" "$dropbear_tar"
verify_sha256 "$dropbear_sig_sha256" "$dropbear_sig"

if command -v gpg >/dev/null 2>&1; then
    gnupg_dir=$work_dir/ssh-build/gnupg
    mkdir -p "$gnupg_dir"
    chmod 0700 "$gnupg_dir"
    GNUPGHOME=$gnupg_dir gpg --batch --quiet --import "$dropbear_key"
    imported_fingerprint=$(
        GNUPGHOME=$gnupg_dir gpg --batch --with-colons --fingerprint |
            awk -F: '$1 == "fpr" { print $10; exit }'
    )
    if [ "$imported_fingerprint" != "$dropbear_fingerprint" ]; then
        echo "unexpected Dropbear signing-key fingerprint" >&2
        exit 1
    fi
    GNUPGHOME=$gnupg_dir gpg --batch --verify "$dropbear_sig" "$dropbear_tar"
fi

if [ ! -x "$source_dir/dropbear-$dropbear_version/configure" ]; then
    tar -xjf "$dropbear_tar" -C "$source_dir"
fi

zig_tar=$download_dir/$zig_archive
fetch "$zig_url" "$zig_tar"
verify_sha256 "$zig_sha256" "$zig_tar"
if [ ! -x "$host_dir/zig-x86_64-linux-$zig_version/zig" ]; then
    tar -xJf "$zig_tar" -C "$host_dir"
fi

extract_debian_tool() {
    package=$1
    destination=$2
    marker=$3
    if [ -e "$destination/$marker" ]; then
        return
    fi
    package_dir=$(mktemp -d)
    (
        cd "$package_dir"
        apt download "$package"
        deb=$(find . -maxdepth 1 -name '*.deb' -print -quit)
        test -n "$deb"
        mkdir -p "$destination"
        dpkg-deb -x "$deb" "$destination"
    )
    rm -rf "$package_dir"
}

extract_debian_tool binutils-mipsel-linux-gnu \
    "$host_dir/binutils-mipsel" usr/bin/mipsel-linux-gnu-strip
extract_debian_tool qemu-user \
    "$host_dir/qemu-user" usr/bin/qemu-mipsel
extract_debian_tool proot \
    "$host_dir/proot" usr/bin/proot
extract_debian_tool libtalloc2 \
    "$host_dir/proot" usr/lib/x86_64-linux-gnu/libtalloc.so.2

echo "SSH toolchain is ready under $work_dir"
