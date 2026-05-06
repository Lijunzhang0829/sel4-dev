#!/bin/bash
# scan-slow-proofs.sh — Scan all .thy files in a directory for slow proofs
#
# Usage: ./scan-slow-proofs.sh [directory] [session]
#
# Runs proof-timing.sh on each .thy file. Collects results into a summary report.
# Only files with baseline >5s are measured (skip trivial files).

set -euo pipefail

# Defensive: if any host-side paths were passed in, rewrite to /workspace.
_translate_host_path() {
  local p="$1"
  local host_root="${HOST_REPO_ROOT:-/home/lijun/seL4-docker-main}"
  case "$p" in
    "$host_root"|"$host_root"/*) echo "/workspace${p#$host_root}" ;;
    *) echo "$p" ;;
  esac
}
[ -n "${1:-}" ] && set -- "$(_translate_host_path "$1")" "${@:2}"
[ -n "${3:-}" ] && set -- "$1" "${2:-AInvs}" "$(_translate_host_path "$3")" "${@:4}"

DIR="${1:-l4v/proof/invariant-abstract}"
SESSION="${2:-AInvs}"
# Use per-folder report file under reports/, or custom path via $3
DIR_SLUG="$(basename "$DIR")"
REPORT="${3:-$(pwd)/reports/slow-proofs-${DIR_SLUG}.md}"
mkdir -p "$(dirname "$REPORT")"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# [BUG 3 — fixed 2026-05-05] Some scan-dirs span multiple sessions, e.g.
# `proof/infoflow/` contains InfoFlow + InfoFlowC + InfoFlowCBase content;
# `proof/refine/ARM/` contains Refine + RefineOrphanage. The original
# behaviour applied a single `--session` arg to every file in the dir, so
# files in the "wrong" session erred with `*** Cannot load theory
# "<session>.<theory>"` because the temp session's heap chain didn't
# include the file's actual session content.
#
# Fix: for each .thy, look up its owning session in the inventory DB
# (built by tools/lemma_inventory/build.py). The supplied $SESSION arg
# becomes the FALLBACK for files with no inventory entry.
INVENTORY_DB="${INVENTORY_DB:-/workspace/reports/inventory/baseline.db}"
_session_for() {
  local thy="$1"
  if [ ! -f "$INVENTORY_DB" ]; then
    echo "$SESSION"; return
  fi
  python3 - "$thy" "$INVENTORY_DB" "$SESSION" <<'PY' 2>/dev/null || echo "$SESSION"
import sqlite3, sys, pathlib
thy, db, fallback = sys.argv[1], sys.argv[2], sys.argv[3]
candidates = [
    pathlib.Path('/sel4-project/verification/l4v'),
    pathlib.Path('/workspace/verification/l4v'),
]
rel = None
for root in candidates:
    try:
        rel = str(pathlib.Path(thy).resolve().relative_to(root)); break
    except ValueError:
        continue
if rel is None:
    print(fallback); sys.exit(0)
c = sqlite3.connect(db).cursor()
c.execute('SELECT session FROM theories WHERE path = ?', (rel,))
r = c.fetchone()
print(r[0] if r and r[0] else fallback)
PY
}

# Session-scoped lock acquired for the whole batch; child proof-timing calls
# inherit $ISABELLE_LOCK_HELD and skip re-acquire. Prevents a separate
# check-theory / proof-timing invocation from racing the scan.
if [ -z "${ISABELLE_LOCK_HELD:-}" ]; then
  LOCK_FILE="/tmp/isabelle-session-${SESSION}.lock"
  exec 200>"$LOCK_FILE"
  if ! flock -n 200; then
    echo "Error: another Isabelle tool is already running on session '$SESSION' (lock: $LOCK_FILE). Invoke skill tools serially — see SKILL.md." >&2
    exit 4
  fi
  export ISABELLE_LOCK_HELD="${SESSION}:$$"
fi

echo "# Slow Proofs Report" > "$REPORT"
echo "" >> "$REPORT"
echo "- **Directory**: $DIR" >> "$REPORT"
echo "- **Session**: $SESSION" >> "$REPORT"
echo "- **Date**: $(date -u '+%Y-%m-%d %H:%M UTC')" >> "$REPORT"
echo "" >> "$REPORT"

# Collect all .thy files, deduplicated, sorted by size (largest first)
# Architecture filter: skip non-target arch subdirs to avoid heap mismatch hangs.
# Default L4V_ARCH is ARM, so we exclude AARCH64/RISCV64/ARM_HYP/X64 subdirectories.
L4V_ARCH="${L4V_ARCH:-ARM}"
EXCLUDE_ARCHS="AARCH64 RISCV64 ARM_HYP X64"
EXCLUDE_FIND=""
for arch in $EXCLUDE_ARCHS; do
  if [ "$arch" != "$L4V_ARCH" ]; then
    EXCLUDE_FIND="$EXCLUDE_FIND -not -path '*/$arch/*'"
  fi
done
FILES=$(eval find "$DIR" -name "*.thy" -not -name "Tmp_*" $EXCLUDE_FIND -printf "'%s %p\n'" | sort -rn | awk '!seen[$2]++ {print $2}')
TOTAL=$(echo "$FILES" | wc -l)

echo "Scanning $TOTAL .thy files in $DIR (session=$SESSION)..."
echo ""

IDX=0
SLOW_FILES=0

for f in $FILES; do
  IDX=$((IDX + 1))
  base=$(basename "$f" .thy)
  printf "[%3d/%d] %-50s " "$IDX" "$TOTAL" "$base"

  # Bug 3 fix: per-file session lookup from inventory.
  FILE_SESSION="$(_session_for "$f")"
  # Run proof-timing and capture output
  OUTPUT=$("$SCRIPT_DIR/proof-timing.sh" "$f" "$FILE_SESSION" 2>&1) || true

  # Extract baseline
  BASELINE=$(echo "$OUTPUT" | grep "Baseline:" | head -1 | grep -oP '\d+(?=ms)' || echo "0")

  if [ "$BASELINE" -lt 5000 ]; then
    echo "skip (${BASELINE}ms < 5s)"
    continue
  fi

  # Check for slow proofs
  SLOW=$(echo "$OUTPUT" | grep -A100 "Slow proofs" | grep "^    " | head -10)

  if [ -z "$SLOW" ]; then
    echo "${BASELINE}ms — no slow proofs"
  else
    SLOW_FILES=$((SLOW_FILES + 1))
    echo "${BASELINE}ms — SLOW PROOFS FOUND:"
    echo "$SLOW" | while read line; do echo "      $line"; done

    # Add to report
    echo "## $base" >> "$REPORT"
    echo "" >> "$REPORT"
    echo "- **File**: $f" >> "$REPORT"
    echo "- **Baseline**: ${BASELINE}ms" >> "$REPORT"
    echo "" >> "$REPORT"
    echo '```' >> "$REPORT"
    echo "$OUTPUT" | grep -A100 "Sorry-substitution" | head -20 >> "$REPORT"
    echo '```' >> "$REPORT"
    echo "" >> "$REPORT"
  fi
done

echo ""
echo "── Done ──"
echo "Scanned: $TOTAL files"
echo "Files with slow proofs: $SLOW_FILES"
echo "Report: $REPORT"

# Add summary to report
echo "## Summary" >> "$REPORT"
echo "" >> "$REPORT"
echo "- **Scanned**: $TOTAL files" >> "$REPORT"
echo "- **Files with slow proofs (>3s per proof)**: $SLOW_FILES" >> "$REPORT"
