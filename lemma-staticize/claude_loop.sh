#!/bin/bash
# claude_loop.sh — single-entry claude -p rewrite agent (CLEAN + SAFE).
#   per lemma:  react_agent loop (claude -p proposes ONE tactic per step, no Bash,
#               no --dangerously-skip-permissions) -> a static PATH that reaches B
#               -> gate.py BUILD-verify (don't trust the loop's claim) -> 5-file record.
#
# claude only ever sees {goal, facts, history} and returns the next tactic; the Python
# loop owns the REPL (apply/observe) and the gate owns correctness. Only external tool
# is the gate. Robust JVM/socket handling (java glob, read_timeout, poly pre-clean).
#
# Env: CANDS=json[{thy,lemma,session,line}]  PROPOSER_MODEL=haiku|sonnet  REPS=2
#      MAX_STEPS=10  TIME_CAP=300
set -u
ROOT=/home/lijun/seL4-docker-main
DIR=$ROOT/tools/seL4-proof-search/Isa-Repl
DC="docker compose -f $ROOT/docker-compose.yml"
KW=/workspace/tools/seL4-proof-search/Isa-Repl/runs/l4v_keywords.json
CANDS=${CANDS:?need CANDS=path/to/candidates.json}
MODEL=${PROPOSER_MODEL:-haiku}; REPS=${REPS:-2}; MAX_STEPS=${MAX_STEPS:-10}; TIME_CAP=${TIME_CAP:-300}
TS=$(date +%Y%m%d-%H%M%S)
OUTROOT=${OUTROOT:-$ROOT/lemma-staticize/runs}
RUN=$OUTROOT/claude-$TS; mkdir -p "$RUN"; cd "$DIR"
cp -f "$CANDS" "$RUN/candidates.json"
echo "[claude_loop] $RUN | model=$MODEL MAX_STEPS=$MAX_STEPS REPS=$REPS"
NCAND=$(python3 -c "import json;print(len(json.load(open('$RUN/candidates.json'))))")

# helper: full process pre-clean (java 8g + gate poly 12g + ml_server) then settle
preclean(){ $DC exec -T l4v bash -lc 'for p in $(pgrep -f "[j]ava -Xmx8g"); do kill -9 $p 2>/dev/null; done; for q in $(pgrep -f "[p]oly --maxheap"); do kill -9 $q 2>/dev/null; done; pkill -9 -f "[m]l_server.ML" 2>/dev/null; sleep 2; true' 2>/dev/null; }

for ((IDX=0; IDX<NCAND; IDX++)); do
  read THYREL LEMMA SESSION PLINE < <(python3 - "$RUN/candidates.json" "$IDX" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(c["thy"],c["lemma"],c["session"],c.get("line",1))
PY
)
  SAFE=$(echo "$LEMMA" | tr -c 'A-Za-z0-9_' '_')
  CD="$RUN/$(printf '%02d' $IDX)-$SAFE"; mkdir -p "$CD"
  # /sel4-project = the path the heaps were fingerprinted with; using it everywhere
  # (REPL init AND gate isar/build) avoids triggering a scala-isabelle session rebuild.
  L4VDIR=/sel4-project/verification/l4v
  THY=$L4VDIR/$THYREL; PORT=$((25750 + IDX))
  echo "[claude_loop $IDX] $SESSION :: $LEMMA"

  # ---- phase 1: claude -p LOOP (react_agent + proposer_host) ----
  preclean
  $DC exec -T l4v bash -lc "rm -f runs/react-'$LEMMA'.json runs/react-'$LEMMA'.events.jsonl" 2>/dev/null
  rm -rf bridge; mkdir -p bridge
  PROPOSER_MODEL=$MODEL python3 proposer_host.py > "$RUN/proposer-$IDX.log" 2>&1 &  PROP=$!
  sleep 2
  $DC exec -T -e THY="$THY" -e LEMMA="$LEMMA" -e SESSION="$SESSION" -e PORT=$PORT -e L4V_DIR="$L4VDIR" \
     -e MAX_STEPS="$MAX_STEPS" -e MAX_FAILS=4 -e TIME_CAP="$TIME_CAP" \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 react_agent.py' > "$CD/loop.log" 2>&1
  touch bridge/STOP 2>/dev/null; sleep 1; kill "$PROP" 2>/dev/null
  $DC exec -T l4v bash -lc "cp -f runs/react-'$LEMMA'.events.jsonl '$CD/attempts.jsonl'" 2>/dev/null || true
  cp -f "runs/react-$LEMMA.events.jsonl" "$CD/attempts.jsonl" 2>/dev/null || true

  if ! grep -q '\[init ok\]' "$CD/loop.log" 2>/dev/null; then
    echo "[claude_loop $IDX] SEARCH-INIT-FAILED"
    echo "{\"lemma\":\"$LEMMA\",\"verdict\":\"SEARCH-INIT-FAILED\"}" > "$CD/result.json"
    { echo "# $LEMMA — SEARCH-INIT-FAILED"; echo; tail -6 "$CD/loop.log"; } > "$CD/record.md"
    continue
  fi

  # ---- phase 2: GATE build-verify the claude-found path (don't trust the loop) ----
  preclean
  $DC exec -T -e ISABELLE_HOME=/workspace/verification/isabelle -e ISAR_EXTRA_KEYWORDS=$KW -e L4V_DIR="$L4VDIR" \
     -e LEMMA="$LEMMA" -e THYREL="$THYREL" -e SESSION="$SESSION" -e PLINE="$PLINE" -e REPS="$REPS" \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 - <<PY
import json,os,gate
lem=os.environ["LEMMA"]; thy=os.environ["THYREL"]; sess=os.environ["SESSION"]
pl=int(os.environ["PLINE"]); reps=int(os.environ["REPS"])
rec={"lemma":lem,"thy":thy,"session":sess,"line":pl,"verdict":"NO-PATH","path":None,"audit":False}
try:
    r=json.load(open(f"runs/react-{lem}.json"))
    rec["loop_verdict"]=r.get("verdict"); path=r.get("path") if r.get("audit") else None
    if path:
        rec["path"]=path; rec["audit"]=True
        rec.update(gate.evaluate(thy,lem,sess,pl,path,reps=reps))
except Exception as e:
    import traceback; rec["error"]=str(e)[:200]; rec["tb"]=traceback.format_exc()[-300:]
print("CLREC "+json.dumps(rec,ensure_ascii=False))
PY' > "$CD/gate.log" 2>&1
  grep -m1 '^CLREC ' "$CD/gate.log" | sed 's/^CLREC //' > "$CD/result.json" || echo '{"verdict":"GATE-ERROR"}' > "$CD/result.json"
  VERD=$(python3 -c "import json;print(json.load(open('$CD/result.json')).get('verdict','?'))" 2>/dev/null)
  echo "[claude_loop $IDX] verdict=$VERD"

  # ---- phase 3: render record.md (claude reasoning steps + before/after + timing) ----
  python3 $ROOT/lemma-staticize/scripts/gen_record_claude.py "$CD/result.json" "$CD/attempts.jsonl" "$CD" 2>&1 | tail -1 || true
done
preclean
echo "[claude_loop] DONE -> $RUN"
