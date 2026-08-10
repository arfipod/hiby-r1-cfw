# Diagnostic logging overlay

This is a deliberately small first CFW modification. It does not enable logs
unless the SD card root contains a file named `CFW_LOG`.

The stock player itself causes `sys_server` to mount the SD card, so the launcher
starts it normally and a pipe discards output until the trigger becomes visible.
When enabled, subsequent `hiby_player` stdout/stderr is written to
`/data/mnt/sd_0/cfw-logs/hiby_player.log`. The log is trimmed to the newest
1 MiB when it grows past 4 MiB, limiting SD-card use. Removing `CFW_LOG` returns
to stock launch behavior after the next reboot.

The launcher automatically invokes `/usr/bin/cfw-info.sh` once after seeing the
trigger. It can also be run over ADB or UART to save another one-shot system
snapshot to the same directory.

Apply this directory's `rootfs-overlay` to an extracted rootfs before running
`build-rootfs`.
