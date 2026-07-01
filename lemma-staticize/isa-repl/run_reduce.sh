#!/bin/bash
# run_reduce.sh — orchestrate ONE search-space-REDUCTION attempt end-to-end, with full recording.
#
#   phase 1  REDUCE  : reduce_agent.py (Isa-REPL JVM on $PORT) keeps the SAME search tactic but
#                      shrinks its work (simp only:/simp del:/targeted simp:/reorder) until it
#                      reaches the SAME state B, FASTER (in-REPL median timing). The host-side
#                      `claude -p` proposer is launched first so every prompt/response is logged.
#   phase 2  VERIFY  : if phase 1 found a FASTER variant, apply it and run check-theory.sh on the
#                      ORIGINAL vs the PATCHED theory -> ground-truth correctness (stock build) AND
#                      whole-theory wall delta (the honest "did the lemma get faster" number).
#
# Everything is recorded into runs/reduce-<ts>/<lemma>/ :
#   reduce-<lemma>.json            (reduce_agent verdict + winning variant + in-REPL timing)
#   reduce-transcript-<lemma>.jsonl(EVERY claude -p prompt/response/claude_secs, per round)
#   proposer_host.log              (host-side claude -p stdout/stderr)
#   search.log / verify.log        (full phase-1 / phase-2 console)
#   gate.json                      (check-theory orig-vs-patched wall + correctness)
#
# Sequential (12GB poly server never coexists with the 8GB REPL JVM). Resumable via progress.json.
#
# Usage:
#   run_reduce.sh [IDX]                 # one candidate (auto-picks next undone if IDX omitted)
#   CAND=<file.json> run_reduce.sh ALL  # sweep every candidate
# Candidate file = JSON list of {thy(rel), lemma, session, line|proof_line, tactic}.
set -u
ROOT=/home/lijun/seL4-docker-main
DIR=$ROOT/tools/seL4-proof-search/Isa-Repl
DC="docker compose -f $ROOT/docker-compose.yml"
CAND=${CAND:-$DIR/../../../lemma-staticize/experiment-candidates/high_value_candidates.json}
L4V_HOST=${L4V_DIR:-/sel4-project/verification/l4v}
TS=$(date +%Y%m%d-%H%M%S)
OUTROOT=$DIR/runs/reduce-$TS
PROG=$DIR/runs/reduce-progress.json
RES=$DIR/runs/reduce-results.jsonl
MAX_ROUNDS=${MAX_ROUNDS:-6}
cd "$DIR"

[ -f "$CAND" ] || { echo "[reduce] candidate file not found: $CAND"; echo "  regenerate with: python3 extract_high_value.py  (reads build command_timings)"; exit 2; }
[ -f "$PROG" ] || echo '{"done":[]}' > "$PROG"

# --- normalise candidate list (handle dict{candidates:[...]} or bare list; line|proof_line) ---
NORM=$(python3 - "$CAND" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
cands=d.get("cases") or d.get("candidates") or d if isinstance(d,dict) else d
if isinstance(cands,dict): cands=list(cands.values())
out=[]
for c in cands:
    out.append({"thy":c["thy"],"lemma":c["lemma"],"session":c["session"],
                "line":c.get("line") or c.get("proof_line") or c.get("hot_line") or 0,
                "tactic":(c.get("tactic") or ""),"file_lines":c.get("file_lines",0),
                "feasible":bool(c.get("feasible",True))})   # feasible flag = the established in-repl/goal-aware decision
json.dump(out,open("/tmp/reduce_cands.json","w"))
nf=sum(1 for x in out if x["feasible"])
print(f"{len(out)} total ({nf} feasible->in-repl, {len(out)-nf} heavy->goal-aware)")
PY
)
echo "[reduce] $NORM feasible candidates from $(basename "$CAND")"

# ESTABLISHED CONCLUSION (high_value feasibility): the Isa-REPL can only init LIGHT sessions.
#   in-REPL    : file<=2900 lines AND session not in {CRefine,Refine,InfoFlowC} -> ms reach-B loop
#   goal-aware : everything else (heavy sessions / big files) -> own-session build + simp_trace,
#                because the REPL deterministically rebuilds the heap (34min) / gateway-times-out / OOMs.
HEAVY_SESSIONS="CRefine Refine InfoFlowC"
pick_method() {  # args: session file_lines -> echoes "in-repl" | "goal-aware"
  local s=$1 fl=${2:-0}
  for h in $HEAVY_SESSIONS; do [ "$s" = "$h" ] && { echo goal-aware; return; }; done
  [ "${fl:-0}" -gt 2900 ] && { echo goal-aware; return; }
  echo in-repl
}

