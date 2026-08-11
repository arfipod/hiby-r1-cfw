# Dropbear SSH modification

This overlay adds a small Dropbear server to the HiBy R1 firmware 1.6 rootfs.
The CFW build also adds a separate **SSH server** switch as the third row of
Developer Options. SSH listens only when all of these conditions are true:

- the SSH marker `/usr/data/dropbear/enabled` exists;
- stock Developer Mode is enabled (`/usr/data/disableadb` is absent); and
- `wlan0` owns an IPv4 address.

The five-second monitor stops or rebinds Dropbear when any of those conditions
changes. A fresh installation is off by default. An upgrade from the earlier
SSH lab image recognizes its existing host key as an enabled legacy state and
creates the new marker at boot, avoiding an unexpected recovery lockout.

Creating an empty `CFW_SSH_ENABLE` file in the root of an inserted microSD is a
deliberate emergency override: it enables Wi-Fi SSH even if either UI gate is
off. Remove the file after recovery. The UI switch continues to show the
persistent marker state, not this temporary override.

The lab image sets the root password to `hibyr1`. This is intentionally easy to
type and therefore unsafe on an untrusted network. Public-key authentication is
preferred: place an OpenSSH public key in
`/usr/data/dropbear/authorized_keys`, mode 0600. The persistent ED25519 host key,
switch markers, and logs are also stored in `/usr/data/dropbear/`.

The stock `/usr` directory is group-writable, so Dropbear correctly rejects it
as a direct `-D` authorized-keys parent. The immutable rootfs instead contains
`/root/.ssh/authorized_keys` as a symlink to the persistent file. Dropbear checks
the root-owned `/root/.ssh` path and the target file's 0600 permissions.

Dropbear is compiled without SSH forwarding, agent forwarding, X11 forwarding,
DSS, RSA/SHA-1, 3DES, or CBC modes. It binds only to the current `wlan0` IPv4
address, not to every device interface.
