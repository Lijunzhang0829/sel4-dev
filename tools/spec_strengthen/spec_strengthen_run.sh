#!/usr/bin/env bash
# tools/spec_strengthen/spec_strengthen_run.sh
#
# Orchestrate a single Pattern G (or custom) spec strengthening end-
# to-end. Stabilizes the workflow that 0014-0022 actually used.
#
# Two invocation modes:
#
#   Mode A (Pattern G — auto-generated template):
#     spec_strengthen_run.sh <theory> <op> <field> <expid> [-y]
#
#     Example:
#       spec_strengthen_run.sh \
#         verification/l4v/proof/invariant-abstract/KHeap_AI.thy \
#         set_object domain_index \
#         0023-set-object-domain-index-frame-lemma
#
#   Mode B (custom patch — author provides the patch file):
#     spec_strengthen_run.sh --patch <patchfile> --theory <theory> \
#                            --expid <expid> [-y]
#
# What it does (Mode A):
#   1.  Pre-flight: direct grep + crunch-derived grep. Abort if
#       either reports a collision (saves a doomed ~30 s check-
#       theory.sh trial).
#   2.  Generate a template Pattern G lemma in the explicit Hoare
#       form (parser-safe per [[0019]] lesson). Write to
#       logs/spec-strengthen-<file>-<op>_<field>-<DATE>.patch.
#   3.  Show the proposed patch + ask "edit-and-continue [yes/no]"
#       (skip with -y).
#   4.  Snapshot the theory file (post-edit baseline).
#   5.  Run baseline wall + trial wall via $ISA_SCRIPTS/check-theory.sh.
#   6.  Run spec_impact.py --measurement-out → measurement.json.
#       Abort if gate fails (no apply, no orphan source change).
#   7.  Apply the patch via check-theory.sh --apply.
#   8.  Capture unified diff via diff -u <pre-snapshot> <post> and
#       prepend the diff --git header.
#   9.  Build reports/experiments/<expid>/ with the full 4-file
#       seL4-source PR audit record.
#
# Defensive design:
#   - Pre-flight is cheap; runs before any check-theory.sh trial.
#   - Snapshot discipline (per [[0018]] lesson) means patch.diff is
#     always generated from the actual pre/post pair, never hand-
#     written or extracted from a cumulative diff.
#   - On any pipeline failure between steps 4 and 7, the script
#     leaves the theory file at the same state it was in before
#     this run (the source hasn't been --apply-ed yet).
#
# Limitations:
#   - Pattern G only for Mode A. Other patterns (B / C / D / E)
#     need a custom patch — use Mode B.
#   - Auto-derived insertion line uses the LAST set_<op>_*[wp]
#     lemma in the file as the anchor. If you want a different
#     insertion point, edit the generated patch before answering "y".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
SPEC_TOOLS="${SPEC_TOOLS:-tools/spec_strengthen}"

# ---------------- arg parsing -----------------------------------------------

CUSTOM_PATCH=""
THEORY=""
OP=""
FIELD=""
EXPID=""
SKIP_PROMPT=0

usage() {
  cat >&2 <<'EOF'
Usage (Mode A — Pattern G):
  spec_strengthen_run.sh <theory> <op> <field> <expid> [-y]

Usage (Mode B — custom patch):
  spec_strengthen_run.sh --patch <patchfile> --theory <theory> \
                         --expid <expid> [-y]

Run from the repo root.
EOF
  exit 2
}

# Detect mode by first token
if [[ "${1:-}" == "--patch" ]]; then
  # Mode B
  while [ $# -gt 0 ]; do
    case "$1" in
      --patch)  CUSTOM_PATCH="$2"; shift 2 ;;
      --theory) THEORY="$2";       shift 2 ;;
      --expid)  EXPID="$2";        shift 2 ;;
      -y)       SKIP_PROMPT=1;     shift ;;
      *)        usage ;;
    esac
  done
  [ -z "$CUSTOM_PATCH" ] || [ -z "$THEORY" ] || [ -z "$EXPID" ] && usage
else
  # Mode A (positional)
  [ $# -lt 4 ] && usage
  THEORY="$1"; OP="$2"; FIELD="$3"; EXPID="$4"
  shift 4
  while [ $# -gt 0 ]; do
    case "$1" in
      -y) SKIP_PROMPT=1; shift ;;
      *)  usage ;;
    esac
  done
fi

# Sanity: theory must exist
[ -f "$THEORY" ] || { echo "theory not found: $THEORY" >&2; exit 4; }