run_one() {
  local IDX=$1
  read THYREL LEMMA SESSION PLINE FLINES FEAS < <(python3 - "$IDX" <<'PY'
import json,sys
c=json.load(open("/tmp/reduce_cands.json"))[int(sys.argv[1])]
print(c["thy"],c["lemma"],c["session"],c["line"],c.get("file_lines",0),int(c.get("feasible",True)))
PY
)
  local TACTIC; TACTIC=$(python3 -c "import json;print(json.load(open('/tmp/reduce_cands.json'))[$IDX]['tactic'])")
  # method = the established conclusion: feasible flag (session+file_lines) decides in-repl vs goal-aware
  local METHOD; if [ "$FEAS" = "1" ]; then METHOD=$(pick_method "$SESSION" "$FLINES"); else METHOD=goal-aware; fi
  local OUT=$OUTROOT/$LEMMA-L$PLINE; mkdir -p "$OUT"
  local THY=$L4V_HOST/$THYREL
  local PORT=$((26200 + IDX))
  local TB64; TB64=$(printf '%s' "$TACTIC" | base64 -w0)
  echo "[reduce $IDX] $SESSION :: $LEMMA  ($THYREL:$PLINE)  tactic='$TACTIC'  METHOD=$METHOD"
  echo "[reduce $IDX] -> $OUT"

  if [ "$METHOD" = "goal-aware" ]; then
    echo "[reduce $IDX] HEAVY session -> goal-aware (own-session build + simp_trace; claude decides, recorded)"
    local OUTC=/workspace/tools/seL4-proof-search/Isa-Repl/runs/reduce-$TS/$LEMMA-L$PLINE
    $DC exec -T l4v bash -lc 'for p in $(pgrep -f "java -Xmx8g"); do kill -9 $p 2>/dev/null; done; true' 2>/dev/null
    rm -rf bridge; mkdir -p bridge
    python3 proposer_host.py > "$OUT/proposer_host.log" 2>&1 &  local PROP=$!
    $DC exec -T \
       -e THY="$THY" -e SESSION="$SESSION" -e TARGET_LINE="$PLINE" -e OUT_DIR="$OUTC" \
       -e MAX_ROUNDS=$MAX_ROUNDS -e ISABELLE_HOME=/workspace/verification/isabelle -e L4V_DIR="$L4V_HOST" \
       l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 goal_aware_reduce.py' \
       > "$OUT/search.log" 2>&1
    touch bridge/STOP 2>/dev/null; sleep 1; kill $PROP 2>/dev/null
    local V; V=$(python3 -c "import json;print(json.load(open('$OUT/result.json')).get('verdict','ERR'))" 2>/dev/null || echo ERR)
    python3 - "$OUT/result.json" "$OUT/gate.json" <<'PY'
import json,sys
try: r=json.load(open(sys.argv[1]))
except Exception: r={"verdict":"ERR"}
g={"verdict":r.get("verdict","ERR"),"method":"goal-aware","verified":r.get("verdict")=="FASTER",
   "wall_delta_pct":r.get("wall_delta_pct"),"line_delta_pct":r.get("line_delta_pct")}
json.dump(g,open(sys.argv[2],"w"))
PY
    echo "[reduce $IDX] goal-aware verdict=$V"
    python3 - "$IDX" "$THYREL" "$LEMMA" "$SESSION" "$PLINE" "$OUT/gate.json" "$RES" "$PROG" <<'PY'
import json,sys
idx,thy,lem,sess,pl,gatef,resf,progf=sys.argv[1:9]
g=json.load(open(gatef)); row={"idx":int(idx),"thy":thy,"lemma":lem,"session":sess,"line":int(pl),**g}
open(resf,"a").write(json.dumps(row,ensure_ascii=False)+"\n")
p=json.load(open(progf)); p["done"]=sorted(set(p["done"])|{int(idx)}); json.dump(p,open(progf,"w"))
print("REDUCE_REC "+json.dumps(row,ensure_ascii=False))
PY
    return 0
  fi

  # ---- phase 1: REDUCE (host claude -p proposer + container REPL agent) ----
  $DC exec -T l4v bash -lc 'for p in $(pgrep -f "java -Xmx8g"); do kill -9 $p 2>/dev/null; done; true' 2>/dev/null
  rm -rf bridge; mkdir -p bridge
  python3 proposer_host.py > "$OUT/proposer_host.log" 2>&1 &  local PROP=$!
  $DC exec -T \
     -e THY="$THY" -e LEMMA="$LEMMA" -e SESSION="$SESSION" -e TARGET_SUBSTR_B64="$TB64" \
     -e PORT=$PORT -e MAX_ROUNDS=$MAX_ROUNDS -e RESULT_TAG="" -e L4V_DIR="$L4V_HOST" \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 reduce_agent.py' \
     > "$OUT/search.log" 2>&1
  touch bridge/STOP 2>/dev/null; sleep 1; kill $PROP 2>/dev/null
  $DC exec -T l4v bash -lc 'for p in $(pgrep -f "java -Xmx8g"); do kill -9 $p 2>/dev/null; done; pkill -9 -f ml_server.ML 2>/dev/null; true' 2>/dev/null

  # collect the agent's own records (result + per-round claude transcript)
  cp -f "$DIR/runs/reduce-$LEMMA.json"            "$OUT/" 2>/dev/null
  cp -f "$DIR/runs/reduce-transcript-$LEMMA.jsonl" "$OUT/" 2>/dev/null

  local VERDICT; VERDICT=$(python3 -c "import json;print(json.load(open('$OUT/reduce-$LEMMA.json')).get('verdict','ERR'))" 2>/dev/null || echo ERR)
  echo "[reduce $IDX] phase1 verdict=$VERDICT"

  # ---- phase 2: ground-truth check-theory wall (orig vs patched), only if a FASTER variant ----
  local GATE='{"verdict":"'$VERDICT'","verified":false}'
  if [ "$VERDICT" = "FASTER" ]; then
    # build a check-theory patch file (START END / replacement) from the winning variant
    python3 - "$OUT/reduce-$LEMMA.json" "$PLINE" "$OUT/variant.patch" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); pl=int(sys.argv[2]); pf=sys.argv[3]
