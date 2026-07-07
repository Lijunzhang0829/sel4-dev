#!/usr/bin/env bash
# regen.sh — corpus-level regeneration driver (mode-A, single theory).
#
# Pipeline per scope theory:
#   [pre]   recoverability: assert in-scope l4v file clean; snapshot HEAD+sha256
#   [0]     BASELINE  check-theory on the ORIGINAL (confirm green + baseline wall)
#   [1]     EXTRACT   regen_extract.py -> keepset.json
#   [2]     STRIP     regen_strip.py   -> kept_skeleton.thy + task_spec.json
#   [3]     LOOP      regen_agent.py (claude -p) -> attempt Schedule_R.thy
#                     -> check-theory (Refine) -> green? anti-cheat gate
#                     -> on red, feed *** back (repair), bounded by REGEN_MAX_ROUNDS
#   [4]     MEASURE   aggregate LOC / lemma-count / wall  attempt vs human
#   [post]  recoverability: assert in-scope l4v file STILL clean
#
# The l4v source is NEVER mutated: the agent writes only under the experiment
# dir; check-theory verifies on its own mktemp copy. Everything is archived.
#
# Usage: regen/regen.sh <theory.thy> [--rounds N] [--model M] [--effort E]
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

DOCKER_CTR="${REGEN_CONTAINER:-sel4-l4v}"
CTR_CHECK="/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh"
REFINE_DIR="${REGEN_REFINE_DIR:-verification/l4v/proof/refine/ARM}"
L4V_TREE="verification/l4v"
SESSION="${REGEN_SESSION:-Refine}"

# check-theory via direct docker exec (compose service name doesn't resolve here);
# heap-consistent L4V_DIR=/sel4-project; theory path passed as container /workspace/<rel>.
check_theory() {  # $1 = repo-relative path to a .thy (basename must match its `theory` name)
  docker exec -e L4V_DIR=/sel4-project/verification/l4v -e L4V_ARCH=ARM "$DOCKER_CTR" \
    bash "$CTR_CHECK" "/workspace/$1" "$SESSION"
}

THEORY_ARG="${1:?usage: regen.sh <theory.thy> [--rounds N] [--model M]}"; shift || true
ROUNDS="${REGEN_MAX_ROUNDS:-6}"
MODEL="${REGEN_MODEL:-sonnet}"
EFFORT="${REGEN_EFFORT:-high}"
while [ $# -gt 0 ]; do case "$1" in
  --rounds) ROUNDS="$2"; shift 2;;
  --model)  MODEL="$2";  shift 2;;
  --effort) EFFORT="$2"; shift 2;;
  *) shift;;
esac; done

THEORY_PATH="$REFINE_DIR/$(basename "$THEORY_ARG")"
[ -f "$THEORY_PATH" ] || THEORY_PATH="$THEORY_ARG"
[ -f "$THEORY_PATH" ] || { echo "theory not found: $THEORY_ARG" >&2; exit 2; }
TBASE="$(basename "$THEORY_PATH" .thy)"
REL_IN_L4V="${THEORY_PATH#"$L4V_TREE"/}"

TS="$(python3 -c 'import datetime,sys; sys.stdout.write(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))')"
EXP="reports/regen/${TBASE,,}-${TS}"
mkdir -p "$EXP"
LOG="$EXP/run.log"
exec > >(tee -a "$LOG") 2>&1

echo "== regen mode-A :: $TBASE :: $EXP =="
echo "model=$MODEL effort=$EFFORT rounds=$ROUNDS session=$SESSION"

# ---- [pre] recoverability ---------------------------------------------------
dirty="$(git -C "$L4V_TREE" status --porcelain -- "$REL_IN_L4V" | wc -l)"
if [ "$dirty" -ne 0 ]; then
  echo "ABORT: in-scope l4v file is dirty ($REL_IN_L4V) — cannot guarantee recovery." >&2
  exit 3
fi
HEAD_L4V="$(git -C "$L4V_TREE" rev-parse HEAD)"
SHA_ORIG="$(sha256sum "$THEORY_PATH" | awk '{print $1}')"
python3 - "$EXP/snapshot.json" "$HEAD_L4V" "$REL_IN_L4V" "$SHA_ORIG" <<'PY'
import json,sys
out,head,rel,sha=sys.argv[1:5]
json.dump({"l4v_head":head,"in_scope_file":rel,"orig_sha256":sha}, open(out,"w"), indent=2)
PY
echo "[pre] clean. l4v HEAD=$HEAD_L4V  orig sha256=${SHA_ORIG:0:12}"

