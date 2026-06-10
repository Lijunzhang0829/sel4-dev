#!/usr/bin/env bash
# Re-runnable measurement for spec-0020 (KHeap_AI / set_object_cur_thread[wp]).
# Assumes [[0019]]'s set_object_cdt[wp] is applied first.
set -euo pipefail

THEORY="verification/l4v/proof/invariant-abstract/KHeap_AI.thy"
SESSION="AInvs"

ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
SPEC_TOOLS="${SPEC_TOOLS:-tools/spec_strengthen}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

TMP_RANGE_PATCH=$(mktemp /tmp/spec0020-XXXXXX.patch)
cat > "$TMP_RANGE_PATCH" <<'INNER'
1283 1283
  by (wpsimp wp: set_object_wp_strong)

lemma set_object_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
INNER

echo "[1/3] baseline wall ..." >&2
BASELINE_OUT=$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$REPO_ROOT/$THEORY" "$SESSION" 2>&1 | tail -1)
BASELINE_MS=$(echo "$BASELINE_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
echo "baseline_wall_ms=$BASELINE_MS"

echo "[2/3] trial wall ..." >&2
TRIAL_OUT=$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$REPO_ROOT/$THEORY" "$SESSION" --patch "$TMP_RANGE_PATCH" 2>&1 | tail -1)
echo "$TRIAL_OUT" | grep -q '^OK' || { echo "patch FAILED" >&2; exit 1; }
TRIAL_MS=$(echo "$TRIAL_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
echo "trial_wall_ms=$TRIAL_MS"

echo "[3/3] impact verdict ..." >&2
python3 "$REPO_ROOT/$SPEC_TOOLS/spec_impact.py" "$TMP_RANGE_PATCH" "$REPO_ROOT/$THEORY" \
  --baseline-wall "$BASELINE_MS" --trial-wall "$TRIAL_MS" \
  --tree "$REPO_ROOT/verification/l4v/proof" \
  --measurement-out "$(dirname "$0")/measurement.json"

DELTA_PCT=$(awk -v b="$BASELINE_MS" -v t="$TRIAL_MS" 'BEGIN{printf "%.1f", (t-b)*100.0/b}')
echo "delta_pct=$DELTA_PCT"
rm -f "$TMP_RANGE_PATCH"
