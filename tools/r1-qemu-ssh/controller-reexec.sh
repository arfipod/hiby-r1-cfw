#!/tmp/r1-host-qemu /bin/sh

# QEMU's binfmt handler covers target ELF applets, but Linux does not recurse
# through a target-MIPS shebang when r1-ssh-control starts its monitor via $0.
# The validator binds this physical-only adapter at /tmp/r1-controller-reexec
# and gives it to the exact candidate script as $0.  Only that self-reexec is
# adapted; the controller body and every BusyBox applet still run as MIPS code.

printf 'controller self-reexec:' >> /run/r1-controller-reexec.log
printf ' %s' "$@" >> /run/r1-controller-reexec.log
printf '\n' >> /run/r1-controller-reexec.log
exec /tmp/r1-host-qemu /bin/sh /usr/bin/r1-ssh-control "$@" \
    >> /run/r1-controller-reexec.log 2>&1
