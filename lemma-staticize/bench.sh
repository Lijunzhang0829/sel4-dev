#!/bin/bash
# bench.sh — reproducible, fully-traced before/after benchmark of A->B
# static-ization over a candidate set of heavy-automation seL4 lemmas.
#
# WHY THIS EXISTS: the rewrite attempts used to happen inside a chat session,
# so the *process* (what was tried, how many attempts, why it did/didn't work)
# was never captured as a reviewable artifact. This script drives the SAME
# proven two-phase machinery as run_loop.sh — phase 1 ab_agent.py search,
# phase 2 gate.py correctness+timing — but collects EVERY stream into one
# timestamped deliverable directory a reviewer (or paper appendix) can audit.
#
# Pipeline per lemma:
#   1. SEARCH  ab_agent.py (JVM/Isa-REPL, heuristic proposer by default) finds an
#              audit-passing STATIC path (rule/erule/simp only:; no auto/blast/...).
#              -> search.log (full per-attempt trace) + ab-<lemma>.{json,md}
#   2. GATE    gate.py: check-theory.sh build (correctness) + `isar timing --lemma`
#              orig-vs-patched, REPS reps each. -> gate.log + result.json
#   Phases are sequential so the 12GB timing server never coexists with the 8GB JVM.
#
# Usage:
#   ./bench.sh                 # default: top 3 by recoverable, heuristic, 3 reps
#   N=10 VIEW=by_elapsed ./bench.sh
#   N=5 PROPOSER=llm REPS=2 ./bench.sh     # llm needs proposer_host.py creds on host
#
# Env knobs:
#   N=3                 number of candidate lemmas
#   VIEW=by_recoverable selection ranking: by_recoverable (elapsed*search_share)
#                       or by_elapsed (raw self time)
#   MIN_MS=100          drop candidates whose orig elapsed is below this (timer noise)
#   PROPOSER=heuristic  ab_agent proposer: heuristic (no LLM, reproducible) or llm
#   REPS=3              timing repetitions per lemma in the gate
set -u
ROOT=/home/lijun/seL4-docker-main
DIR=$ROOT/tools/seL4-proof-search/Isa-Repl
DC="docker compose -f $ROOT/docker-compose.yml"
KW=/workspace/tools/seL4-proof-search/Isa-Repl/runs/l4v_keywords.json
RANKING=$DIR/runs/timing/ranking.json

N=${N:-3}
VIEW=${VIEW:-by_recoverable}
MIN_MS=${MIN_MS:-100}
PROPOSER=${PROPOSER:-heuristic}
REPS=${REPS:-3}
# ab_agent search budgets (overridable; defaults match the validated run_loop.sh)
ATTEMPT_CAP=${ATTEMPT_CAP:-30}
DEPTH=${DEPTH:-24}
TIME_CAP=${TIME_CAP:-2400}

TS=$(date +%Y%m%d-%H%M%S)
# All run output (per-lemma attempt logs, summaries) lands under the self-contained
# lemma-staticize archive by default. Override with OUTROOT=... if needed.
OUTROOT=${OUTROOT:-$ROOT/lemma-staticize/runs}
RUN=$OUTROOT/bench-$TS
mkdir -p "$RUN"
cd "$DIR"

echo "[bench] run dir: $RUN"
echo "[bench] N=$N VIEW=$VIEW MIN_MS=$MIN_MS PROPOSER=$PROPOSER REPS=$REPS"

# ---- record exact invocation so the run is re-runnable ----
cat > "$RUN/command.sh" <<EOF
#!/bin/bash
# Reproduce this bench run.
cd $DIR
N=$N VIEW=$VIEW MIN_MS=$MIN_MS PROPOSER=$PROPOSER REPS=$REPS ./bench.sh
EOF
chmod +x "$RUN/command.sh"

# ---- phase 0: SELECT candidates ----
# CANDS=<file> supplies a ready-made candidate list (json array of
# {thy,lemma,session,line,tactic}), e.g. a search-dominated set; otherwise select
# from the reliable-timer ranking by the chosen view.
if [ -n "${CANDS:-}" ]; then
  cp -f "$CANDS" "$RUN/candidates.json"
  echo "[select] using provided candidate set: $CANDS"
  python3 -c "import json;[print(f\"  [{i}] {c['session']:8s} {c['lemma'][:40]:40s} {c.get('tactic','')}\") for i,c in enumerate(json.load(open('$RUN/candidates.json')))]"
