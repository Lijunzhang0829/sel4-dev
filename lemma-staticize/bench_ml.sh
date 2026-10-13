#!/bin/bash
# bench_ml.sh — MULTI-LINE static-ization: for each lemma, pick the M slowest
# search commands (above a threshold), rewrite EACH linearly, then gate all M
# rewrites together (build once + before/after timing of the whole lemma).
#
# Per lemma:
#   phase 1  ab_agent (JVM)  : times every classical command, keeps the top-M by
#            time (>= MIN_LINE_MS), searches a static path for each (X attempts).
#            -> ab-<lemma>.json (results[] each with src_line + path + B_sig)
#   phase 2  gate.evaluate_multiline (poly) : patch all audit-OK lines, build once,
#            time the whole lemma before/after.
#   phase 3  render record.md + dump structured files.
#
# Env: CANDS=json[{thy,lemma,session,line}]  PROPOSER=llm|heuristic  MAX_LINES=3
#      MIN_LINE_MS=50  ATTEMPT_CAP=20(X)  DEPTH=18  TIME_CAP=600  REPS=3
set -u
ROOT=/home/lijun/seL4-docker-main
DIR=$ROOT/tools/seL4-proof-search/Isa-Repl
DC="docker compose -f $ROOT/docker-compose.yml"
KW=/workspace/tools/seL4-proof-search/Isa-Repl/runs/l4v_keywords.json
PROPOSER=${PROPOSER:-llm}; MAX_LINES=${MAX_LINES:-3}; MIN_LINE_MS=${MIN_LINE_MS:-50}
ATTEMPT_CAP=${ATTEMPT_CAP:-20}; DEPTH=${DEPTH:-18}; TIME_CAP=${TIME_CAP:-600}; REPS=${REPS:-3}
CANDS=${CANDS:?need CANDS=path/to/candidates.json}
TS=$(date +%Y%m%d-%H%M%S)
OUTROOT=${OUTROOT:-$ROOT/lemma-staticize/runs}
RUN=$OUTROOT/mlbench-$TS
mkdir -p "$RUN"; cd "$DIR"
cp -f "$CANDS" "$RUN/candidates.json"
echo "[mlbench] $RUN  | MAX_LINES=$MAX_LINES MIN_LINE_MS=$MIN_LINE_MS X(attempt)=$ATTEMPT_CAP PROPOSER=$PROPOSER"
NCAND=$(python3 -c "import json;print(len(json.load(open('$RUN/candidates.json'))))")

