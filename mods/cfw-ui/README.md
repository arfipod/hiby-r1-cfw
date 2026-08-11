# HiBy R1 CFW UI overlay

This component combines a fail-open launcher selector, one guarded runtime
route, and an independent MIPS sidecar. The selector runs after `/usr/data` is
mounted and before `hiby_player`, then bind-mounts one deterministic launcher
variant over each stock theme path.

Persistent state is a single recoverable file:

```text
/usr/data/r1-cfw/launcher.conf
launcher_mask=71
```

Bit 5 is the CFW tile and is mandatory, and every generated configuration has
at least four visible tiles. Missing, malformed, or unsupported state falls
back to `0x71` (stored as `71`: Music, System, CFW, About). The all-enabled
`0x7f` configuration restores Stream, Wireless, and eBook and uses a vertically
scrollable 984-pixel content surface. A selector failure leaves the stock
six-tile launcher available.

`libr1-cfw-hook.so` is loaded only for `hiby_player`. It verifies the exact
stock Step descriptor, callback pointer, and no-op instructions before changing
that one writable callback word in memory. On a CFW tap it transfers the
already-grabbed touchscreen, snapshots both framebuffer pages, and runs
`/usr/bin/r1-cfw-ui` synchronously. Back, route exit, exec failure, and child
signals all restore the original pages, active page, and touch ownership. If
the library or any runtime guard fails, the player continues with the dormant
stock Step action.

The sidecar owns CFW rendering and data collection. It delegates SSH changes to
`/usr/bin/r1-ssh-control`, reads storage with `statvfs`, reads memory and uptime
from `/proc`, and saves launcher state atomically. Wi-Fi and Bluetooth exit with
distinct route codes; the parent then opens the stock Wireless hub, preserving
the vendor radio configuration UI and backend.

The SSH row changes the controller's persistent switch; it does not start an
independent daemon. An actual listener still requires Developer Mode and a
`wlan0` IPv4 address, unless the deliberately named microSD recovery override
is active. Dropbear remains Wi-Fi-only on TCP port 2222.

Build the two target artifacts with:

```bash
tools/build-r1-cfw-ui.sh work/r1-cfw-ui-build
```

They target MIPS32r2 little-endian o32 hard-float and glibc 2.22. They are
installed by `tools/build-cfw-0.1.sh`; they are not committed to Git.

The firmware build has separate `prepare` and `publish` stages. `prepare`
creates and independently verifies a candidate under `work/`. QEMU validation
extracts and runs the hashed candidate SquashFS itself, then rehashes it before
PASS. `publish` verifies the exact 23-check manifest and its featured PNG
evidence before it atomically writes `dist/r1-cfw-0.1-experimental.upt`.
Neither stage flashes a player, writes MTD, or modifies the stock input in place.
