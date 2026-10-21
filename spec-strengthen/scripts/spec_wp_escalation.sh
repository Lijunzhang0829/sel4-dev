#!/usr/bin/env bash
# spec_wp_escalation.sh — run the §2.5.1 P/Q + wp regression escalation and
# emit an escalation_record JSON that spec_delivery_gate.py will admit.
#
# A P/Q-slot lemma delivered via [wp] is reject-by-default: registering a [wp]
# rule changes proof-search behaviour for the whole op, so it must be proven
# not to regress. This is the multi-file, three-round measurement that earns a
# P/Q+wp candidate its `allowed` status (execute-additive-design.md §2.5.1):
#
#   round 1  baseline   — sum of check-theory.sh wall over the file set, unpatched
#   round 2  trial      — apply the L'[wp] patch to L's file, re-measure the set
#   round 3  baseline2  — remove the patch, re-measure (reversibility check)
#
# Admission gates (validated again by the delivery gate):
#   trial_ms <= baseline_ms * 1.05            (no wall regression)
#   (baseline2_ms - baseline_ms) / baseline_ms <= 0.05   (reversible, one-sided:
#                       baseline2 must not be materially SLOWER than baseline1)
#   >= 3 files in the regression set
#
# Usage:
#   spec_wp_escalation.sh --patch <range-patch> --lemma-file <L.thy> \
#       --files "<thy1> <thy2> ...> --slot Q [--reason "..."] --out <record.json>
#
#   --patch        the L'[wp] addition (check-theory.sh range-replace patch),
#                  applied to --lemma-file
#   --lemma-file   the theory L lives in (repo-relative or absolute)
#   --files        space-separated regression set (MUST include --lemma-file +
#                  L's in-file consumer file + >=1 cross-file consumer)
#   --slot         P or Q (recorded; the candidate's slot)
#   --reason       why named-realized is insufficient (too many consumers, ...)
#   --out          where to write the escalation record (gate reads this)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"
ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
CHECK="$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh"

PATCH=""; LEMMA_FILE=""; FILES=""; SLOT=""; REASON=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --patch)       PATCH="$2"; shift 2 ;;
    --lemma-file)  LEMMA_FILE="$2"; shift 2 ;;
    --files)       FILES="$2"; shift 2 ;;
    --slot)        SLOT="$2"; shift 2 ;;
    --reason)      REASON="$2"; shift 2 ;;
    --out)         OUT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -z "$PATCH" ] || [ -z "$LEMMA_FILE" ] || [ -z "$FILES" ] || [ -z "$OUT" ] && {
  echo "usage: spec_wp_escalation.sh --patch P --lemma-file L --files \"...\" --slot Q --out R" >&2
  exit 2
}

abspath() {  # repo-relative / l4v-relative / absolute -> absolute
  local t="$1"
  [ -f "$t" ] && { echo "$t"; return; }
  [ -f "$REPO_ROOT/$t" ] && { echo "$REPO_ROOT/$t"; return; }
  [ -f "$REPO_ROOT/verification/l4v/$t" ] && { echo "$REPO_ROOT/verification/l4v/$t"; return; }
  echo ""
}
detect_session() {
  case "$1" in
    *spec/abstract/*) echo ASpec ;; *proof/invariant-abstract/*) echo AInvs ;;
    *proof/refine/*) echo Refine ;; *proof/crefine/*) echo CRefine ;; *) echo "" ;;
  esac
}
wall_ms() { echo "$1" | grep -oE '\([0-9]+ms\)' | tail -1 | tr -d '()ms'; }

LFILE_ABS="$(abspath "$LEMMA_FILE")"
[ -z "$LFILE_ABS" ] && { echo "lemma-file not found: $LEMMA_FILE" >&2; exit 4; }

# measure the SUM of walls over the file set; $1 = "" (baseline) or patch path
measure_set() {
  local patch_arg="$1" total=0 f abs sess raw line ms
  for f in $FILES; do
    abs="$(abspath "$f")"
    [ -z "$abs" ] && { echo "  file not found: $f" >&2; return 1; }
    sess="$(detect_session "$abs")"
    [ -z "$sess" ] && { echo "  no session for: $f" >&2; return 1; }
    if [ -n "$patch_arg" ] && [ "$abs" = "$LFILE_ABS" ]; then
      raw="$(bash "$CHECK" "$abs" "$sess" --patch "$patch_arg" 2>&1 || true)"
    else
      raw="$(bash "$CHECK" "$abs" "$sess" 2>&1 || true)"
    fi
    line="$(echo "$raw" | tail -1)"
    echo "$line" | grep -q '^OK' || { echo "  measure failed on $f: $line" >&2; return 1; }
    ms="$(wall_ms "$line")"; [ -z "$ms" ] && ms=0
    total=$((total + ms))
    echo "  [$f / $sess] ${ms}ms (patched=$([ -n "$patch_arg" ] && [ "$abs" = "$LFILE_ABS" ] && echo yes || echo no))" >&2
  done
  echo "$total"
}

NFILES=$(echo $FILES | wc -w)
echo "[escalation] slot=$SLOT files=$NFILES patch=$PATCH" >&2
echo "[escalation] round 1: baseline ..." >&2
B1="$(measure_set "")" || { echo "baseline round failed" >&2; exit 7; }
echo "[escalation] round 2: trial (L'[wp] applied to $LEMMA_FILE) ..." >&2
T="$(measure_set "$PATCH")" || { echo "trial round failed" >&2; exit 7; }
echo "[escalation] round 3: baseline2 (reversibility) ..." >&2
B2="$(measure_set "")" || { echo "baseline2 round failed" >&2; exit 7; }

python3 - "$B1" "$T" "$B2" "$SLOT" "$NFILES" "$OUT" "$REASON" "$FILES" <<'PY'
import json, sys
b1, t, b2 = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
slot, nfiles, out, reason = sys.argv[4], int(sys.argv[5]), sys.argv[6], sys.argv[7]
files = sys.argv[8].split()
ratio = t / b1 if b1 else 99.0
# ONE-SIDED reversibility: the risk is [wp] leaving a persistent cost, i.e.
# baseline2 SLOWER than baseline1. baseline2 faster (or equal within noise) is
# fine. 5% covers real single-measurement wall noise (a 3% two-sided band
# false-FAILs on the ~6% noise seen on small invariant-abstract files).
rev = (b2 - b1) / b1 if b1 else 99.0   # signed; >0 = slower on re-measure
passed = (nfiles >= 3) and (ratio <= 1.05) and (rev <= 0.05)
rec = {
    "slot": slot, "files": files,
    "rounds": {"baseline_ms": b1, "trial_ms": t, "baseline2_ms": b2},
    "wall_ratio": round(ratio, 4), "reversible_delta": round(rev, 4),
    "reason_named_insufficient": reason or "(unstated)",
    "passed": passed,
}
json.dump(rec, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps(rec, ensure_ascii=False, indent=1))
print(f"\n[escalation] {'PASS' if passed else 'FAIL'}  "
      f"ratio={ratio:.3f} (<=1.05) baseline2-vs-baseline1={rev*100:+.1f}% (<=+5%) "
      f"files={nfiles} (>=3)", file=sys.stderr)
sys.exit(0 if passed else 1)
PY
