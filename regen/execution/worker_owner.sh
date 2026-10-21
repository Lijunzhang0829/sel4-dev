#!/bin/sh
# PID 1 for an owned resident checker. Exiting stops the entire container,
# including a proof or heap loader that does not respond to ordinary signals.
lease_seconds=$1
heartbeat=$2
shift 2
supervisor_pid=$$
worker_pid=
watchdog_pid=

finish() {
    trap '' TERM HUP INT USR1
    if [ -n "$watchdog_pid" ]; then kill "$watchdog_pid" 2>/dev/null || :; fi
    if [ -n "$worker_pid" ]; then kill "$worker_pid" 2>/dev/null || :; fi
    exit "$1"
}
trap 'finish 143' TERM HUP INT
trap 'finish 125' USR1

"$@" &
worker_pid=$!
(
    last=$(stat -c %y "$heartbeat") || last=
    unchanged=0
    while kill -0 "$worker_pid" 2>/dev/null; do
        sleep 1
        current=$(stat -c %y "$heartbeat") || break
        if [ "$current" = "$last" ]; then
            unchanged=$((unchanged + 1))
        else
            last=$current
            unchanged=0
        fi
        if [ "$unchanged" -ge "$lease_seconds" ]; then break; fi
    done
    if kill -0 "$worker_pid" 2>/dev/null; then
        echo 'REGEN_WORKER_OWNER_LEASE_EXPIRED' >&2
        kill -USR1 "$supervisor_pid"
    fi
) &
watchdog_pid=$!
wait "$worker_pid"
worker_status=$?
finish "$worker_status"