# ---- [0] baseline -----------------------------------------------------------
echo "[0] baseline check-theory (original, $SESSION) ..."
BASE_OUT="$(check_theory "$THEORY_PATH" 2>&1)"; BRC=$?
echo "$BASE_OUT" | tail -3
BASE_MS="$(echo "$BASE_OUT" | grep -oE 'OK \([0-9]+ms\)' | grep -oE '[0-9]+' | head -1)"
if [ $BRC -ne 0 ]; then echo "ABORT: original does not build green (rc=$BRC)"; exit 4; fi
ORIG_LINES="$(wc -l < "$THEORY_PATH")"
ORIG_LEMMAS="$(grep -cE '^(lemma|theorem|corollary)' "$THEORY_PATH")"
echo "[0] baseline OK  wall=${BASE_MS}ms  orig: ${ORIG_LINES} lines, ${ORIG_LEMMAS} lemmas"

# ---- [1] extract + [2] strip ------------------------------------------------
echo "[1] extract ..."
python3 regen/scripts/regen_extract.py --refine "$REFINE_DIR" \
   --out "$EXP/keepset.json" "$THEORY_PATH"
echo "[2] strip ..."
python3 regen/scripts/regen_strip.py --keepset "$EXP/keepset.json" \
   --theory "$THEORY_PATH" --outdir "$EXP"

# ---- [3] generate -> verify -> triage -> repair loop ------------------------
# Cost/time is NOT the constraint here: a real green case may be expensive.
# Give the agent a long ceiling so opus deep-thinking rounds don't time out
# (a timeout -> empty attempt -> lost repair chain).
export REGEN_TIMEOUT="${REGEN_TIMEOUT:-7200}"
GREEN=0; PREV=""; ATTEMPT_MS=""; PREV_TRIAGE=""; STALL_STREAK=0
for r in $(seq 1 "$ROUNDS"); do
  echo "--- round $r/$ROUNDS ---"
  RD="$EXP/round-$r"; mkdir -p "$RD"
  ATT="$RD/$TBASE.thy"        # basename must match theory name for check-theory rename
  RAW="$RD/transcript.ndjson"
  if [ -z "$PREV" ]; then
    REGEN_MODEL="$MODEL" REGEN_EFFORT="$EFFORT" \
      python3 regen/scripts/regen_agent.py --task "$EXP/task_spec.json" \
        --out "$ATT" --raw-out "$RAW"
  else
    REGEN_MODEL="$MODEL" REGEN_EFFORT="$EFFORT" \
      python3 regen/scripts/regen_agent.py --task "$EXP/task_spec.json" \
        --out "$ATT" --raw-out "$RAW" --repair "$PREV" --error "$RD/../round-$((r-1))/error.txt"
  fi
  # empty attempt (agent timeout/crash): keep PREV so the next round still
  # REPAIRS the last good attempt instead of restarting a fresh generation
  # (a reset breaks the repair chain and the loop stalls).
  [ -s "$ATT" ] || { echo "round $r: empty attempt (timeout/crash), keeping prior context"; continue; }
  # anti-cheat: no sorry/oops/axiomatization
  if grep -nE '\b(sorry|oops|axiomatization)\b' "$ATT" >/dev/null; then
    echo "round $r: REJECT — attempt contains sorry/oops/axiomatization"
    grep -nE '\b(sorry|oops|axiomatization)\b' "$ATT" | head > "$RD/error.txt"
    echo "*** anti-cheat: remove all sorry/oops/axiomatization; every lemma must be fully proved." >> "$RD/error.txt"
    PREV="$ATT"; continue
  fi
  echo "round $r: check-theory ($SESSION) ..."
  OUT="$(check_theory "$ATT" 2>&1)"; RC=$?
  echo "$OUT" | tail -6 > "$RD/check.tail"
  echo "$OUT" > "$RD/check.full"
  if [ $RC -eq 0 ] && echo "$OUT" | grep -q 'OK ('; then
    ATTEMPT_MS="$(echo "$OUT" | grep -oE 'OK \([0-9]+ms\)' | grep -oE '[0-9]+' | head -1)"
    echo "round $r: GREEN  wall=${ATTEMPT_MS}ms"
    cp "$ATT" "$EXP/regenerated-$TBASE.thy"
    GREEN=$r; break
  else
    echo "round $r: RED (rc=$RC) — triaging"
    echo "$OUT" | grep -nE '^\*\*\*|error|Error|Failed|Undefined|Type unification' | head -60 > "$RD/errors_raw.txt"
    [ -s "$RD/errors_raw.txt" ] || echo "$OUT" | tail -40 > "$RD/errors_raw.txt"
    # per-round SELF-INTROSPECTION: classify (harness-structural / fact-discovery
    # / proof), extract Isabelle's suggested fact names, detect no-progress stall.
    python3 regen/scripts/regen_triage.py --attempt "$ATT" --check "$RD/check.full" \
       --task "$EXP/task_spec.json" ${PREV_TRIAGE:+--prev "$PREV_TRIAGE"} \
       --out "$RD/triage.json" || true
    CAT=$(python3 -c "import json;print(json.load(open('$RD/triage.json')).get('category','?'))" 2>/dev/null)
    STALLED=$(python3 -c "import json;print(json.load(open('$RD/triage.json')).get('stalled',False))" 2>/dev/null)
    HINT=$(python3 -c "import json;print(json.load(open('$RD/triage.json')).get('repair_hint',''))" 2>/dev/null)
    # error handed to the next repair = raw *** errors + the triage hint
    # (concrete fact-name suggestions, structural fix, strategy-change on stall).
    { cat "$RD/errors_raw.txt"; echo; echo "=== TRIAGE (${CAT}) ==="; echo "$HINT"; } > "$RD/error.txt"
    echo "round $r: category=$CAT stalled=$STALLED"
    [ -n "$HINT" ] && echo "  hint: $(echo "$HINT" | head -1)"
    PREV_TRIAGE="$RD/triage.json"
    if [ "$STALLED" = "True" ]; then
      STALL_STREAK=$((STALL_STREAK+1))
      if [ "$STALL_STREAK" -ge 2 ]; then
        echo "== BREAK after round $r: $STALL_STREAK consecutive stalls (no progress). The loop"
        echo "   is not converging — this needs a harness/strategy change, not more rounds. =="
        break
      fi
    else
      STALL_STREAK=0
    fi
    PREV="$ATT"
  fi