for ((IDX=0; IDX<NCAND; IDX++)); do
  read THYREL LEMMA SESSION PLINE < <(python3 - "$RUN/candidates.json" "$IDX" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(c["thy"],c["lemma"],c["session"],c.get("line",1))
PY
)
  SAFE=$(echo "$LEMMA" | tr -c 'A-Za-z0-9_' '_')
  CD="$RUN/$(printf '%02d' $IDX)-$SAFE"; mkdir -p "$CD"
  THY=${L4V_DIR:-/sel4-project/verification/l4v}/$THYREL; PORT=$((25700 + IDX))
  echo "[mlbench $IDX] $SESSION :: $LEMMA ($THYREL)"

  # ---- phase 1: SEARCH all top-M classical lines (JVM) ----
  $DC exec -T l4v bash -lc 'for p in $(pgrep -f "[j]ava -Xmx8g"); do kill -9 $p 2>/dev/null; done; pkill -9 -f "[m]l_server.ML" 2>/dev/null; for q in $(pgrep -f "[p]oly --maxheap"); do kill -9 $q 2>/dev/null; done; sleep 2; true' 2>/dev/null
  $DC exec -T l4v bash -lc "rm -f runs/ab-'$LEMMA'.json runs/ab-'$LEMMA'.md runs/ab-'$LEMMA'.events.jsonl" 2>/dev/null
  rm -f "runs/ab-$LEMMA.json" "runs/ab-$LEMMA.md" "runs/ab-$LEMMA.events.jsonl" 2>/dev/null
  PROP=""
  if [ "$PROPOSER" = "llm" ]; then rm -rf bridge; mkdir -p bridge; python3 proposer_host.py > "$RUN/proposer-$IDX.log" 2>&1 & PROP=$!; fi
  $DC exec -T -e LEMMA="$LEMMA" -e THY="$THY" -e SESSION="$SESSION" \
     -e MAX_LINES="$MAX_LINES" -e MIN_LINE_MS="$MIN_LINE_MS" \
     -e PORT=$PORT -e PROPOSER="$PROPOSER" -e ATTEMPT_CAP="$ATTEMPT_CAP" -e DEPTH="$DEPTH" -e TIME_CAP="$TIME_CAP" -e HAMMER_N=1 \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 ab_agent.py' > "$CD/search.log" 2>&1 & AGENT=$!
  wait $AGENT
  touch bridge/STOP 2>/dev/null; sleep 1; [ -n "$PROP" ] && kill $PROP 2>/dev/null
  $DC exec -T l4v bash -lc 'for p in $(pgrep -f "[j]ava -Xmx8g"); do kill -9 $p 2>/dev/null; done; pkill -9 -f "[m]l_server.ML" 2>/dev/null; for q in $(pgrep -f "[p]oly --maxheap"); do kill -9 $q 2>/dev/null; done; true' 2>/dev/null
  cp -f "runs/ab-$LEMMA.events.jsonl" "$CD/attempts.jsonl" 2>/dev/null || true

  if ! grep -q '\[init ok\]' "$CD/search.log" 2>/dev/null; then
    echo "[mlbench $IDX] SEARCH-INIT-FAILED"
    echo "{\"lemma\":\"$LEMMA\",\"verdict\":\"SEARCH-INIT-FAILED\"}" > "$CD/result.json"
    { echo "# $LEMMA — SEARCH-INIT-FAILED"; echo; echo "JVM never reported [init ok] (flaky session $SESSION / heap)."; } > "$CD/record.md"
    rm -f "$CD/search.log"; continue
  fi

  # ---- phase 2: MULTI-LINE GATE (poly) ----
  $DC exec -T -e ISABELLE_HOME=/workspace/verification/isabelle -e ISAR_EXTRA_KEYWORDS=$KW \
     -e LEMMA="$LEMMA" -e THYREL="$THYREL" -e SESSION="$SESSION" -e REPS="$REPS" \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 - <<PY
import json,os,gate
lem=os.environ["LEMMA"]; thy=os.environ["THYREL"]; sess=os.environ["SESSION"]; reps=int(os.environ["REPS"])
rec={"lemma":lem,"thy":thy,"session":sess,"reps":reps,"verdict":"NO-PATH"}
try:
    rs=json.load(open(f"runs/ab-{lem}.json")).get("results",[])
    # audit-OK lines: (proof_line, path, add_done). add_done iff the ORIGINAL command
    # is a `by (...)` (= apply+done); an intermediate `apply (...)` already has a
    # separate `done`/next command after it, so adding one would double it.
    lp=[(r["src_line"], r["path"], (r.get("orig") or "").strip().startswith("by"))
        for r in rs if r.get("audit") and r.get("path") and r.get("src_line")]
    rec["targets_total"]=len(rs); rec["targets_solved"]=len(lp)
    if lp:
        rec.update(gate.evaluate_multiline(thy,lem,sess,lp,reps=reps))
except Exception as e:
    import traceback; rec["error"]=str(e)[:200]; rec["tb"]=traceback.format_exc()[-400:]
print("MLREC "+json.dumps(rec,ensure_ascii=False))
PY' > "$CD/gate.log" 2>&1
  grep -m1 '^MLREC ' "$CD/gate.log" | sed 's/^MLREC //' > "$CD/result.json" || echo '{"verdict":"GATE-ERROR"}' > "$CD/result.json"
  VERD=$(python3 -c "import json;print(json.load(open('$CD/result.json')).get('verdict','?'))" 2>/dev/null)
  NS=$(python3 -c "import json;r=json.load(open('$CD/result.json'));print(f\"{r.get('targets_solved','?')}/{r.get('targets_total','?')}\")" 2>/dev/null)
  echo "[mlbench $IDX] verdict=$VERD  lines_solved=$NS"

  # ---- phase 3: structured files + record.md ----
  python3 $ROOT/lemma-staticize/scripts/gen_record_ml.py "$CD/result.json" "$CD/attempts.jsonl" "$CD" 2>&1 | tail -1 || true
done

echo "[mlbench] DONE -> $RUN"
