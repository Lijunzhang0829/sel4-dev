#!/usr/bin/env bash
# strengthen.sh — execute_additive driver: scan a theory with an LLM agent,
# trial-verify every proposed additive strengthening, archive the full trace.
#
# WHY THIS EXISTS: spec strengthening used to be driven by per-pattern
# subcommands (`run.sh execute --pattern G|C|A|D ...`) where the candidate was
# found by a mechanical detector and a human hand-wrote the rest. That tied the
# work to the pattern-letter framework that execute-additive-design.md retires.
# This driver is the spec analog of lemma-staticize/bench.sh: ONE controlling
# shell that runs the SAME proven verification gate (check-theory.sh trial +
# spec_impact.py verdict) but lets an AGENT do the scan + strengthening, and
# collects every stream into one timestamped, replayable archive a reviewer can
# audit.
#
# Per theory the pipeline is:
#   [0] BASELINE   check-theory.sh on the unpatched file (once)            -> baseline wall
#   [1] SCAN       spec_agent.py: agent reads the theory, proposes N
#                  additive P/Q/F candidates (slot + delivery + new lemma) -> proposals.json
#   [2] per candidate:
#       a. DELIVERY GATE  spec_delivery_gate.py (execute-additive §2.5)    -> accept/reject
#       b. PATCH          build a range-replace patch (insert L' after anchor)
#       c. TRIAL          check-theory.sh --patch                          -> trial wall / OK
#       d. IMPACT         spec_impact.py (verdict + wall gate)             -> measurement.json
#       e. APPLY          check-theory.sh --apply   (only with --apply)
#       -> per-candidate audit bundle (patch.diff / measurement.json /
#          decision.md / command.sh / proposal.json) + one ledger event
#   [3] SUMMARY    summary.md / summary.csv
#
# The agent only PROPOSES; the trial gate is the sole source of truth. A wrong
# proposal costs a trial build and is recorded as trial_failed — nothing more.
#
# Usage:
#   spec-strengthen/strengthen.sh <theory.thy> [--slot P|Q|F|all] [--n N]
#       [--session S] [--model M] [--focus-lemma NAME] [--escalation REC] [--apply] [-y]
#
#   --escalation REC : a completed §2.5.1 wp-regression record (from
#                      spec_wp_escalation.sh) — the ONLY way a P/Q+wp candidate
#                      is admitted past the delivery gate.
#
#   # dry run (default): trial + impact only, source untouched
#   spec-strengthen/strengthen.sh proof/invariant-abstract/Ipc_AI.thy --slot Q
#   # commit accepted candidates into the source file:
#   spec-strengthen/strengthen.sh .../KHeap_AI.thy --slot F --apply -y
#
# Env knobs (all overridable):
#   SLOT=all  N=3  MODEL=sonnet  APPLY=0  OUTROOT=spec-strengthen/experiments
#   WALL_GATE=1.30   (trial wall must be <= baseline * WALL_GATE)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
SPEC_TOOLS="${SPEC_TOOLS:-spec-strengthen/scripts}"
LEDGER="${LEDGER:-spec-strengthen/candidates/candidate-ledger.jsonl}"
CHECK="$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh"

mkdir -p "$(dirname "$LEDGER")"; touch "$LEDGER"

# ---------------- args ------------------------------------------------------
THEORY=""; SLOT="${SLOT:-all}"; N="${N:-3}"; SESSION=""; MODEL="${MODEL:-sonnet}"
FOCUS=""; APPLY="${APPLY:-0}"; YES=0; WALL_GATE="${WALL_GATE:-1.30}"
OUTROOT="${OUTROOT:-spec-strengthen/experiments}"; ESCALATION="${ESCALATION:-}"
# headless `claude -p` latency is variable (cold model load + queue); give the
# scan generous headroom. Override with AGENT_TIMEOUT=<seconds>.
AGENT_TIMEOUT="${AGENT_TIMEOUT:-600}"
# closed-loop repair: on trial failure, feed the prover error back to the
# agent for a corrected proposal and retry. 0 = old open-loop behaviour.
REPAIR_TRIES="${REPAIR_TRIES:-1}"
# per-trial wall cap. A live run saw a repaired patch's build run >25min
# (normal: ~1min) and eat the whole run budget; one slow candidate must not
# starve the rest.
TRIAL_TIMEOUT="${TRIAL_TIMEOUT:-900}"

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

[ $# -lt 1 ] && usage
THEORY="$1"; shift
[[ "$THEORY" == --* ]] && usage
while [ $# -gt 0 ]; do
  case "$1" in
    --slot)        SLOT="$2"; shift 2 ;;
    --n)           N="$2"; shift 2 ;;
    --session)     SESSION="$2"; shift 2 ;;
    --model)       MODEL="$2"; shift 2 ;;
    --focus-lemma) FOCUS="$2"; shift 2 ;;
    --escalation)  ESCALATION="$2"; shift 2 ;;
    --apply)       APPLY=1; shift ;;
    -y)            YES=1; shift ;;
    -h|--help)     usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