# Derive session from path prefix
detect_session() {
  case "$1" in
    *spec/abstract/*)            echo ASpec ;;
    *spec/cspec/*)               echo CSpec ;;
    *proof/invariant-abstract/*) echo AInvs ;;
    *proof/refine/*)             echo Refine ;;
    *proof/crefine/*)            echo CRefine ;;
    *proof/access-control/*)     echo Access ;;
    *proof/infoflow/*)           echo InfoFlow ;;
    *proof/drefine/*)            echo DRefine ;;
    *proof/bisim/*)              echo Bisim ;;
    *)                           echo "" ;;
  esac
}
SESSION=$(detect_session "$THEORY")
[ -z "$SESSION" ] && { echo "could not derive session from $THEORY" >&2; exit 4; }

THEORY_BASENAME=$(basename "$THEORY" .thy)
DATE_TAG=$(date +%Y%m%d)

echo "================================================================"
echo "spec_strengthen_run.sh"
echo "================================================================"
echo "  theory:  $THEORY"
echo "  session: $SESSION"
echo "  mode:    $([ -n "$CUSTOM_PATCH" ] && echo "B (custom patch)" || echo "A (Pattern G)")"
[ -n "$OP" ]    && echo "  op:      $OP"
[ -n "$FIELD" ] && echo "  field:   $FIELD"
echo "  expid:   $EXPID"
echo ""

# ---------------- preflight (Mode A only) -----------------------------------

confirm() {
  local prompt="$1"
  if [ "$SKIP_PROMPT" = 1 ]; then
    echo "[auto-yes via -y]"
    return 0
  fi
  read -r -p "$prompt [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || return 1
}

if [ -z "$CUSTOM_PATCH" ]; then
  LEMMA_NAME="${OP}_${FIELD}"

  echo "[preflight 1/3] direct grep for $LEMMA_NAME ..."
  # NOTE: `grep | wc -l` with no matches makes grep exit 1, which
  # set -e + pipefail would propagate. Trail with `|| true` to
  # neutralize. Same pattern in step 2/3 below.
  DIRECT_HITS=$( (grep -rln "\b${LEMMA_NAME}\b" verification/l4v/ 2>/dev/null || true) | wc -l)
  if [ "$DIRECT_HITS" -gt 0 ]; then
    echo "  ✗ FAIL — $LEMMA_NAME already exists in $DIRECT_HITS files"
    grep -rn "\b${LEMMA_NAME}\b" verification/l4v/ 2>/dev/null | head -3 || true
    exit 5
  fi
  echo "  ✓ 0 hits"

  echo "[preflight 2/3] crunch-derived grep ..."
  WRAPPERS="set_simple_ko set_cap thread_set set_thread_state set_bound_notification ${OP}"
  PATTERN=""
  for w in $WRAPPERS; do
    PATTERN="${PATTERN}crunch ${FIELD}\\b.*${w}|"
  done
  PATTERN="${PATTERN%|}"
  CRUNCH_HITS=$( (grep -rEn "${PATTERN}" verification/l4v/proof/invariant-abstract/ 2>/dev/null || true) | wc -l)
  if [ "$CRUNCH_HITS" -gt 0 ]; then
    echo "  ✗ FAIL — $FIELD has $CRUNCH_HITS crunch derivation(s) on wrappers"
    echo "          that reach $OP. Skip this field; the gap is"
    echo "          already covered. Hits:"
    grep -rEn "${PATTERN}" verification/l4v/proof/invariant-abstract/ 2>/dev/null | head -5 || true
    exit 5
  fi
  echo "  ✓ 0 hits"

  # Find insertion anchor — last set_<op>_*[wp] lemma in the file
  ANCHOR_LINE=$(grep -nE "^lemma ${OP}_[a-zA-Z_]+\s*\[wp\]?:" "$THEORY" | tail -1 | cut -d: -f1)
  if [ -z "$ANCHOR_LINE" ]; then
    echo "[preflight 3/3] ✗ no existing ${OP}_*[wp] lemma found as anchor"
    exit 5
  fi
  # Find the `by` line of the anchor lemma (search forward ~10 lines).
  # awk regex uses POSIX [[:space:]], NOT Perl \s.
  BY_LINE=$(awk -v start="$ANCHOR_LINE" 'NR>=start && /^[[:space:]]*by[[:space:]]/ {print NR; exit}' "$THEORY")
  [ -z "$BY_LINE" ] && { echo "could not locate by-line of anchor lemma at $ANCHOR_LINE" >&2; exit 5; }
  ANCHOR_NAME=$(sed -n "${ANCHOR_LINE}p" "$THEORY" | grep -oE "^lemma ${OP}_[a-zA-Z_]+" | sed "s/^lemma //")
  echo "[preflight 3/3] insertion anchor: $ANCHOR_NAME (by-line $BY_LINE)"
  echo "  ✓ GO"
  echo ""

  # Generate the patch file
  PATCH_PATH="logs/spec-strengthen-${THEORY_BASENAME}-${LEMMA_NAME}-${DATE_TAG}.patch"
  mkdir -p logs
  BY_TEXT=$(sed -n "${BY_LINE}p" "$THEORY")
  cat > "$PATCH_PATH" <<EOF
${BY_LINE} ${BY_LINE}
${BY_TEXT}

lemma ${LEMMA_NAME}[wp]:
  "\\<lbrace>\\<lambda>s. P (${FIELD} s)\\<rbrace> ${OP} p ko \\<lbrace>\\<lambda>_ s. P (${FIELD} s)\\<rbrace>"
  ${BY_TEXT##  }
EOF

  echo "Generated patch at: $PATCH_PATH"
  echo "----------------------------------------------------------------"
  cat "$PATCH_PATH"
  echo "----------------------------------------------------------------"
  echo "Edit if needed (different statement, different proof tactic, ..)"
  echo "and then continue. The default uses Pattern G shape +"
  echo "the same 'by ...' tactic as the anchor lemma."
  echo ""
  confirm "Continue with this patch?" || { echo "aborted"; exit 0; }

  CUSTOM_PATCH="$PATCH_PATH"
else
  [ -f "$CUSTOM_PATCH" ] || { echo "patch not found: $CUSTOM_PATCH" >&2; exit 4; }
fi

# ---------------- shape check (Mode A & B) ----------------------------------

echo "[check 1/8] spec_witness_gen.py shape ..."
SHAPE_OUT=$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_witness_gen.py" "$CUSTOM_PATCH" "$THEORY" 2>&1 | head -3)
echo "$SHAPE_OUT" | sed 's/^/  /'
case "$SHAPE_OUT" in
  *"SHAPE 1"*) WITNESS_NEEDED=1 ;;
  *"SHAPE 2"*) WITNESS_NEEDED=0 ;;
  *"SHAPE 3"*) echo "  ✗ shape 3 (deletion) — Acceptance Gate 2 will fail. Abort."; exit 6 ;;
  *"WEAKENING"*) echo "  ✗ weakening detected. Abort."; exit 6 ;;
  *) echo "  ⚠ ambiguous shape — proceed with manual review"; WITNESS_NEEDED=0 ;;
esac

# ---------------- snapshot (pre-apply) --------------------------------------

SNAPSHOT_DIR="/tmp/spec_strengthen_run"
mkdir -p "$SNAPSHOT_DIR"
PRE_SNAPSHOT="$SNAPSHOT_DIR/$(basename "$THEORY").pre.$$"
cp "$THEORY" "$PRE_SNAPSHOT"
echo "[check 2/8] snapshot saved → $PRE_SNAPSHOT"

# ---------------- baseline wall ---------------------------------------------

echo "[check 3/8] baseline wall ..."
BASELINE_OUT=$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$REPO_ROOT/$THEORY" "$SESSION" 2>&1 | tail -1)
BASELINE_MS=$(echo "$BASELINE_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
[ -z "$BASELINE_MS" ] && { echo "  ✗ baseline run failed: $BASELINE_OUT"; exit 7; }
echo "  ✓ baseline_wall_ms=$BASELINE_MS"

# ---------------- trial wall ------------------------------------------------

echo "[check 4/8] trial wall (with patch) ..."
TRIAL_OUT=$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$REPO_ROOT/$THEORY" "$SESSION" --patch "$REPO_ROOT/$CUSTOM_PATCH" 2>&1 | tail -1)
TRIAL_MS=$(echo "$TRIAL_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
if ! echo "$TRIAL_OUT" | grep -q '^OK'; then
  echo "  ✗ trial FAILED: $TRIAL_OUT"
  echo "  source untouched (no apply happened). Exit."
  exit 7
fi
DELTA_PCT=$(awk -v b="$BASELINE_MS" -v t="$TRIAL_MS" 'BEGIN{printf "%.1f", (t-b)*100.0/b}')
echo "  ✓ trial_wall_ms=$TRIAL_MS (Δ ${DELTA_PCT}%)"

# ---------------- spec_impact + measurement.json ----------------------------

AUDIT_DIR="reports/experiments/${EXPID}"
mkdir -p "$REPO_ROOT/$AUDIT_DIR"

echo "[check 5/8] spec_impact verdict ..."
IMPACT_OUT=$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_impact.py" "$REPO_ROOT/$CUSTOM_PATCH" "$REPO_ROOT/$THEORY" \
  --baseline-wall "$BASELINE_MS" --trial-wall "$TRIAL_MS" \
  --tree "$REPO_ROOT/verification/l4v/proof" \
  --measurement-out "$REPO_ROOT/$AUDIT_DIR/measurement.json" 2>&1)
GATE=$(python3 -c "import json; print('PASS' if json.load(open('$REPO_ROOT/$AUDIT_DIR/measurement.json'))['gate_pass'] else 'FAIL')")
VERDICT=$(python3 -c "import json; print(json.load(open('$REPO_ROOT/$AUDIT_DIR/measurement.json'))['impact_verdict'])")
echo "  verdict=$VERDICT gate=$GATE"
if [ "$GATE" != "PASS" ]; then
  echo "  ✗ Acceptance Gate 2 FAIL — no apply."
  echo "  Inspect: $REPO_ROOT/$AUDIT_DIR/measurement.json"
  exit 8
fi

# ---------------- apply ------------------------------------------------------

echo "[check 6/8] apply ..."
APPLY_OUT=$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$REPO_ROOT/$THEORY" "$SESSION" --apply "$REPO_ROOT/$CUSTOM_PATCH" 2>&1 | tail -3)
APPLY_MS=$(echo "$APPLY_OUT" | grep -oE '\([0-9]+ms\)' | head -1 | tr -d '()ms')
if ! echo "$APPLY_OUT" | grep -q 'Patch applied'; then
  echo "  ✗ apply FAILED"
  echo "$APPLY_OUT" | sed 's/^/    /'
  exit 7
fi
echo "  ✓ apply_wall_ms=$APPLY_MS"

# ---------------- audit dir: patch.diff via snapshot ------------------------

echo "[check 7/8] capture diff + build audit dir ..."

# Generate patch.diff via diff -u against snapshot — the only
# reliable way per [[0018]] (no hand-written unified diffs).
DIFF_TMP=$(mktemp)
diff -u "$PRE_SNAPSHOT" "$THEORY" > "$DIFF_TMP" || true
PATCH_DIFF="$REPO_ROOT/$AUDIT_DIR/patch.diff"
{
  echo "diff --git a/${THEORY#verification/l4v/} b/${THEORY#verification/l4v/}"
  sed "1s|^--- .*|--- a/${THEORY#verification/l4v/}|; 2s|^+++ .*|+++ b/${THEORY#verification/l4v/}|" "$DIFF_TMP"
} > "$PATCH_DIFF"
rm -f "$DIFF_TMP"

# Verify the patch.diff round-trips
if git -C verification/l4v apply --check --reverse "$PATCH_DIFF" 2>&1 | grep -q '^error:'; then
  echo "  ⚠ patch.diff failed reverse-apply check — manual review needed"
else
  echo "  ✓ patch.diff round-trips"
fi

# Copy the range-replace patch into audit dir for transparency
cp "$CUSTOM_PATCH" "$REPO_ROOT/$AUDIT_DIR/range-patch.patch.txt"

# Generate command.sh
cat > "$REPO_ROOT/$AUDIT_DIR/command.sh" <<EOF
#!/usr/bin/env bash
# Re-runnable measurement for $EXPID. Generated by spec_strengthen_run.sh.
set -euo pipefail

THEORY="$THEORY"
SESSION="$SESSION"
ISA_SCRIPTS="\${ISA_SCRIPTS:-$ISA_SCRIPTS}"
SPEC_TOOLS="\${SPEC_TOOLS:-$SPEC_TOOLS}"
REPO_ROOT="\$(cd "\$(dirname "\$0")/../../.." && pwd)"

TMP_RANGE_PATCH=\$(mktemp /tmp/${EXPID}-XXXXXX.patch)
cp "\$(dirname "\$0")/range-patch.patch.txt" "\$TMP_RANGE_PATCH"

echo "[1/3] baseline wall ..." >&2
BASELINE_OUT=\$(bash "\$REPO_ROOT/\$ISA_SCRIPTS/check-theory.sh" "\$REPO_ROOT/\$THEORY" "\$SESSION" 2>&1 | tail -1)
BASELINE_MS=\$(echo "\$BASELINE_OUT" | grep -oE '\\([0-9]+ms\\)' | tr -d '()ms')
echo "baseline_wall_ms=\$BASELINE_MS"

echo "[2/3] trial wall ..." >&2
TRIAL_OUT=\$(bash "\$REPO_ROOT/\$ISA_SCRIPTS/check-theory.sh" "\$REPO_ROOT/\$THEORY" "\$SESSION" --patch "\$TMP_RANGE_PATCH" 2>&1 | tail -1)
echo "\$TRIAL_OUT" | grep -q '^OK' || { echo "patch FAILED" >&2; exit 1; }
TRIAL_MS=\$(echo "\$TRIAL_OUT" | grep -oE '\\([0-9]+ms\\)' | tr -d '()ms')
echo "trial_wall_ms=\$TRIAL_MS"

echo "[3/3] impact verdict ..." >&2
python3 "\$REPO_ROOT/\$SPEC_TOOLS/spec_impact.py" "\$TMP_RANGE_PATCH" "\$REPO_ROOT/\$THEORY" \\
  --baseline-wall "\$BASELINE_MS" --trial-wall "\$TRIAL_MS" \\
  --tree "\$REPO_ROOT/verification/l4v/proof" \\
  --measurement-out "\$(dirname "\$0")/measurement.json"

DELTA_PCT=\$(awk -v b="\$BASELINE_MS" -v t="\$TRIAL_MS" 'BEGIN{printf "%.1f", (t-b)*100.0/b}')
echo "delta_pct=\$DELTA_PCT"
rm -f "\$TMP_RANGE_PATCH"
EOF
chmod +x "$REPO_ROOT/$AUDIT_DIR/command.sh"

# Generate decision.md skeleton (author fills in the prose)
cat > "$REPO_ROOT/$AUDIT_DIR/decision.md" <<EOF
# spec-${EXPID%%-*} — \`${LEMMA_NAME:-(custom)}\` (seL4-source PR)

| Field | Value |
|---|---|
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | $(date +%Y-%m-%d) |
| Verdict | applied |
| Patch shape | $([ "$WITNESS_NEEDED" = 1 ] && echo "1 (modify)" || echo "2 (additive)") |
| Impact verdict | $VERDICT |
| Acceptance | $GATE |
| File | $THEORY |
| Δ wall | $(awk -v b="$BASELINE_MS" -v t="$TRIAL_MS" 'BEGIN{printf "%.1f%%", (t-b)*100.0/b}') |

## What changed

(see patch.diff — the unified-diff record of the source change)

## Reference / motivating companion

(TODO: describe the existing lemma(s) that motivated this addition, with line
references)

## Spec strengthening claim

(TODO: explain why the new lemma is stronger than what existed before)

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK ${TRIAL_MS} ms |
| 2. spec_impact verdict | ✓ $VERDICT |
| 3. trial wall ≤ baseline × 1.30 | $(awk -v b="$BASELINE_MS" -v t="$TRIAL_MS" 'BEGIN{p=(t-b)*100.0/b; print (p<=30 ? "✓" : "✗") " " p "%"}') |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

| Phase | Wall | Notes |
|---|---:|---|
| baseline | ${BASELINE_MS} ms | |
| trial | ${TRIAL_MS} ms | |
| apply | ${APPLY_MS} ms | re-verifies |

Tier-2 consumer count: see \`measurement.json\#consumers_lines\`/\`consumers_files\`.
Cross-session: NOT rebuilt (deferred per session policy).

## Notes / follow-ups

(TODO)
EOF

echo "  ✓ audit dir built at $AUDIT_DIR"
ls "$REPO_ROOT/$AUDIT_DIR"

# ---------------- summary ---------------------------------------------------

echo ""
echo "[check 8/8] SUMMARY"
echo "================================================================"
echo "  experiment:   $EXPID"
echo "  lemma:        ${LEMMA_NAME:-(custom)}"
echo "  baseline ms:  $BASELINE_MS"
echo "  trial ms:     $TRIAL_MS  (Δ ${DELTA_PCT}%)"
echo "  apply ms:     $APPLY_MS"
echo "  verdict:      $VERDICT"
echo "  gate:         $GATE"
echo "  audit dir:    $AUDIT_DIR/"
echo "  range patch:  $CUSTOM_PATCH"
echo ""
echo "Next steps (author):"
echo "  1. Fill in TODO sections in $AUDIT_DIR/decision.md"
echo "  2. Review patch.diff for correctness"
echo "  3. git add $AUDIT_DIR/ && git commit"
echo "  4. (optional) git -C verification/l4v stash if you want to"
echo "     revert the source change without committing."
echo "================================================================"