var=d.get("path") or d.get("accepted") or ""
# variant may be a multi-line apply-script; replace the single slow line PLINE with it.
open(pf,"w").write(f"{pl} {pl}\n{var}\n---\n")
print("[patch]",repr(var)[:80])
PY
    $DC exec -T -e ISABELLE_HOME=/workspace/verification/isabelle -e L4V_DIR="$L4V_HOST" \
       l4v bash -lc '
         set -e; cd /workspace/tools/seL4-proof-search/Isa-Repl
         CT=./check_theory_selfqual.sh
         echo "=== baseline (orig) ==="; bash $CT '"$THY"' '"$SESSION"' 2>&1 | grep -E "OK|FAILED" | tail -1
         echo "=== variant (patched) ==="; bash $CT '"$THY"' '"$SESSION"' --patch /workspace/tools/seL4-proof-search/Isa-Repl/runs/reduce-'"$TS"'/'"$LEMMA"'-L'"$PLINE"'/variant.patch 2>&1 | grep -E "OK|FAILED" | tail -1
       ' > "$OUT/verify.log" 2>&1
    GATE=$(python3 - "$OUT/verify.log" "$VERDICT" <<'PY'
import re,sys,json
t=open(sys.argv[1]).read()
ms=[int(m) for m in re.findall(r"OK \((\d+)ms\)",t)]
fail="FAILED" in t
res={"verdict":sys.argv[2],"verified":(len(ms)>=2 and not fail),
     "orig_ms":ms[0] if ms else None,"variant_ms":ms[1] if len(ms)>1 else None,
     "build_failed":fail}
if len(ms)>=2 and ms[0]: res["wall_delta_pct"]=round(100*(ms[0]-ms[1])/ms[0],1)
print(json.dumps(res))
PY
)
  fi
  echo "$GATE" > "$OUT/gate.json"

  # ---- record result row + mark progress ----
  python3 - "$IDX" "$THYREL" "$LEMMA" "$SESSION" "$PLINE" "$OUT/gate.json" "$RES" "$PROG" <<'PY'
import json,sys
idx,thy,lem,sess,pl,gatef,resf,progf=sys.argv[1:9]
gate=json.load(open(gatef))
row={"idx":int(idx),"thy":thy,"lemma":lem,"session":sess,"line":int(pl),**gate}
open(resf,"a").write(json.dumps(row,ensure_ascii=False)+"\n")
p=json.load(open(progf)); p["done"]=sorted(set(p["done"])|{int(idx)}); json.dump(p,open(progf,"w"))
print("REDUCE_REC "+json.dumps(row,ensure_ascii=False))
PY
}

# --- driver: single IDX, ALL, or auto-next ---
if [ "${1:-}" = "ALL" ]; then
  for ((i=0;i<NORM;i++)); do
    done_i=$(python3 -c "import json;print(int($i in json.load(open('$PROG'))['done']))")
    [ "$done_i" = "1" ] && { echo "[reduce $i] skip (done)"; continue; }
    run_one "$i"
  done
  echo "[reduce] ALL done -> $RES"
else
  if [ $# -ge 1 ]; then IDX=$1; else
    IDX=$(python3 -c "import json;d=json.load(open('$PROG'))['done'];print(next((i for i in range($NORM) if i not in d),-1))")
  fi
  [ "$IDX" = "-1" ] && { echo "[reduce] ALL DONE"; exit 7; }
  run_one "$IDX"
fi