# ---------------- resolve theory + session ----------------------------------
# accept an absolute path, a repo-relative path, or a verification/l4v-relative
# path, or a bare basename to locate under proof/ + spec/. ALWAYS emit an
# ABSOLUTE path: check-theory.sh's _dx.sh only translates paths under $REPO_ROOT
# to /workspace/, so a relative path would pass through untranslated and fail
# realpath inside the container.
resolve_theory() {
  local t="$1"
  # a relative path that exists under cwd (== $REPO_ROOT) must still be
  # emitted absolute — hence realpath, not a bare echo.
  [ -f "$t" ] && { realpath "$t"; return; }
  [ -f "$REPO_ROOT/$t" ] && { echo "$REPO_ROOT/$t"; return; }
  [ -f "$REPO_ROOT/verification/l4v/$t" ] && { echo "$REPO_ROOT/verification/l4v/$t"; return; }
  local hit
  hit="$(find "$REPO_ROOT/verification/l4v/proof" "$REPO_ROOT/verification/l4v/spec" \
         -name "$(basename "$t" .thy).thy" 2>/dev/null | head -1)"
  [ -n "$hit" ] && { echo "$hit"; return; }
  echo ""
}
THEORY_ABS="$(resolve_theory "$THEORY")"
[ -z "$THEORY_ABS" ] && { echo "[strengthen] theory not found: $THEORY" >&2; exit 4; }
THEORY_REL="${THEORY_ABS#$REPO_ROOT/}"
THEORY_SHORT="${THEORY_REL#verification/l4v/}"
THEORY_BASE="$(basename "$THEORY_ABS" .thy)"

