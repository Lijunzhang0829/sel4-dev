#!/usr/bin/env bash
# tools/scan_chain_topN.sh — sequential global top-N scan over multiple dirs.
# Sister to tools/scan_chain.sh but uses tools/scan_topN_global.py.
#
# scan_topN_global.py looks up each .thy's owning session per inventory
# (reports/inventory/baseline.db) — so a single scan-dir that spans multiple
# sessions (proof/infoflow/ → InfoFlow + InfoFlowC + InfoFlowCBase;
# proof/refine/ARM/ → Refine + RefineOrphanage) is handled correctly: each
# file is built against its own session's heap chain. Output filenames use
# the dir-slug (basename) rather than a single session name.
#
# The optional <fallback-session> column in the queue is passed via --session
# as a backup for files with no inventory entry (rare orphan files).
set -uo pipefail

REPO="/home/lijun/seL4-docker-main"
LOG_DIR="$REPO/logs/scans"
PROG="$LOG_DIR/chain-topN-progress.log"
TOP_N="${TOP_N:-100}"
mkdir -p "$LOG_DIR"

# Queue: "<dir-slug>:<container-scan-dir>:<fallback-session>"
# Note: scan-dir paths use /workspace (host overlay path); the script translates
# heap-fingerprint paths via L4V_DIR env in proof_cost_scan.build_temp_session.
QUEUE=(
  "refine:/workspace/verification/l4v/proof/refine/ARM:Refine"
  "ainvs:/workspace/verification/l4v/proof/invariant-abstract:AInvs"
  "infoflow:/workspace/verification/l4v/proof/infoflow:InfoFlow"
  "crefine:/workspace/verification/l4v/proof/crefine/ARM:CRefine"
)

echo "=== chain-topN start $(date -u '+%Y-%m-%dT%H:%M:%SZ')  TOP_N=$TOP_N ===" | tee -a "$PROG"
for entry in "${QUEUE[@]}"; do
  IFS=":" read -r slug dir fallback <<< "$entry"
  out_json="/workspace/reports/slow-proofs-${slug}-topN.json"
  out_md="/workspace/reports/slow-proofs-${slug}-topN.md"
  log="$LOG_DIR/${slug}-topN.log"
  echo "=== START: $slug  dir=$dir  fallback=$fallback  N=$TOP_N  $(date -u '+%H:%M:%SZ') ===" | tee -a "$PROG"
  t0=$(date +%s)
  docker exec sel4-l4v python3 /workspace/tools/scan_topN_global.py \
    --session "$fallback" --scan-dir "$dir" --top-n "$TOP_N" \
    --out-json "$out_json" --out-md "$out_md" \
    > "$log" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "===SCAN-DONE: $slug rc=$rc wall=$((t1 - t0))s ===" | tee -a "$PROG"
done
echo "=== chain-topN end $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===" | tee -a "$PROG"
