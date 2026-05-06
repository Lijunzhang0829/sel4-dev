#!/usr/bin/env bash
# tools/rescan_infoflow_5files.sh — targeted re-scan of the 5 InfoFlow build
# files that ERR'd in the original topN scan due to Bug 4 / Bug 5 in
# proof_cost_scan.py.
#
# Each file gets measured with --top 5 (5 largest lemmas per file). Cost: 6
# builds per file × 5 files = ~30 builds × ~5-10 min/build = ~3-5 h wall.
# Output JSONs go to /workspace/reports/slow-proofs-infoflow-fix5/<file>.json
# Then a follow-up Python pass merges them into the existing InfoFlow topN
# report.
set -uo pipefail
mkdir -p reports/slow-proofs-infoflow-fix5 logs/scans/infoflow-fix5
docker exec sel4-l4v mkdir -p /workspace/reports/slow-proofs-infoflow-fix5

FILES=(
  # path-relative-to-l4v, session
  "proof/infoflow/FinalCaps.thy InfoFlow"
  "proof/infoflow/Scheduler_IF.thy InfoFlow"
  "proof/infoflow/ARM/Example_Valid_State.thy InfoFlow"
  "proof/infoflow/Syscall_IF.thy InfoFlow"
  "proof/infoflow/PasUpdates.thy InfoFlow"
)

for entry in "${FILES[@]}"; do
  read -r relpath session <<< "$entry"
  base=$(basename "$relpath" .thy)
  echo "===== $base ($session) — $(date -u '+%H:%M:%SZ') ====="
  docker exec sel4-l4v rm -f /tmp/isabelle-session-*.lock 2>/dev/null
  docker exec sel4-l4v python3 /workspace/tools/proof_cost_scan.py \
    "/workspace/verification/l4v/$relpath" "$session" --top 5 \
    --json-out "/workspace/reports/slow-proofs-infoflow-fix5/${base}.json" \
    > "logs/scans/infoflow-fix5/${base}.log" 2>&1
  rc=$?
  echo "===== $base done rc=$rc — $(date -u '+%H:%M:%SZ') ====="
done
echo "===== ALL 5 FILES DONE — $(date -u '+%H:%M:%SZ') ====="