else
python3 - "$RANKING" "$N" "$VIEW" "$MIN_MS" "$RUN/candidates.json" <<'PY'
import json, sys
ranking, n, view, min_ms, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4]), sys.argv[5]
rows = json.load(open(ranking))
# classical-search proportion (mirrors select_top30.py): how much of the line's
# time is plausibly recoverable by removing classical search.
WEIGHT = {"metis":1.0,"blast":1.0,"fast":1.0,"meson":1.0,
          "force":0.55,"fastforce":0.55,"first":0.5,
          "auto":0.30,"safe":0.30,"clarsimp":0.20}
def weight(tac, proof):
    w = WEIGHT.get((tac or "").strip(), 0.0)
    # explicit simp: / add: lists mean more of the time is rewriting, not search
    mods = (proof or "").count("simp:") + (proof or "").count("add:")
    return max(0.1, w - 0.05*mods) if w else 0.0
cands = []
seen = set()
for r in rows:
    tac = (r.get("tactic") or "").strip()
    if tac not in WEIGHT:                      # not classical-search-bearing
        continue
    if not r.get("matched"):                   # timing didn't bind to this line
        continue
    el = float(r.get("elapsed_ms") or 0)
    if el < min_ms:
        continue
    key = (r["thy"], r["lemma"])
    if key in seen:
        continue
    seen.add(key)
    w = weight(tac, r.get("proof"))
    cands.append({"thy": r["thy"], "lemma": r["lemma"], "session": r["session"],
                  "line": r["line"], "tactic": tac, "elapsed_ms": el,
                  "search_weight": round(w, 2), "recoverable_ms": round(el*w, 1)})
keyf = "recoverable_ms" if view == "by_recoverable" else "elapsed_ms"
cands.sort(key=lambda c: c[keyf], reverse=True)
cands = cands[:n]
json.dump(cands, open(out, "w"), indent=1, ensure_ascii=False)
print(f"[select] {len(cands)} candidates (view={view}):")
for i, c in enumerate(cands):
    print(f"  [{i}] {c['session']:8s} {c['lemma'][:40]:40s} {c['tactic']:10s} "
          f"elapsed={c['elapsed_ms']:.0f}ms recoverable={c['recoverable_ms']:.0f}ms")
PY
fi
[ -s "$RUN/candidates.json" ] || { echo "[bench] no candidates selected"; exit 1; }
NCAND=$(python3 -c "import json;print(len(json.load(open('$RUN/candidates.json'))))")

