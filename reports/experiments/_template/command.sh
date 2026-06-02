#!/usr/bin/env bash
# TEMPLATE: command.sh — re-runnable measurement command
#
# CI (or any auditor) runs this from the repo root on a clean checkout
# of verification/l4v at the ref recorded in measurement.json#baseline_ref.
#
# Contract:
#   - Stdout: baseline_wall_ms, trial_wall_ms, delta_pct (one per line)
#   - Exit 0  iff all gates pass (check-theory.sh OK + spec_impact gate)
#   - Exit != 0 if any gate fails

set -euo pipefail

# === EDIT: fill these placeholders for your experiment ===
THEORY_FILE="verification/l4v/proof/invariant-abstract/<File>_AI.thy"
SESSION="AInvs"   # or ASpec, Refine, etc per session-mapping table
PATCH="$(dirname "$0")/patch.diff"
# === END EDIT ===

ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
SPEC_TOOLS="${SPEC_TOOLS:-tools/spec_strengthen}"

echo "[1/3] baseline wall ..." >&2
BASELINE_OUT=$(bash "$ISA_SCRIPTS/check-theory.sh" "$THEORY_FILE" "$SESSION" 2>&1 | tail -1)
BASELINE_MS=$(echo "$BASELINE_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
echo "baseline_wall_ms=$BASELINE_MS"

echo "[2/3] trial wall (patch verifies main lemma + witness in one pass) ..." >&2
TRIAL_OUT=$(bash "$ISA_SCRIPTS/check-theory.sh" "$THEORY_FILE" "$SESSION" --patch "$PATCH" 2>&1 | tail -1)
echo "$TRIAL_OUT" | grep -q '^OK' || { echo "patch FAILED" >&2; exit 1; }
TRIAL_MS=$(echo "$TRIAL_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
echo "trial_wall_ms=$TRIAL_MS"

echo "[3/3] impact verdict + gate ..." >&2
python3 "$SPEC_TOOLS/spec_impact.py" "$PATCH" "$THEORY_FILE" \
  --baseline-wall "$BASELINE_MS" --trial-wall "$TRIAL_MS" \
  --tree verification/l4v/proof \
  --measurement-out "$(dirname "$0")/measurement.json"

if [ -n "${BASELINE_MS:-}" ] && [ -n "${TRIAL_MS:-}" ]; then
  DELTA_PCT=$(awk -v b="$BASELINE_MS" -v t="$TRIAL_MS" 'BEGIN{printf "%.1f", (t-b)*100.0/b}')
  echo "delta_pct=$DELTA_PCT"
fi
