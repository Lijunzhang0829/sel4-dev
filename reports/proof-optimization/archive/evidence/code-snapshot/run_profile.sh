#!/bin/bash
# Poll-and-early-kill runner for the intra-tactic profiler.
# time_profile makes `isabelle process` HANG after the theory finishes; this runs
# it, polls the output for a LARGE profile (>=1000 samples => the heavy tactic's
# real ProfileTime table), and kills as soon as it lands so we don't wait out the
# hang. Thorough kill (process tree + by-theory-name) to avoid lock-holding orphans.
#
# usage: run_profile.sh <tmpd> <tmpname> <session> <l4vdir> <rawout> [maxsecs]
set -u
TMPD=$1; THY=$2; SESSION=$3; L4V=$4; RAW=$5; MAX=${6:-1800}
ISA=/workspace/verification/isabelle/bin/isabelle
LOCK=/tmp/isabelle-session-${SESSION}.lock
: > "$RAW"

exec 200>"$LOCK"
flock 200   # serialize on the session heap; released when this script exits

cd "$TMPD"
# setsid => isabelle (and its java/poly children) form a new process group led by
# IP, so we can kill the whole tree with `kill -9 -$IP` without a `pkill -f $THY`
# that would also match (and kill) this very script's argv.
#
# parallel_proofs=0 + threads=1: Isabelle normally runs each proof in a PARALLEL
# FUTURE, so `by (fastforce ...)` forks the real work OUTSIDE our time_profile
# region (we'd capture only ~16 fork samples). Forcing synchronous, single-thread
# execution makes the tactic run INLINE inside the profiled region — and gives its
# true CPU cost (vs the build DB's parallel `elapsed`, which is inflated by
# scheduling contention).
# quick_and_dirty=true allows the sorry-prefix; parallel_proofs=0 keeps the TARGET
# proof synchronous/in-line so time_profile captures it; threads kept >1 so the
# (sorried, cheap) prefix and any parallel setup still move quickly.
setsid "$ISA" process -l "$SESSION" -d "$L4V" \
  -o parallel_proofs=0 -o quick_and_dirty=true \
  -T "$THY" </dev/null >"$RAW" 2>&1 &
IP=$!
S=$(date +%s)
RES="unknown"
while :; do
  EL=$(( $(date +%s) - S ))
  if grep -qaE '[0-9]{4,}[[:space:]]+TOTAL' "$RAW"; then
    sleep 3   # let the profile finish flushing
    RES="profile-captured@${EL}s"; break
  fi
  if ! kill -0 "$IP" 2>/dev/null; then RES="isabelle-exited@${EL}s"; break; fi
  if [ "$EL" -ge "$MAX" ]; then RES="max-timeout@${EL}s"; break; fi
  sleep 8
done
# kill the whole process group (setsid leader = IP); covers java + poly children
kill -9 -"$IP" 2>/dev/null
kill -9 "$IP" 2>/dev/null
sleep 1
echo "[run_profile] result=$RES"