# ---- per-candidate loop ----
for ((IDX=0; IDX<NCAND; IDX++)); do
  read THYREL LEMMA SESSION PLINE TACTIC < <(python3 - "$RUN/candidates.json" "$IDX" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(c["thy"],c["lemma"],c["session"],c["line"],(c.get("tactic") or "")[:50])
PY
)
  SAFE=$(echo "$LEMMA" | tr -c 'A-Za-z0-9_' '_')
  CD="$RUN/$(printf '%02d' $IDX)-$SAFE"
  mkdir -p "$CD"
  THY=${L4V_DIR:-/sel4-project/verification/l4v}/$THYREL
  PORT=$((25900 + IDX))
  echo "[bench $IDX] $SESSION :: $LEMMA ($THYREL:$PLINE) tac=$TACTIC"

  # ---- phase 1: SEARCH (JVM) — full attempt trace to search.log ----
  # Reclaim BOTH the 8GB search JVM and the 12GB gate poly server (ml_server) from
  # any prior run/candidate, then pause so the kernel frees the pages — otherwise a
  # leftover 12GB ml_server starves the new 8GB JVM and _initializeRepl dies with
  # "Answer from Java side is empty" (empty search.log, NO-PATH-looking false fail).
  $DC exec -T l4v bash -lc 'for p in $(pgrep -f "[j]ava -Xmx8g"); do kill -9 $p 2>/dev/null; done; pkill -9 -f "[m]l_server.ML" 2>/dev/null; for q in $(pgrep -f "[p]oly --maxheap"); do kill -9 $q 2>/dev/null; done; sleep 2; true' 2>/dev/null
  # delete any PRIOR ab-LEMMA outputs so a failed/aborted search this run can't be
  # silently masked by a stale file from a previous run (which would copy days-old,
  # pre-instrumentation events into the bench dir — exactly what hid node events).
  $DC exec -T l4v bash -lc "rm -f runs/ab-'$LEMMA'.json runs/ab-'$LEMMA'.md runs/ab-'$LEMMA'.events.jsonl" 2>/dev/null
  rm -f "runs/ab-$LEMMA.json" "runs/ab-$LEMMA.md" "runs/ab-$LEMMA.events.jsonl" 2>/dev/null
  PROP=""
  if [ "$PROPOSER" = "llm" ]; then
    rm -rf bridge; mkdir -p bridge
    python3 proposer_host.py > "$RUN/proposer-$IDX.log" 2>&1 &  PROP=$!
  fi
  $DC exec -T -e LEMMA="$LEMMA" -e THY="$THY" -e SESSION="$SESSION" -e TARGET_SUBSTR="$TACTIC" \
     -e PORT=$PORT -e PROPOSER="$PROPOSER" -e ATTEMPT_CAP="$ATTEMPT_CAP" -e DEPTH="$DEPTH" -e TIME_CAP="$TIME_CAP" -e HAMMER_N=1 \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 ab_agent.py' \
     > "$CD/search.log" 2>&1 &  AGENT=$!
  wait $AGENT
  touch bridge/STOP 2>/dev/null; sleep 1; [ -n "$PROP" ] && kill $PROP 2>/dev/null
  # JVM gone — safe to boot the 12GB timing server.
  $DC exec -T l4v bash -lc 'for p in $(pgrep -f "[j]ava -Xmx8g"); do kill -9 $p 2>/dev/null; done; pkill -9 -f "[m]l_server.ML" 2>/dev/null; for q in $(pgrep -f "[p]oly --maxheap"); do kill -9 $q 2>/dev/null; done; true' 2>/dev/null

  # harvest the agent's per-attempt event stream (node events carry facts/menu/
  # llm_ranked/llm_thought; attempt events carry the tactic + fail_full). This is the
  # ONE raw search artifact we keep; the human-readable tree is rendered into record.md.
  cp -f "runs/ab-$LEMMA.events.jsonl"  "$CD/attempts.jsonl"   2>/dev/null || true

  # guard: if the JVM never reported "[init ok]" the search never really ran (OOM /
  # heap collision) — flag it explicitly rather than letting it look like NO-PATH.
  if ! grep -q '\[init ok\]' "$CD/search.log" 2>/dev/null; then
    echo "[bench $IDX] SEARCH-INIT-FAILED (no [init ok] — JVM did not boot)"
    echo "{\"idx\":$IDX,\"lemma\":\"$LEMMA\",\"thy\":\"$THYREL\",\"session\":\"$SESSION\",\"line\":$PLINE,\"verdict\":\"SEARCH-INIT-FAILED\",\"audit\":false}" > "$CD/result.json"
    { echo "# $LEMMA — SEARCH-INIT-FAILED"; echo;
      echo "Agent JVM never reported \`[init ok]\` (OOM / heap collision / flaky session \`$SESSION\`).";
      echo "Nothing was searched. Re-run on a clean environment."; echo;
      echo '## agent stderr tail'; echo '```'; tail -8 "$CD/search.log" 2>/dev/null; echo '```'; } > "$CD/record.md"
    rm -f "$CD/search.log"
    continue
  fi

  # ---- phase 2: GATE (correctness build + before/after timing, REPS reps) ----
  $DC exec -T -e ISABELLE_HOME=/workspace/verification/isabelle -e ISAR_EXTRA_KEYWORDS=$KW \
     -e LEMMA="$LEMMA" -e THYREL="$THYREL" -e SESSION="$SESSION" -e PLINE="$PLINE" \
     -e IDX="$IDX" -e REPS="$REPS" \
     l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 - <<PY
import json,os,gate
idx=int(os.environ["IDX"]); lem=os.environ["LEMMA"]; thy=os.environ["THYREL"]
sess=os.environ["SESSION"]; pl=int(os.environ["PLINE"]); reps=int(os.environ["REPS"])
rec={"idx":idx,"lemma":lem,"thy":thy,"session":sess,"line":pl,
     "path":None,"audit":False,"verdict":"NO-PATH","reps":reps}