done

# ---- [4] measure ------------------------------------------------------------
echo "[4] measure ..."
if [ "$GREEN" -gt 0 ]; then
  ATT="$EXP/regenerated-$TBASE.thy"
  NEW_LINES="$(wc -l < "$ATT")"
  NEW_LEMMAS="$(grep -cE '^(lemma|theorem|corollary)' "$ATT")"
  # verbatim anti-cheat: every CONTRACT statement's `lemma NAME` present
  MISS="$(python3 - "$EXP/task_spec.json" "$ATT" <<'PY'
import json,sys,re
spec=json.load(open(sys.argv[1])); att=open(sys.argv[2]).read()
miss=[k["name"] for k in spec["contracts"] if k["name"] and not re.search(r'\b(lemma|theorem)\s+'+re.escape(k["name"])+r'\b',att)]
print(",".join(miss))
PY
)"
else
  NEW_LINES=0; NEW_LEMMAS=0; MISS="(no green attempt)"
fi

python3 - "$EXP/measurement.json" "$GREEN" "$BASE_MS" "$ATTEMPT_MS" \
   "$ORIG_LINES" "$NEW_LINES" "$ORIG_LEMMAS" "$NEW_LEMMAS" "$MISS" <<'PY'
import json,sys
out,green,bms,ams,ol,nl,olm,nlm,miss=sys.argv[1:10]
def i(x):
    try: return int(x)
    except: return None
rec=dict(green_round=i(green), baseline_wall_ms=i(bms), machine_wall_ms=i(ams),
         human_lines=i(ol), machine_lines=i(nl),
         human_lemmas=i(olm), machine_lemmas=i(nlm),
         missing_contracts=(miss if miss and not miss.startswith("(") else []))
json.dump(rec,open(out,"w"),indent=2)
print(json.dumps(rec,indent=2))
PY

# ---- [post] recoverability --------------------------------------------------
dirty2="$(git -C "$L4V_TREE" status --porcelain -- "$REL_IN_L4V" | wc -l)"
SHA_NOW="$(sha256sum "$THEORY_PATH" | awk '{print $1}')"
if [ "$dirty2" -ne 0 ] || [ "$SHA_NOW" != "$SHA_ORIG" ]; then
  echo "WARNING: in-scope l4v file changed during run! (recovery invariant violated)";
else
  echo "[post] recoverability OK — $REL_IN_L4V byte-identical, tree clean."
fi

if [ "$GREEN" -gt 0 ]; then
  echo "== DONE: GREEN in round $GREEN — regenerated corpus at $EXP/regenerated-$TBASE.thy =="
  [ -n "$MISS" ] && [ "$MISS" != "(no green attempt)" ] && echo "   (verbatim-contract check missing: $MISS)"
else
  echo "== DONE: no green attempt in $ROUNDS rounds — see $EXP/round-*/error.txt =="
fi
echo "archive: $EXP"
