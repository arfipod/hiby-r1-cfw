# R1 file-control helper

`r1-filectl` is the constrained filesystem side of the Android **R1 Manager**
application. It is executed through an authenticated Dropbear SSH session and
opens no additional port or resident service.

The helper treats `/usr/data/mnt/sd_0` as an immutable security root. Paths are
unpadded URL-safe Base64, must be relative, and cannot enter the reserved
`.r1-manager` tree except through dedicated upload and Trash commands. Filesystem
traversal uses `openat(2)`, `O_NOFOLLOW`, and `AT_SYMLINK_NOFOLLOW`; Android never
constructs `rm`, `mv`, or `mkdir` shell commands from remote names.

Supported operations include health and capacity discovery, deterministic
JSONL listings, status, folder creation, rename, move with conflict policies,
Trash/restore/permanent delete, and staged SCP uploads with exact-size checks
and atomic final rename.

Build it with:

```sh
tools/build-r1-filectl.sh work/r1-filectl-build
```

The binary targets MIPS32r2 little-endian o32 hard-float with glibc 2.22 and is
installed as `/usr/bin/r1-filectl` by the firmware pipeline.
