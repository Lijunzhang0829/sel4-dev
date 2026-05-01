#!/bin/bash
# Sequentially build CRefine, InfoFlowCBase, InfoFlowC.
# Designed to run *inside* the sel4-public container, started via docker exec -d.
#
# Constraints from plan:
#   - Sessions strictly serial (no -j N>1, no overlapping isabelle build invocations)
#   - Each session's heap save (incl. future stage) must finish before next starts.
#     `isabelle build -b` already enforces this — heap is serialized only after all
#     futures are forced.
#   - Bumped ML maxheap to 16G to dodge the OOM (exit=137) the previous attempt hit
#     during CRefine heap save with the default 10G ceiling.
#   - threads=8 within session: 16-core box, leaves headroom; original concurrent
#     attempt used threads=4 per session × 2 parallel sessions.
#
# Outputs (inside container):
#   /sel4-project/build-logs/CRefine_retry.{log,start,end,exit}  — full verbose
#   ditto for InfoFlowCBase, InfoFlowC
#   /sel4-project/build-logs/build_remaining.STATUS              — overall progress
#   /sel4-project/build-logs/build_remaining.DONE                — final sentinel

set -u

L4V_DIR=/sel4-project/verification/l4v
LOG_DIR=/sel4-project/build-logs
STATUS=$LOG_DIR/build_remaining.STATUS
DONE=$LOG_DIR/build_remaining.DONE

mkdir -p "$LOG_DIR"
rm -f "$DONE"

cd "$L4V_DIR"
export L4V_ARCH=ARM
export PATH=/sel4-project/verification/isabelle/bin:$PATH
# Bump ML heap ceiling. Keep the same -H init so warm-up is unchanged.
# 16000 MB cap > previous 10000 — the heap-save peak fits with margin.
export ML_OPTIONS="-H 1000 --maxheap 16000 --stackspace 64"

ts() { date -Iseconds; }

run_session() {
    local sess="$1"
    local logfile="$LOG_DIR/${sess}_retry.log"
    local startfile="$LOG_DIR/${sess}_retry.start"
    local endfile="$LOG_DIR/${sess}_retry.end"
    local exitfile="$LOG_DIR/${sess}_retry.exit"

    echo "$(ts) START $sess" >> "$STATUS"
    date +%s > "$startfile"
    date -Iseconds >> "$startfile"
    free -h >> "$STATUS"
    df -h /root/.isabelle/heaps >> "$STATUS"

    # bash builtin `time` writes to stderr; redirect to .timing file via a
    # subshell that combines time output with the build's stdout/stderr.
    local timefile="$LOG_DIR/${sess}_retry.timing"
    { time isabelle build -b -v -j 1 \
        -o threads=8 \
        -d "$L4V_DIR" \
        "$sess" > "$logfile" 2>&1 ; } 2> "$timefile"
    local rc=$?

    echo "$rc" > "$exitfile"
    date +%s > "$endfile"
    date -Iseconds >> "$endfile"
    echo "$(ts) END   $sess rc=$rc" >> "$STATUS"
    free -h >> "$STATUS"
    df -h /root/.isabelle/heaps >> "$STATUS"

    if [ "$rc" -ne 0 ]; then
        echo "$(ts) FAILED at $sess (rc=$rc) — stopping chain" >> "$STATUS"
        return $rc
    fi

    # confirm heap landed
    if [ ! -f "/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/$sess" ]; then
        echo "$(ts) ERROR: $sess rc=0 but heap file missing" >> "$STATUS"
        return 99
    fi
    local sz
    sz=$(stat -c%s "/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/$sess")
    echo "$(ts) heap saved: $sess size=$sz bytes" >> "$STATUS"
    return 0
}

echo "=== build_remaining started $(ts) ===" > "$STATUS"
echo "ML_OPTIONS=$ML_OPTIONS" >> "$STATUS"
echo "PATH=$PATH" >> "$STATUS"
echo "" >> "$STATUS"

run_session CRefine        || { echo FAIL_CRefine        > "$DONE"; exit 1; }
run_session InfoFlowCBase  || { echo FAIL_InfoFlowCBase  > "$DONE"; exit 1; }
run_session InfoFlowC      || { echo FAIL_InfoFlowC      > "$DONE"; exit 1; }

echo "OK" > "$DONE"
echo "$(ts) ALL DONE" >> "$STATUS"
