#!/usr/bin/env bash
# tools/scan_resumable.sh — server-crash-tolerant per-file scan driver.
#
# Standalone wrapper around tools/proof-timing.sh that:
#   1. Walks every .thy file under <scan-dir> (architecture-filtered).
#   2. For each file, checks whether the report already has a `## <basename>`
#      section. If yes, SKIP (allows resume after server crash).
#   3. Otherwise calls proof-timing.sh INSIDE the container, parses output,
#      appends a new section to the report. Each file is committed to disk
#      before moving to the next, so a crash mid-scan keeps every previously
#      finished file's data intact.
#
# Usage:
#   bash tools/scan_resumable.sh <container-scan-dir> <fallback-session> <host-report-path>
#
# Example:
#   bash tools/scan_resumable.sh \
#       /workspace/verification/l4v/proof/invariant-abstract \
#       AInvs \
#       /home/lijun/seL4-docker-main/reports/slow-proofs-ainvs.md
set -uo pipefail

DIR="$1"
FALLBACK_SESSION="$2"
HOST_REPORT="$3"

# Container-side report path (host's reports/ is bind-mounted at /workspace/reports).
REPORT="/workspace${HOST_REPORT#/home/lijun/seL4-docker-main}"

mkdir -p "$(dirname "$HOST_REPORT")"
docker exec sel4-l4v rm -f /tmp/isabelle-session-*.lock 2>/dev/null

# Initialize the report if it doesn't exist.
if [ ! -f "$HOST_REPORT" ]; then
  cat > "$HOST_REPORT" <<EOF
# Slow Proofs Report

- **Directory**: $DIR
- **Date**: $(date -u '+%Y-%m-%d %H:%M UTC')

EOF
fi

# Inventory-aware session lookup is built into tools/proof-timing.sh? No — it's
# in tools/scan-slow-proofs.sh's _session_for. Replicate here for completeness.
_session_for() {
  local thy="$1"
  local rel="${thy#/workspace/verification/l4v/}"
  python3 - "$rel" "$FALLBACK_SESSION" <<PY 2>/dev/null || echo "$FALLBACK_SESSION"
import sqlite3, sys
rel, fallback = sys.argv[1], sys.argv[2]
c = sqlite3.connect("/home/lijun/seL4-docker-main/reports/inventory/baseline.db").cursor()
r = c.execute("SELECT session FROM theories WHERE path=?", (rel,)).fetchone()
print(r[0] if r and r[0] else fallback)
PY
}

# Enumerate .thy files (largest first).
L4V_ARCH="${L4V_ARCH:-ARM}"
EXCLUDE_FIND=""
for arch in AARCH64 RISCV64 ARM_HYP X64; do
  if [ "$arch" != "$L4V_ARCH" ]; then
    EXCLUDE_FIND="$EXCLUDE_FIND -not -path '*/$arch/*'"
  fi
done

# [BUG fix 2026-05-07] enumerate .thy files on the host (same bind-mounted
# tree as container) instead of doing it inside the container — earlier
# attempt with `docker exec bash -c "find ... -printf '%s %p\n' ..."` was
# broken because the literal `\n` in -printf got eaten by the outer double-
# quoted `bash -c "..."` (bash interpreted `\n` as escape-the-n). find then
# saw `%pn` and errored "paths must precede expression: `%pn`", FILES was
# empty, and the script "completed" in 2 seconds without measuring anything.
# Doing find on the host avoids the quoting problem entirely; we just rewrite
# the host paths back to /workspace/... that proof-timing.sh expects.
HOST_DIR="${DIR/#\/workspace/\/home\/lijun\/seL4-docker-main}"
EXCLUDE_FIND_ARRAY=()
for arch in AARCH64 RISCV64 ARM_HYP X64; do
  if [ "$arch" != "$L4V_ARCH" ]; then
    EXCLUDE_FIND_ARRAY+=(-not -path "*/$arch/*")
  fi
done
FILES=$(find "$HOST_DIR" -name '*.thy' -not -name 'Tmp_*' "${EXCLUDE_FIND_ARRAY[@]}" -printf '%s %p\n' \
        | sort -rn | awk '!seen[$2]++ {print $2}' \
        | sed "s|$HOST_DIR|$DIR|")
TOTAL=$(echo "$FILES" | wc -l)

echo "Scanning $TOTAL .thy files in $DIR (fallback session=$FALLBACK_SESSION)..."
echo "Report: $HOST_REPORT (resumable — skips files already present)"
echo

IDX=0
DONE_BEFORE=$(grep -cE '^## ' "$HOST_REPORT" 2>/dev/null || echo 0)
NEW_DONE=0

for f in $FILES; do
  IDX=$((IDX + 1))
  base=$(basename "$f" .thy)
  # Skip if already in report
  if grep -qE "^## $base\$" "$HOST_REPORT" 2>/dev/null; then
    printf "[%3d/%d] %-50s skip (already in report)\n" "$IDX" "$TOTAL" "$base"
    continue
  fi

  session="$(_session_for "$f")"
  printf "[%3d/%d] %-50s session=%-15s " "$IDX" "$TOTAL" "$base" "$session"

  OUTPUT=$(docker exec sel4-l4v bash /workspace/tools/proof-timing.sh "$f" "$session" 2>&1) || true
  BASELINE=$(echo "$OUTPUT" | grep "Baseline:" | head -1 | grep -oP '\d+(?=ms)' || echo "0")

  if [ "$BASELINE" -lt 5000 ]; then
    echo "skip (${BASELINE}ms baseline < 5s)"
    # Mark as scanned in the report so resume doesn't re-try
    {
      echo "## $base"
      echo
      echo "- **File**: $f"
      echo "- **Baseline**: ${BASELINE}ms (skipped: < 5s)"
      echo
    } >> "$HOST_REPORT"
    continue
  fi

  SLOW=$(echo "$OUTPUT" | grep -A100 "Slow proofs" | grep "^    " | head -10)

  echo "${BASELINE}ms"
  {
    echo "## $base"
    echo
    echo "- **File**: $f"
    echo "- **Session**: $session"
    echo "- **Baseline**: ${BASELINE}ms"
    echo
    echo '```'
    echo "$OUTPUT" | grep -A100 "Sorry-substitution" | head -20
    echo '```'
    echo
  } >> "$HOST_REPORT"
  NEW_DONE=$((NEW_DONE + 1))
done

echo
echo "── Done ──"
echo "Total files: $TOTAL"
echo "Already-done before this run: $DONE_BEFORE"
echo "Newly scanned this run: $NEW_DONE"
echo "Report: $HOST_REPORT"
