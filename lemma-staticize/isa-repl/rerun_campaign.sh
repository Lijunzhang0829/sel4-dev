#!/bin/bash
# Serial in-REPL reduce campaign over the feasible candidates that lack a FASTER variant.
# SERIAL (one REPL at a time, kill stale JVM before each) so the REPL never gets starved.
# Proposer_host (host claude -p) must already be running. Writes per-candidate reduce-<lemma>.json.
set -u
cd /home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl
PROG=runs/rerun_campaign.tsv
: > "$PROG"
N=$(python3 -c "import json;print(len(json.load(open('/tmp/rerun.json'))))")
echo "[campaign] $N candidates  $(date)"
for ((i=0; i<N; i++)); do
  eval "$(python3 - "$i" <<'PY'
import json,base64,sys
c=json.load(open('/tmp/rerun.json'))[int(sys.argv[1])]
print(f"LEMMA={c['lemma']!r}")
print(f"THYREL={c['thy']!r}")
print(f"SESS={c['session']!r}")
print(f"TB={base64.b64encode((c.get('tactic') or '').encode()).decode()!r}")
PY
)"
  # kill any stale REPL JVM so this run gets a clean machine
  docker exec sel4-l4v bash -lc 'pkill -9 -f "java -Xmx8g" 2>/dev/null; pkill -9 -f IsaREPL.jar 2>/dev/null; true' 2>/dev/null
  timeout 420 docker exec \
    -e THY="/sel4-project/verification/l4v/$THYREL" -e LEMMA="$LEMMA" -e SESSION="$SESS" \
    -e TARGET_SUBSTR_B64="$TB" -e PORT=$((26500 + i)) -e MAX_ROUNDS=3 \
    -e L4V_DIR=/sel4-project/verification/l4v \
    sel4-l4v bash -lc 'cd /workspace/tools/seL4-proof-search/Isa-Repl && python3 reduce_agent.py' \
    > "runs/rerun-$LEMMA.log" 2>&1
  V=$(python3 -c "import json;print(json.load(open('runs/reduce-$LEMMA.json')).get('verdict','ERR'))" 2>/dev/null || echo TIMEOUT)
  echo -e "$i\t$LEMMA\t$SESS\t$V" | tee -a "$PROG"
done
docker exec sel4-l4v bash -lc 'pkill -9 -f "java -Xmx8g" 2>/dev/null; true' 2>/dev/null
echo "[campaign] done $(date)"
echo "=== verdict 分布 ==="
cut -f4 "$PROG" | sort | uniq -c | sort -rn
