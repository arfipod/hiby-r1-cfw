#!/bin/sh

killall hiby_player >/dev/null 2>&1
killall -9 hiby_player >/dev/null 2>&1

if [ -f "/usr/bin/batd" ]; then
    killall batd >/dev/null 2>&1
    killall -9 batd >/dev/null 2>&1
    /usr/bin/batd -v -s -t5 -o /mnt/sd_0/batlog.txt &
fi

trigger="/data/mnt/sd_0/CFW_LOG"
log_dir="/data/mnt/sd_0/cfw-logs"
log_file="$log_dir/hiby_player.log"

# hiby_player asks sys_server to mount the SD card. Therefore the trigger cannot
# be checked before starting the player. Keep consuming its output and start
# persisting it only after the mount and trigger become visible.
log_sink() {
    enabled=0
    while IFS= read -r line; do
        if [ -f "$trigger" ]; then
            if [ "$enabled" -eq 0 ]; then
                mkdir -p "$log_dir"
                if [ -f "$log_file" ]; then
                    log_size=$(wc -c < "$log_file")
                    if [ "$log_size" -gt 4194304 ]; then
                        tail -c 1048576 "$log_file" > "$log_file.tmp"
                        mv "$log_file.tmp" "$log_file"
                    fi
                fi
                echo "===== logging enabled: $(date) =====" >> "$log_file"
                enabled=1
            fi
            printf '%s\n' "$line" >> "$log_file"
        else
            enabled=0
        fi
    done
}

# Save one snapshot automatically when a card carrying the trigger is mounted.
# Stop looking after two minutes so this helper is not left running indefinitely.
snapshot_when_requested() {
    waited=0
    while [ "$waited" -lt 120 ]; do
        if [ -f "$trigger" ]; then
            /usr/bin/cfw-info.sh >/dev/console 2>&1
            return
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

snapshot_when_requested &
snapshot_pid=$!
/usr/bin/hiby_player 2>&1 | log_sink
kill "$snapshot_pid" >/dev/null 2>&1

sleep 1
reboot
