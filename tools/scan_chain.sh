#!/usr/bin/env bash
# tools/scan_chain.sh — Run scan-slow-proofs.sh sequentially over a queue of
# (directory, session) pairs. Sessions cannot run in parallel because each
# polyml worker eats ~9 GB RSS; a single isabelle build at a time is the safe
# bound on this host.
#
# Each scan's full stdout/stderr is teed to logs/scans/<session>.log.
# A summary marker `===SCAN-DONE: <session> wall=<s>===` is appended to
# logs/scans/chain-progress.log between sessions, so you can monitor a single
# file for end-of-each-session events.
#
# Usage: bash tools/scan_chain.sh
set -uo pipefail

REPO="/home/lijun/seL4-docker-main"
SCAN="$REPO/.claude/skills/isabelle_prover/scripts/scan-slow-proofs.sh"
LOG_DIR="$REPO/logs/scans"
PROG="$LOG_DIR/chain-progress.log"
mkdir -p "$LOG_DIR"

# Queue: "<session>:<dir-relative-to-repo>:<output-report>"
QUEUE=(
  "Access:verification/l4v/proof/access-control:reports/slow-proofs-access.md"
  "Refine:verification/l4v/proof/refine/ARM:reports/slow-proofs-refine.md"
  "AInvs:verification/l4v/proof/invariant-abstract:reports/slow-proofs-ainvs.md"
  "InfoFlow:verification/l4v/proof/infoflow:reports/slow-proofs-infoflow.md"
  "CRefine:verification/l4v/proof/crefine/ARM:reports/slow-proofs-crefine.md"
)

echo "=== chain start $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" | tee -a "$PROG"
for entry in "${QUEUE[@]}"; do
  IFS=":" read -r session dir report <<< "$entry"
  echo "=== START: $session  dir=$dir  report=$report  $(date -u '+%H:%M:%SZ') ===" | tee -a "$PROG"
  t0=$(date +%s)
  bash "$SCAN" "$REPO/$dir" "$session" "$REPO/$report" \
    > "$LOG_DIR/${session}.log" 2>&1
  rc=$?
  t1=$(date +%s)
  wall=$((t1 - t0))
  echo "===SCAN-DONE: $session rc=$rc wall=${wall}s ===" | tee -a "$PROG"
done
echo "=== chain end $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" | tee -a "$PROG"
