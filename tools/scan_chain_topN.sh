#!/usr/bin/env bash
# tools/scan_chain_topN.sh — sequential global top-N scan over multiple sessions.
# Sister to tools/scan_chain.sh but uses tools/scan_topN_global.py instead of
# the skill's per-file scan-slow-proofs.sh.
set -uo pipefail

REPO="/home/lijun/seL4-docker-main"
LOG_DIR="$REPO/logs/scans"
PROG="$LOG_DIR/chain-topN-progress.log"
TOP_N="${TOP_N:-100}"
mkdir -p "$LOG_DIR"

# Queue: "<session>:<container-scan-dir>"
# Note: scan-dir paths use /workspace (host overlay path); the script translates
# heap-fingerprint paths via L4V_DIR env in proof_cost_scan.build_temp_session.
QUEUE=(
  "Refine:/workspace/verification/l4v/proof/refine/ARM"
  "AInvs:/workspace/verification/l4v/proof/invariant-abstract"
  "InfoFlow:/workspace/verification/l4v/proof/infoflow"
  "CRefine:/workspace/verification/l4v/proof/crefine/ARM"
)

echo "=== chain-topN start $(date -u '+%Y-%m-%dT%H:%M:%SZ')  TOP_N=$TOP_N ===" | tee -a "$PROG"
for entry in "${QUEUE[@]}"; do
  IFS=":" read -r session dir <<< "$entry"
  slug="$(echo "$session" | tr '[:upper:]' '[:lower:]')"
  out_json="/workspace/reports/slow-proofs-${slug}-topN.json"
  out_md="/workspace/reports/slow-proofs-${slug}-topN.md"
  log="$LOG_DIR/${session}-topN.log"
  echo "=== START: $session  dir=$dir  N=$TOP_N  $(date -u '+%H:%M:%SZ') ===" | tee -a "$PROG"
  t0=$(date +%s)
  docker exec sel4-l4v python3 /workspace/tools/scan_topN_global.py \
    --session "$session" --scan-dir "$dir" --top-n "$TOP_N" \
    --out-json "$out_json" --out-md "$out_md" \
    > "$log" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "===SCAN-DONE: $session rc=$rc wall=$((t1 - t0))s ===" | tee -a "$PROG"
done
echo "=== chain-topN end $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" | tee -a "$PROG"