detect_session() {
  case "$1" in
    *spec/abstract/*)            echo ASpec ;;
    *proof/invariant-abstract/*) echo AInvs ;;
    *proof/refine/*)             echo Refine ;;
    *proof/crefine/*)            echo CRefine ;;
    *proof/access-control/*)     echo Access ;;
    *) echo "" ;;
  esac
}
[ -z "$SESSION" ] && SESSION="$(detect_session "$THEORY_REL")"
[ -z "$SESSION" ] && { echo "[strengthen] could not derive session from $THEORY_REL; pass --session" >&2; exit 4; }

# ---------------- run dir ----------------------------------------------------
TS="$(date +%Y%m%d-%H%M%S)"
RUN="$OUTROOT/strengthen-${THEORY_BASE}-${TS}"
mkdir -p "$REPO_ROOT/$RUN"
RUN_ABS="$REPO_ROOT/$RUN"

echo "[strengthen] theory=$THEORY_SHORT  session=$SESSION  slot=$SLOT  n=$N  model=$MODEL  apply=$APPLY"
echo "[strengthen] run dir: $RUN"

# Copy any escalation record INTO the run dir so the reproducer is
# self-contained — a P/Q+wp run was admitted *because of* this record; losing
# it would make the rerun reject the very candidates this run accepted.
ESC_REPRO=""
if [ -n "$ESCALATION" ] && [ -f "$ESCALATION" ]; then
  cp "$ESCALATION" "$RUN_ABS/escalation-record.json"
  ESC_REPRO=' --escalation "$(dirname "$0")/escalation-record.json"'
fi

cat > "$RUN_ABS/command.sh" <<EOF
#!/usr/bin/env bash
# Reproduce this strengthen run.
cd "$REPO_ROOT"
SLOT=$SLOT N=$N MODEL=$MODEL APPLY=$APPLY \\
  spec-strengthen/strengthen.sh "$THEORY_REL" --session $SESSION${FOCUS:+ --focus-lemma "$FOCUS"}${ESC_REPRO}
EOF
chmod +x "$RUN_ABS/command.sh"

# ---------------- ledger helper ---------------------------------------------
ledger_append() {  # $1 = JSON dict (without ts)
  python3 -c "
import json
d = json.loads('''$1''')
d['ts'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
print(json.dumps(d, ensure_ascii=False))
" >> "$LEDGER"
}

wall_ms() {  # extract trailing (NNNms) from a check-theory line
  echo "$1" | grep -oE '\([0-9]+ms\)' | tail -1 | tr -d '()ms'
}

# One-page reviewer record (lemma-staticize record.md analog): original lemma
# source + new lemma source + why (hint/rationale/claim) + repair chain +
# verification. Rendered for EVERY candidate terminal state — success or any
# failure — purely from already-written artifacts.
render_record() {  # $1 = candidate dir
  python3 "$REPO_ROOT/$SPEC_TOOLS/spec_record_render.py" "$1" \
    --theory "$THEORY_ABS" --hints "$RUN_ABS/hints.json" \
    --baseline-ms "${BASELINE_MS:-}" >/dev/null 2>&1 || true
}

# Build the (possibly COMPOUND) range patch + patched preview from a
# proposal.json. Re-runnable: the repair loop rebuilds after the agent
# corrects a rejected proposal (anchor/new_lemma may change).
#   $1 = candidate dir (expects $1/proposal.json; writes $1/range-patch.patch.txt
#        and $1/_preview.thy)
build_patch_from_proposal() {
  local cd_="$1"
  python3 - "$cd_/proposal.json" "$THEORY_ABS" > "$cd_/range-patch.patch.txt" <<'PY'
import json, sys
prop = json.load(open(sys.argv[1]))
lines = open(sys.argv[2], encoding="utf-8", errors="replace").read().splitlines()
anchor = int(prop["anchor_line"])
want = prop.get("anchor_text") or ""
cur = lines[anchor-1] if 1 <= anchor <= len(lines) else ""
# STALE-ANCHOR RELOCATION: in --apply mode, an earlier candidate's apply
# shifts later line numbers. If the anchor line's text no longer matches the
# snapshot taken at scan time, find the nearest line that does and re-anchor.
if want and cur != want:
    hits = [i+1 for i, ln in enumerate(lines) if ln == want]
    if hits:
        anchor = min(hits, key=lambda i: abs(i - anchor))
        print(f"[patch] anchor relocated {prop['anchor_line']} -> {anchor} "
              f"(text-match)", file=sys.stderr)
    else:
        print(f"[patch] WARNING: anchor text not found; keeping line "
              f"{anchor} as-is", file=sys.stderr)
anchor_text = lines[anchor-1] if 1 <= anchor <= len(lines) else ""
blocks = [f"{anchor} {anchor}\n{anchor_text}\n\n{prop['new_lemma']}"]
for h in (prop.get("consumer_hunks") or []):
    try:
        s, e, r = int(h["start_line"]), int(h["end_line"]), h["replacement"]
    except (KeyError, TypeError, ValueError):
        continue
    blocks.append(f"{s} {e}\n{r}")
sys.stdout.write("\n---\n".join(blocks))
PY
  python3 "$REPO_ROOT/$SPEC_TOOLS/spec_range_apply.py" \
    "$cd_/range-patch.patch.txt" "$THEORY_ABS" "$cd_/_preview.thy" \
    2>/dev/null || cp "$THEORY_ABS" "$cd_/_preview.thy"
}

# ---------------- [0] baseline ----------------------------------------------
echo "[0] baseline wall (unpatched) ..."
BASELINE_RAW="$(bash "$CHECK" "$THEORY_ABS" "$SESSION" 2>&1 || true)"
echo "$BASELINE_RAW" | tail -3 > "$RUN_ABS/baseline.log"
BASELINE_MS="$(wall_ms "$(echo "$BASELINE_RAW" | tail -1)")"
if [ -z "$BASELINE_MS" ]; then
  echo "  ✗ baseline failed; see $RUN/baseline.log" >&2
  echo "$BASELINE_RAW" | tail -8 | sed 's/^/    /'
  exit 7
fi
echo "  ✓ baseline_wall_ms=$BASELINE_MS"

# ---------------- [0.5] mechanical hints (Q/P detector layer) ---------------
# Q/P slots have mechanical precursors the agent alone cannot find reliably
# (live evidence: a --slot Q run silently degraded to F proposals). The
# detector supplies "directions worth probing"; the agent does the semantic
# judgment; the trial stays the only ground truth (design §6.3).
HINTS=""
if [ "$SLOT" = "Q" ] || [ "$SLOT" = "P" ] || [ "$SLOT" = "all" ]; then
  HINTS="$RUN_ABS/hints.json"
  HSLOT="$SLOT"; [ "$SLOT" = "all" ] && HSLOT="all"
  python3 "$REPO_ROOT/$SPEC_TOOLS/spec_slot_hints.py" "$THEORY_ABS" \
    --slot "$HSLOT" --out "$HINTS" > /dev/null 2> "$RUN_ABS/hints.log" || true
  NHINTS="$(python3 -c "import json;print(len(json.load(open('$HINTS'))))" 2>/dev/null || echo 0)"
  echo "[0.5] detector hints: $NHINTS (slot=$HSLOT) → hints.json"
fi

# ---------------- [1] scan (agent) ------------------------------------------
echo "[1] scan — spec_agent.py proposing candidates ..."
PROPOSALS="$RUN_ABS/proposals.json"
# The agent streams the FULL `claude -p` process on its stderr (stream-json
# trace: session init, thinking, text, result). tee it to BOTH the console and
# agent.log so the operator watches it live while it's still archived.
# SPEC_AGENT_STREAM=0 reverts spec_agent.py to the old quiet single-shot.
python3 "$REPO_ROOT/$SPEC_TOOLS/spec_agent.py" "$THEORY_ABS" \
  --slot "$SLOT" --n "$N" --model "$MODEL" --timeout "$AGENT_TIMEOUT" \
  ${FOCUS:+--focus-lemma "$FOCUS"} ${HINTS:+--hints "$HINTS"} \
  --out "$PROPOSALS" --raw-out "$RUN_ABS/agent-raw.txt" \
  > "$RUN_ABS/agent.stdout" 2> >(tee "$RUN_ABS/agent.log" >&2) || true
[ -s "$PROPOSALS" ] || echo "[]" > "$PROPOSALS"
# Snapshot each proposal's anchor-line TEXT while the file is still pristine.
# In --apply mode an earlier candidate's apply SHIFTS later anchors (live bug:
# candidate 2's stale anchor landed mid-statement → "Outer syntax error");
# build_patch_from_proposal relocates by this text when the line moved.
python3 - "$PROPOSALS" "$THEORY_ABS" <<'PY'
import json, sys
props = json.load(open(sys.argv[1]))
lines = open(sys.argv[2], encoding="utf-8", errors="replace").read().splitlines()
for c in props:
    try:
        a = int(c.get("anchor_line", 0))
        c["anchor_text"] = lines[a-1] if 1 <= a <= len(lines) else ""
    except (TypeError, ValueError):
        c["anchor_text"] = ""
json.dump(props, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
PY
NCAND="$(python3 -c "import json;print(len(json.load(open('$PROPOSALS'))))" 2>/dev/null || echo 0)"
echo "  ✓ $NCAND candidate(s) proposed (agent.log for trace)"
if [ "$NCAND" -eq 0 ]; then
  echo "[strengthen] no candidates; nothing to verify."
  tail -6 "$RUN_ABS/agent.log" | sed 's/^/    /'
  exit 0
fi

# ---------------- [1.5] pre-register the round's proposals -------------------
# Append a `discovered` event for every proposal key BEFORE the per-candidate
# loop, so a block candidate whose downstream is ALSO proposed THIS round can
# satisfy the gate's condition-1 ("downstream already in the ledger"). Without
# this, within-run A-depends-on-B block chains would only see history. We skip
# keys that already carry any event (idempotent across re-runs).
python3 - "$PROPOSALS" "$THEORY_BASE" "$LEDGER" "$RUN" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<'PY'
import json, sys, os
proposals, thy_base, ledger, expid, ts = sys.argv[1:6]
props = json.load(open(proposals))
seen = set()
if os.path.exists(ledger):
    for line in open(ledger):
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line)["key"])
        except (json.JSONDecodeError, KeyError):
            continue
n = 0
with open(ledger, "a", encoding="utf-8") as f:
    for c in props:
        key = f"{c['slot']}:{thy_base}:{c['lemma_name']}"
        if key in seen:
            continue
        seen.add(key)
        f.write(json.dumps({"ts": ts, "key": key, "event": "discovered",
                            "slot": c["slot"], "delivery": c["delivery"],
                            "evidence": "agent", "expid": expid}, ensure_ascii=False) + "\n")
        n += 1
print(f"[strengthen] pre-registered {n} new candidate(s) as discovered", file=sys.stderr)
PY

# ---------------- [2] per-candidate loop ------------------------------------
for ((IDX=0; IDX<NCAND; IDX++)); do
  # pull the candidate fields
  read SLOT_C DELIV LNAME ANCHOR < <(python3 - "$PROPOSALS" "$IDX" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(c["slot"], c["delivery"], c["lemma_name"], c["anchor_line"])
PY
)
  SAFE="$(echo "$LNAME" | tr -c 'A-Za-z0-9_' '_')"
  CD="$RUN_ABS/$(printf '%02d' "$IDX")-${SLOT_C}-${SAFE}"
  mkdir -p "$CD"
  KEY="${SLOT_C}:${THEORY_BASE}:${LNAME}"
  echo "----------------------------------------------------------------"
  echo "[$IDX] $KEY  (delivery=$DELIV, anchor=L$ANCHOR)"

  # write the isolated proposal record
  python3 - "$PROPOSALS" "$IDX" "$CD/proposal.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
json.dump(c, open(sys.argv[3],"w"), ensure_ascii=False, indent=1)
PY

  # P-slot claim verification: the strengthening_claim is mechanically
  # derivable for a pure premise-weakening (strict pre-conjunct subset,
  # identical post) — derive it and OVERWRITE the agent's free-text claim
  # (the first live P case recorded a reversed entailment arrow). A failed
  # check is logged, not fatal: the trial still owns provability, but the
  # decision.md will carry the warning instead of an unverified claim.
  if [ "$SLOT_C" = "P" ]; then
    PCLAIM="$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_slot_hints.py" "$THEORY_ABS" \
      --check-p-claim "$CD/proposal.json" 2>/dev/null || true)"
    echo "$PCLAIM" > "$CD/p_claim_check.json"
    if python3 -c "import json,sys;sys.exit(0 if json.loads(sys.argv[1]).get('ok') else 1)" "$PCLAIM" 2>/dev/null; then
      python3 - "$CD/proposal.json" "$PCLAIM" <<'PY'
import json, sys
prop = json.load(open(sys.argv[1])); res = json.loads(sys.argv[2])
prop["strengthening_claim"] = res["claim"]
json.dump(prop, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
PY
      echo "  ✓ P-claim verified mechanically (strict premise subset, post identical)"
    else
      PWHY="$(python3 -c "import json,sys;print(json.loads(sys.argv[1]).get('reason','?'))" "$PCLAIM" 2>/dev/null || echo '?')"
      echo "  ⚠ P-claim NOT mechanically verifiable: $PWHY (trial still decides provability)"
    fi
  fi

  # ---- [2a] build the (possibly COMPOUND) range patch --------------------
  # hunk 0: insert L' after the anchor line. Optional further hunks come from
  # the proposal's `consumer_hunks` — a named-realized candidate carries its
  # SAME-PATCH consumer this way. check-theory.sh splits hunks on `---`.
  # Also renders the PATCHED PREVIEW (what the file looks like after the
  # patch) that the delivery gate greps for consumer evidence — pure python,
  # no Isabelle, so gate rejects stay free.
  PATCH="$CD/range-patch.patch.txt"
  PREVIEW="$CD/_preview.thy"
  build_patch_from_proposal "$CD"
  NHUNKS="$(python3 -c "import json;print(1+len(json.load(open('$CD/proposal.json')).get('consumer_hunks') or []))")"
  echo "  patch → $(basename "$PATCH") ($NHUNKS hunk(s))"

  # ---- [2b] delivery gate (§2.5 contract, WITH evidence) -----------------
  # The gate VERIFIES delivery, not just "a field was filled in":
  #   --theory     : the PATCHED preview — named-realized needs a real
  #                  consumer (existing OR same-patch) referencing L'
  #   --ledger     : block needs the downstream candidate to already exist
  #                  (this round's proposals are pre-registered as `discovered`)
  #   --escalation : the ONLY way a P/Q+wp candidate is admitted
  GATE_JSON="$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_delivery_gate.py" \
    --in "$CD/proposal.json" --theory "$PREVIEW" --ledger "$LEDGER" \
    ${ESCALATION:+--escalation "$ESCALATION"})"
  echo "$GATE_JSON" > "$CD/delivery_gate.json"
  read GATE_OK GATE_SUB GATE_STATE GATE_GRACE GATE_REC <<EOF
$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(d['accept'],d.get('resolved_substate'),d.get('resolved_delivery_state'),d.get('grace_period_weeks'),d.get('escalation_record'))" "$GATE_JSON")
EOF
  GATE_REASON="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['reason'])" "$GATE_JSON")"
  if [ "$GATE_OK" != "True" ]; then
    echo "  ✗ delivery gate REJECT: $GATE_REASON"
    echo "REJECTED-DELIVERY" > "$CD/verdict.txt"; rm -f "$PREVIEW"; render_record "$CD"
    ledger_append "{\"key\":\"$KEY\",\"event\":\"rejected_delivery\",\"slot\":\"$SLOT_C\",\"delivery\":\"$DELIV\",\"expid\":\"$RUN\",\"reason\":\"$(echo "$GATE_REASON" | tr -d '"' | cut -c1-200)\"}"
    continue
  fi
  echo "  ✓ delivery gate: $GATE_REASON"
  # normalize the gate's tokens (the python prints 'None' for JSON null)
  [ "$GATE_SUB" = "None" ] && GATE_SUB=""
  [ "$GATE_STATE" = "None" ] && GATE_STATE=""
  [ "$GATE_GRACE" = "None" ] && GATE_GRACE=""
  [ "$GATE_REC" = "None" ] && GATE_REC=""

  # ---- [2c] trial — CLOSED LOOP: prover error feeds back to the agent ----
  # The first live runs were open-loop: a failed trial (wrong anchor, proof
  # residue) was a dead end even when trivially fixable. Now the error tail +
  # the rejected proposal + a source window go back to the agent (--repair),
  # which emits a corrected proposal (or [] when it judges the strengthening
  # semantically false — e.g. a genuinely load-bearing premise). Capped by
  # REPAIR_TRIES; every attempt is archived.
  TRIAL_OK=0
  for ((TRY=0; TRY<=REPAIR_TRIES; TRY++)); do
    if [ "$TRY" -gt 0 ]; then
      echo "  [repair $TRY/$REPAIR_TRIES] feeding prover error back to agent ..."
      python3 "$REPO_ROOT/$SPEC_TOOLS/spec_agent.py" "$THEORY_ABS" \
        --repair-proposal "$CD/proposal.json" --repair-error "$CD/trial-error.txt" \
        --model "$MODEL" --timeout "$AGENT_TIMEOUT" \
        --out "$CD/repair-$TRY.json" --raw-out "$CD/repair-$TRY-raw.txt" \
        >/dev/null 2>> "$RUN_ABS/agent.log" || true
      NREP="$(python3 -c "import json;print(len(json.load(open('$CD/repair-$TRY.json'))))" 2>/dev/null || echo 0)"
      if [ "$NREP" -eq 0 ]; then
        echo "  ✗ agent judged it unrepairable (returned [])"
        break
      fi
      [ -f "$CD/proposal-0.json" ] || cp "$CD/proposal.json" "$CD/proposal-0.json"
      python3 -c "import json;json.dump(json.load(open('$CD/repair-$TRY.json'))[0],open('$CD/proposal.json','w'),ensure_ascii=False,indent=1)"
      build_patch_from_proposal "$CD"
      NEW_ANCHOR="$(python3 -c "import json;print(json.load(open('$CD/proposal.json'))['anchor_line'])")"
      echo "  repaired proposal adopted (anchor L$NEW_ANCHOR); re-trialing ..."
    fi
    echo "  [trial] check-theory.sh --patch (cap ${TRIAL_TIMEOUT}s) ..."
    TRIAL_RAW="$(timeout "$TRIAL_TIMEOUT" bash "$CHECK" "$THEORY_ABS" "$SESSION" --patch "$PATCH" 2>&1 || true)"
    echo "$TRIAL_RAW" | tail -25 > "$CD/trial.log"
    TRIAL_LAST="$(echo "$TRIAL_RAW" | tail -1)"
    [ -z "$TRIAL_LAST" ] && TRIAL_LAST="(no output — trial timed out after ${TRIAL_TIMEOUT}s or died)"
    if echo "$TRIAL_LAST" | grep -q '^OK'; then
      TRIAL_OK=1
      [ "$TRY" -gt 0 ] && echo "  ✓ REPAIRED after $TRY feedback round(s)"
      break
    fi
    echo "  ✗ trial FAILED: $TRIAL_LAST"
    echo "$TRIAL_RAW" | tail -6 | sed 's/^/      /'
    echo "$TRIAL_RAW" | tail -25 > "$CD/trial-error.txt"
  done
  if [ "$TRIAL_OK" != 1 ]; then
    echo "TRIAL-FAILED" > "$CD/verdict.txt"
    render_record "$CD"
    ledger_append "{\"key\":\"$KEY\",\"event\":\"trial_failed\",\"slot\":\"$SLOT_C\",\"delivery\":\"$DELIV\",\"expid\":\"$RUN\",\"reason\":\"check-theory --patch did not return OK (after $REPAIR_TRIES repair round(s))\"}"
    continue
  fi
  TRIAL_MS="$(wall_ms "$TRIAL_LAST")"; [ -z "$TRIAL_MS" ] && TRIAL_MS=0
  DELTA="$(awk -v b="$BASELINE_MS" -v t="$TRIAL_MS" 'BEGIN{printf "%.1f",(t-b)*100.0/b}')"
  echo "  ✓ trial OK  trial_wall_ms=$TRIAL_MS (Δ${DELTA}%)"

  # ---- [2d] impact verdict ------------------------------------------------
  python3 "$REPO_ROOT/$SPEC_TOOLS/spec_impact.py" "$PATCH" "$THEORY_ABS" \
    --baseline-wall "$BASELINE_MS" --trial-wall "$TRIAL_MS" \
    --tree "$REPO_ROOT/verification/l4v/proof" \
    --measurement-out "$CD/measurement.json" >/dev/null 2>&1 || true
  if [ ! -s "$CD/measurement.json" ]; then
    echo '{"gate_pass":false,"impact_verdict":"impact-tool-error"}' > "$CD/measurement.json"
  fi
  VERDICT="$(python3 -c "import json;print(json.load(open('$CD/measurement.json')).get('impact_verdict','?'))")"
  IGATE="$(python3 -c "import json;print('PASS' if json.load(open('$CD/measurement.json')).get('gate_pass') else 'FAIL')")"
  # enforce the wall gate here too (spec_impact uses its own cap; we re-assert WALL_GATE)
  WALL_OK="$(awk -v t="$TRIAL_MS" -v b="$BASELINE_MS" -v g="$WALL_GATE" 'BEGIN{print (t<=b*g)?"PASS":"FAIL"}')"
  echo "  impact verdict=$VERDICT  spec_impact_gate=$IGATE  wall_gate(${WALL_GATE})=$WALL_OK"
  if [ "$IGATE" != "PASS" ] || [ "$WALL_OK" != "PASS" ]; then
    echo "  ✗ impact/wall gate FAIL — not accepting."
    echo "IMPACT-FAILED" > "$CD/verdict.txt"
    render_record "$CD"
    ledger_append "{\"key\":\"$KEY\",\"event\":\"impact_failed\",\"slot\":\"$SLOT_C\",\"delivery\":\"$DELIV\",\"expid\":\"$RUN\",\"verdict\":\"$VERDICT\",\"delta_pct\":$DELTA}"
    continue
  fi

  # ---- [2e] apply (only with --apply) OR dry-run patch.diff ---------------
  SNAP="$(mktemp)"; cp "$THEORY_ABS" "$SNAP"
  if [ "$APPLY" = 1 ]; then
    if [ "$YES" != 1 ]; then
      echo "  --- new lemma ---"; sed -n '2,$p' "$PATCH" | sed 's/^/    /'
      read -r -p "  apply this into $THEORY_SHORT? [y/N] " ans
      [[ "$ans" =~ ^[Yy]$ ]] || { echo "  skipped by user"; echo "SKIPPED" > "$CD/verdict.txt"; render_record "$CD"; rm -f "$SNAP"; continue; }
    fi
    APPLY_RAW="$(bash "$CHECK" "$THEORY_ABS" "$SESSION" --apply "$PATCH" 2>&1 | tail -3)"
    if ! echo "$APPLY_RAW" | grep -q 'Patch applied'; then
      echo "  ✗ apply failed"; echo "$APPLY_RAW" | sed 's/^/      /'
      echo "APPLY-FAILED" > "$CD/verdict.txt"
      render_record "$CD"
      ledger_append "{\"key\":\"$KEY\",\"event\":\"trial_failed\",\"slot\":\"$SLOT_C\",\"delivery\":\"$DELIV\",\"expid\":\"$RUN\",\"reason\":\"apply failed\"}"
      rm -f "$SNAP"; continue
    fi
    diff -u "$SNAP" "$THEORY_ABS" > "$CD/_raw.diff" 2>/dev/null || true
    EVENT="applied"
    echo "  ✓ APPLIED into $THEORY_SHORT"
  else
    # render the would-be diff against the PATCHED PREVIEW (already built at
    # [2a], multi-hunk aware); source stays clean
    diff -u "$SNAP" "$PREVIEW" > "$CD/_raw.diff" 2>/dev/null || true
    EVENT="trial_passed"
    echo "  ✓ ACCEPTED (dry-run; pass --apply to commit)"
  fi
  # normalize the diff header to git-style a/b paths
  {
    echo "diff --git a/${THEORY_SHORT} b/${THEORY_SHORT}"
    sed "1s|^--- .*|--- a/${THEORY_SHORT}|; 2s|^+++ .*|+++ b/${THEORY_SHORT}|" "$CD/_raw.diff"
  } > "$CD/patch.diff"
  rm -f "$CD/_raw.diff" "$SNAP" "$PREVIEW"
  echo "$EVENT" > "$CD/verdict.txt"

  # ---- per-candidate command.sh (replay measurement) ----------------------
  cat > "$CD/command.sh" <<EOF
#!/usr/bin/env bash
# Replay the measurement for $KEY.
set -euo pipefail
cd "$REPO_ROOT"
PATCH="\$(dirname "\$0")/range-patch.patch.txt"
B=\$(bash "$CHECK" "$THEORY_ABS" "$SESSION" 2>&1 | tail -1 | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
T=\$(bash "$CHECK" "$THEORY_ABS" "$SESSION" --patch "\$PATCH" 2>&1 | tail -1 | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
python3 "$SPEC_TOOLS/spec_impact.py" "\$PATCH" "$THEORY_ABS" \\
  --baseline-wall "\$B" --trial-wall "\$T" \\
  --tree "verification/l4v/proof" --measurement-out "\$(dirname "\$0")/measurement.json"
EOF
  chmod +x "$CD/command.sh"

  # ---- per-candidate decision.md ------------------------------------------
  python3 - "$CD/proposal.json" "$CD/measurement.json" "$KEY" "$RUN" \
            "$THEORY_SHORT" "$BASELINE_MS" "$TRIAL_MS" "$DELTA" "$EVENT" \
            "$GATE_REASON" "$CD/delivery_gate.json" > "$CD/decision.md" <<'PY'
import json,sys
prop=json.load(open(sys.argv[1])); meas=json.load(open(sys.argv[2]))
key,expid,thy,b,t,delta,event,gate=sys.argv[3:11]
gj=json.load(open(sys.argv[11]))
# gate-RESOLVED delivery (the raw agent claim may have been demoted)
rsub=gj.get('resolved_substate'); rstate=gj.get('resolved_delivery_state')
rgrace=gj.get('grace_period_weeks'); rrec=gj.get('escalation_record')
downgraded=gj.get('downgraded')
print(f"# {expid.split('/')[-1]} — {prop['lemma_name']}\n")
print("| Field | Value |")
print("|---|---|")
print(f"| Key | `{key}` |")
print(f"| Slot | {prop['slot']} |")
print(f"| Delivery | {prop['delivery']}"
      + (f" / {rsub}" if rsub else "") + " |")
print(f"| Delivery state | {rstate or '—'}"
      + (f" (grace {rgrace}w)" if rstate=='pending' and rgrace else "") + " |")
print(f"| Delivery target | {prop.get('delivery_target') or '—'} |")
print(f"| File | `{thy}` |")
print(f"| Verdict | {'applied' if event=='applied' else 'trial-passed (dry-run)'} |")
print(f"| Impact verdict | {meas.get('impact_verdict','?')} |")
print(f"| Δ wall (trial) | {delta}% |")
print(f"| Walls | baseline={b} ms · trial={t} ms |\n")
print("## Strengthening claim\n")
print(prop.get('strengthening_claim','(none stated)') + "\n")
print("## Why this slot is real (agent rationale)\n")
print(prop.get('rationale','') + "\n")
print("## Delivery attestation\n")
print(f"- mechanism: **{prop['delivery']}**" + (f" → resolved **{rsub}**" if rsub else ""))
print(f"- delivery_state: {rstate or '—'}"
      + (f" (orphan after {rgrace}w with no consumer)" if rstate=='pending' and rgrace else ""))
print(f"- target (agent claim): {prop.get('delivery_target') or '—'}")
if downgraded:
    print(f"- ⚠ **demoted**: the raw claim (`{prop.get('delivery_substate')}`) "
          f"was not backed by evidence; gate resolved it to `{rsub}`.")
if rrec:
    print(f"- escalation_record: `{rrec}` (§2.5.1 P/Q+wp admission)")
print(f"- gate verdict: {gate}\n")
print("## What changed\n")
print("See `patch.diff` (additive — original lemmas untouched). "
      "`range-patch.patch.txt` is the check-theory.sh range-replace input.\n")
print("## Acceptance gate trace\n")
print("| Gate | Result |")
print("|---|---|")
print(f"| 0. delivery contract (§2.5) | ✓ |")
print(f"| 1. check-theory.sh --patch | ✓ OK {t} ms |")
print(f"| 2. spec_impact verdict | ✓ {meas.get('impact_verdict','?')} |")
print(f"| 3. trial wall ≤ baseline×gate | ✓ ({delta}%) |")
print(f"| 4. additive (no `_old` witness needed) | ✓ |\n")
print("## Notes / follow-ups\n")
if rstate=='pending':
    print(f"- ⚠ delivery_state is **pending** ({rsub}): a consumer/downstream "
          f"must land within the {rgrace}-week grace period or "
          f"`spec_delivery_lifecycle.py` will mark this **orphan** (design "
          f"§6.2). Run that sweep periodically to advance the state.")
elif rstate=='realized':
    print("- delivery is **realized** — first-class, no follow-up needed.")
else:
    print("- (none)")
PY

  # ledger event carries the FULL §3.4 delivery lifecycle, using the gate's
  # RESOLVED values (e.g. a faked named-realized was demoted to planned):
  #   delivery_substate  — realized | planned (gate-resolved, not raw claim)
  #   delivery_state     — realized | pending  (state machine entry point)
  #   grace_period_weeks — for pending entries, the orphan threshold
  #   escalation_record  — for P/Q+wp, the §2.5.1 record that admitted it
  # spec_delivery_lifecycle.py later moves `pending` -> realized/orphan.
  # Empty shell vars map to JSON null; python does the encoding (no quoting
  # gymnastics, optional fields handled cleanly).
  python3 - "$KEY" "$EVENT" "$SLOT_C" "$DELIV" "$GATE_SUB" "$GATE_STATE" \
            "$GATE_GRACE" "$GATE_REC" "$RUN" "$VERDICT" "$BASELINE_MS" \
            "$TRIAL_MS" "$DELTA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LEDGER" <<'PY'
import json, sys
(key, event, slot, deliv, sub, state, grace, rec, expid, verdict,
 base, trial, delta, ts) = sys.argv[1:15]
def s(x): return x if x else None
ev = {"ts": ts, "key": key, "event": event, "slot": slot, "delivery": deliv,
      "delivery_substate": s(sub), "delivery_state": s(state),
      "grace_period_weeks": int(grace) if grace else None,
      "escalation_record": s(rec), "expid": expid, "verdict": verdict,
      "walls": {"baseline": int(base), "trial": int(trial)},
      "delta_pct": float(delta)}
print(json.dumps(ev, ensure_ascii=False))
PY

  # one-page reviewer record (success path; failure paths render in-branch)
  render_record "$CD"
done

# ---------------- [3] summary -----------------------------------------------
python3 - "$RUN_ABS" "$THEORY_SHORT" "$SESSION" "$BASELINE_MS" "$APPLY" > "$RUN_ABS/summary.md" <<'PY'
import json,os,glob,sys,csv
run,thy,sess,baseline,apply=sys.argv[1:6]
rows=[]
for cd in sorted(glob.glob(os.path.join(run,"[0-9][0-9]-*"))):
    p=os.path.join(cd,"proposal.json"); v=os.path.join(cd,"verdict.txt")
    if not os.path.exists(p): continue
    prop=json.load(open(p))
    verdict=open(v).read().strip() if os.path.exists(v) else "?"
    meas={}
    mp=os.path.join(cd,"measurement.json")
    if os.path.exists(mp):
        try: meas=json.load(open(mp))
        except Exception: meas={}
    rows.append({"dir":os.path.basename(cd),"name":prop["lemma_name"],
                 "slot":prop["slot"],"delivery":prop["delivery"],
                 "verdict":verdict,"impact":meas.get("impact_verdict",""),
                 "delta":meas.get("delta_pct","")})
with open(os.path.join(run,"summary.csv"),"w",newline="") as f:
    w=csv.writer(f); w.writerow(["dir","name","slot","delivery","verdict","impact","delta_pct"])
    for r in rows: w.writerow([r[k] for k in ("dir","name","slot","delivery","verdict","impact","delta")])
accepted=[r for r in rows if r["verdict"] in ("applied","trial_passed")]
out=[]
out.append(f"# strengthen summary — {thy} ({sess})\n")
out.append(f"- baseline wall: **{baseline} ms**")
out.append(f"- candidates proposed: **{len(rows)}**")
out.append(f"- accepted (trial+impact+delivery pass): **{len(accepted)}/{len(rows)}**"
           + ("  *(applied to source)*" if apply=="1" else "  *(dry-run)*"))
out.append("")
out.append("| dir | lemma | slot | delivery | verdict | impact | Δ% |")
out.append("|---|---|---|---|---|---|---|")
for r in rows:
    out.append(f"| {r['dir']} | `{r['name']}` | {r['slot']} | {r['delivery']} | "
               f"{r['verdict']} | {r['impact']} | {r['delta']} |")
out.append("")
out.append("Verdict legend: `applied` = committed to source · `trial_passed` = "
           "dry-run accept · `trial_failed` = patch did not build · "
           "`impact_failed` = verdict/wall gate · `rejected_delivery` = "
           "failed the §2.5 delivery contract before any build.\n")
out.append("Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), "
           "delivery_gate.json, range-patch.patch.txt, trial.log, "
           "measurement.json, patch.diff, decision.md, command.sh.")
open(os.path.join(run,"summary.md"),"w").write("\n".join(out))
print("\n".join(out))
PY

echo "----------------------------------------------------------------"
echo "[strengthen] DONE → $RUN"
echo "[strengthen]   summary.md / summary.csv  — headline"
echo "[strengthen]   proposals.json            — what the agent proposed"
echo "[strengthen]   NN-<slot>-<lemma>/        — per-candidate full trace"
[ "$APPLY" != 1 ] && echo "[strengthen]   (dry-run — re-run with --apply to commit accepted candidates)"