try:
    rs=json.load(open(f"runs/ab-{lem}.json")).get("results",[])
    win=next((r for r in rs if r.get("audit") and r.get("path")),None)
    if win:
        rec["path"]=win["path"]; rec["audit"]=True
        rec.update(gate.evaluate(thy,lem,sess,pl,win["path"],reps=reps))
except Exception as e:
    rec["error"]=str(e)[:200]
print("BENCH_REC "+json.dumps(rec,ensure_ascii=False))
PY' > "$CD/gate.log" 2>&1
  # extract the BENCH_REC line into result.json
  grep -m1 '^BENCH_REC ' "$CD/gate.log" | sed 's/^BENCH_REC //' > "$CD/result.json" \
    || echo '{"verdict":"GATE-ERROR"}' > "$CD/result.json"
  VERD=$(python3 -c "import json;print(json.load(open('$CD/result.json')).get('verdict','?'))" 2>/dev/null)
  echo "[bench $IDX] verdict=$VERD"

  # render the human one-pager (record.md) + timing.json from the gate result + events
  python3 gen_record.py "$CD" 2>/dev/null || echo "[bench $IDX] (record gen skipped)"
  # keep ONLY the 5-file set: record.md / timing.json / attempts.jsonl / result.json / gate.log
  rm -f "$CD/search.log"
done

# ---- summary: CSV + markdown, success rate / speedup / failures ----
python3 - "$RUN" <<'PY'
import json, os, glob, statistics, csv, sys
run = sys.argv[1]
rows = []
for cd in sorted(glob.glob(os.path.join(run, "[0-9]*-*"))):
    rp = os.path.join(cd, "result.json")
    if not os.path.exists(rp):
        continue
    try:
        r = json.load(open(rp))
    except Exception:
        continue
    rows.append(r)

with open(os.path.join(run, "summary.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["idx","lemma","session","line","verdict","audit",
                "orig_ms","static_ms","delta_pct","build_ms","path"])
    for r in rows:
        w.writerow([r.get("idx"), r.get("lemma"), r.get("session"), r.get("line"),
                    r.get("verdict"), r.get("audit"), r.get("orig_ms"),
                    r.get("static_ms"), r.get("delta_pct"), r.get("build_ms"),
                    " ; ".join(r.get("path") or [])])

total = len(rows)
found = sum(1 for r in rows if r.get("audit"))
accept = [r for r in rows if r.get("verdict") == "ACCEPT"]
deltas = [r["delta_pct"] for r in accept if r.get("delta_pct") is not None]
lines = []
lines.append(f"# bench summary — {os.path.basename(run)}\n")
lines.append(f"- candidates: **{total}**")
lines.append(f"- static path found (audit-clean): **{found}/{total}**")
lines.append(f"- ACCEPT (correct AND faster): **{len(accept)}/{total}**")
if deltas:
    lines.append(f"- median speedup on ACCEPTs: **{statistics.median(deltas):.1f}%** "
                 f"(delta_pct; negative = faster)")
lines.append("")
lines.append("| idx | lemma | session | verdict | orig_ms | static_ms | delta% |")
lines.append("|----:|-------|---------|---------|--------:|----------:|-------:|")
for r in rows:
    lines.append(f"| {r.get('idx')} | `{r.get('lemma')}` | {r.get('session')} | "
                 f"{r.get('verdict')} | {r.get('orig_ms')} | {r.get('static_ms')} | "
                 f"{r.get('delta_pct')} |")
lines.append("")
lines.append("Verdict legend: ACCEPT = audit-clean static path that builds AND is "
             "faster by >MARGIN; REJECT-no-speedup = builds but not faster; "
             "REJECT-incorrect = patched proof failed to build; "
             "INCONCLUSIVE-* = below noise floor or timing failed; "
             "NO-PATH = agent found no audit-passing static path.\n")
lines.append("Per-lemma dir `<NN>-<lemma>/` (5 files): record.md (one-page: before/after "
             "code + diff + search tree with LLM reasoning + per-line timing), "
             "timing.json (before/after total+per-line), attempts.jsonl (raw node+attempt "
             "events), result.json (gate verdict), gate.log (build stdout).")
open(os.path.join(run, "summary.md"), "w").write("\n".join(lines))
print("\n".join(lines))
PY

echo
echo "[bench] DONE -> $RUN"
echo "[bench]   summary.md / summary.csv  — headline table"
echo "[bench]   candidates.json           — what was selected and why"
echo "[bench]   <NN>-<lemma>/             — full per-lemma trace"
