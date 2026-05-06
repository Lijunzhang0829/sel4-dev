#!/usr/bin/env bash
# tools/rescan_ainvs_3files.sh — targeted re-scan of the 3 AInvs files that
# baseline_err'd in the original 2026-05-04 topN scan. The fixes for Bug 4/5/6
# were committed 2026-05-05/06, after the AInvs scan; rerunning these three
# files with the fixed proof_cost_scan.py recovers their cost data.
#
# Mirror of tools/rescan_infoflow_5files.sh.
set -uo pipefail
mkdir -p reports/slow-proofs-ainvs-fix3 logs/scans/ainvs-fix3
docker exec sel4-l4v mkdir -p /workspace/reports/slow-proofs-ainvs-fix3

FILES=(
  "proof/invariant-abstract/Deterministic_AI.thy AInvs"
  "proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy AInvs"
  "proof/invariant-abstract/KernelInitSepProofs_AI.thy AInvs"
)

for entry in "${FILES[@]}"; do
  read -r relpath session <<< "$entry"
  base=$(basename "$relpath" .thy)
  echo "===== $base ($session) — $(date -u '+%H:%M:%SZ') ====="
  docker exec sel4-l4v rm -f /tmp/isabelle-session-*.lock 2>/dev/null
  docker exec sel4-l4v python3 /workspace/tools/proof_cost_scan.py \
    "/workspace/verification/l4v/$relpath" "$session" --top 5 \
    --json-out "/workspace/reports/slow-proofs-ainvs-fix3/${base}.json" \
    > "logs/scans/ainvs-fix3/${base}.log" 2>&1
  rc=$?
  echo "===== $base done rc=$rc — $(date -u '+%H:%M:%SZ') ====="
done
echo "===== ALL 3 FILES DONE — $(date -u '+%H:%M:%SZ') ====="
