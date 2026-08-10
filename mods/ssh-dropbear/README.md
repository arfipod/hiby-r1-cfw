# Dropbear SSH modification

This overlay adds a small Dropbear server to the HiBy R1 firmware 1.6 rootfs.
It deliberately reuses the stock **Developer Mode** state instead of patching
the proprietary settings UI:

- Developer Mode enabled (`/usr/data/disableadb` absent): SSH follows the Wi-Fi
  IPv4 address and listens on TCP port 2222.
- Developer Mode disabled (`/usr/data/disableadb` present), Wi-Fi down, or the
  address changes: the server is stopped or rebound.

The lab image sets the root password to `hibyr1`. This is intentionally easy to
type and therefore unsafe on an untrusted network. Public-key authentication is
preferred: place an OpenSSH public key in
`/usr/data/dropbear/authorized_keys`, mode 0600. The persistent ED25519 host key
and logs are also stored in `/usr/data/dropbear/`.

The stock `/usr` directory is group-writable, so Dropbear correctly rejects it
as a direct `-D` authorized-keys parent. The immutable rootfs instead contains
`/root/.ssh/authorized_keys` as a symlink to the persistent file. Dropbear checks
the root-owned `/root/.ssh` path and the target file's 0600 permissions.

Dropbear is compiled without SSH forwarding, agent forwarding, X11 forwarding,
DSS, RSA/SHA-1, 3DES, or CBC modes. It binds only to the current `wlan0` IPv4
address, not to every device interface.
