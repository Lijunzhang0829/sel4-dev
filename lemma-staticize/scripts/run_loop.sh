#!/bin/bash
# One A->B static-ization attempt, end-to-end, with the RELIABLE gate.
#   phase 1 SEARCH  : ab_agent.py (JVM/Isa-REPL) finds an audit-passing static
#                     path using per-transition proof state. JVM EXITS after.
#   phase 2 GATE    : gate.py (no JVM) — check-theory.sh build (correctness) +
#                     `isar timing --lemma` orig-vs-patched (reliable speed).
# Sequential so the 12GB poly server never coexists with the 8GB JVM heap.
# Resumable: skips candidates already in loop-progress.json. Usage: run_loop.sh [idx]
set -u
ROOT=/home/lijun/seL4-docker-main
DIR=$ROOT/tools/seL4-proof-search/Isa-Repl
DC="docker compose -f $ROOT/docker-compose.yml"
CAND=${CAND:-$DIR/runs/loop_cands.json}
PROG=$DIR/runs/loop-progress.json
RES=$DIR/runs/loop-results.jsonl
KW=/workspace/tools/seL4-proof-search/Isa-Repl/runs/l4v_keywords.json
PROPOSER=${PROPOSER:-llm}   # set PROPOSER=heuristic to skip the host LLM proposer
cd "$DIR"
[ -f "$PROG" ] || echo '{"done":[]}' > "$PROG"
if [ $# -ge 1 ]; then IDX=$1; else
  IDX=$(python3 - "$CAND" "$PROG" <<'PY'
import json,sys
cand=json.load(open(sys.argv[1])); done=set(json.load(open(sys.argv[2]))["done"])
for i in range(len(cand)):
    if i not in done: print(i); break
else: print(-1)
PY
)
fi
[ "$IDX" = "-1" ] && { echo "[loop] ALL DONE"; exit 7; }

read THYREL LEMMA SESSION PLINE < <(python3 - "$CAND" "$IDX" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(c["thy"],c["lemma"],c["session"],c["line"])
PY
)
echo "[loop $IDX] $SESSION :: $LEMMA ($THYREL:$PLINE)"
THY=${L4V_DIR:-/sel4-project/verification/l4v}/$THYREL
PORT=$((25900 + IDX))
# Focus the search on ONLY the DB-flagged slow line (its tactic text), so a slow
# LLM search finishes within budget instead of searching every classical line.
TACTIC=$(python3 -c "import json;print((json.load(open('$CAND'))[$IDX].get('tactic') or '')[:50])")

# ---- phase 1: SEARCH (JVM) ----
$DC exec -T l4v bash -lc 'for p in $(pgrep -f "java -Xmx8g"); do kill -9 $p 2>/dev/null; done; true' 2>/dev/null
PROP=""
if [ "$PROPOSER" = "llm" ]; then
  rm -rf bridge; mkdir -p bridge
  python3 proposer_host.py > runs/proposer_host.log 2>&1 &  PROP=$!
fi
$DC exec -T -e LEMMA="$LEMMA" -e THY="$THY" -e SESSION="$SESSION" -e TARGET_SUBSTR="$TACTIC" \
   -e PORT=$PORT -e PROPOSER="$PROPOSER" -e ATTEMPT_CAP=30 -e DEPTH=24 -e TIME_CAP=2400 -e HAMMER_N=1 \
   l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 ab_agent.py' > /tmp/loop_search_$IDX.log 2>&1 &  AGENT=$!
wait $AGENT
touch bridge/STOP 2>/dev/null; sleep 1; [ -n "$PROP" ] && kill $PROP 2>/dev/null
# JVM is now gone — safe to boot the 12GB timing server.
$DC exec -T l4v bash -lc 'for p in $(pgrep -f "java -Xmx8g"); do kill -9 $p 2>/dev/null; done; pkill -9 -f ml_server.ML 2>/dev/null; true' 2>/dev/null

# ---- phase 2: GATE (reliable timing + correctness), if a path was found ----
$DC exec -T -e ISABELLE_HOME=/workspace/verification/isabelle -e ISAR_EXTRA_KEYWORDS=$KW \
   -e LEMMA="$LEMMA" -e THYREL="$THYREL" -e SESSION="$SESSION" -e PLINE="$PLINE" -e IDX="$IDX" \
   l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 - <<PY
import json,os,gate
idx=os.environ["IDX"]; lem=os.environ["LEMMA"]; thy=os.environ["THYREL"]
sess=os.environ["SESSION"]; pl=int(os.environ["PLINE"])
abj=f"runs/ab-{lem}.json"
rec={"idx":int(idx),"lemma":lem,"thy":thy,"session":sess,"line":pl,
     "path":None,"audit":False,"verdict":"NO-PATH"}
try:
    rs=json.load(open(abj)).get("results",[])
    win=next((r for r in rs if r.get("audit") and r.get("path")),None)
    if win:
        rec["path"]=win["path"]; rec["audit"]=True
        v=gate.evaluate(thy,lem,sess,pl,win["path"],reps=int(os.environ.get("REPS","2")))
        rec.update(v)
except Exception as e:
    rec["error"]=str(e)[:120]
print("LOOP_REC "+json.dumps(rec,ensure_ascii=False))
open("runs/loop-results.jsonl","a").write(json.dumps(rec,ensure_ascii=False)+"\n")
p=json.load(open("runs/loop-progress.json")); p["done"]=sorted(set(p["done"])|{int(idx)})
json.dump(p,open("runs/loop-progress.json","w"))
PY' 2>&1 | tee /tmp/loop_gate_$IDX.log | grep -E "LOOP_REC|verdict|Traceback" | tail -3

echo "[loop $IDX] done"
