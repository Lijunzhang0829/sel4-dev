#!/usr/bin/env bash
# Host wrapper for spec_premise_probe.py — runs inside sel4-l4v container.
# Usage:
#   bash tools/spec_strengthen/spec_premise_probe.sh \
#     <relative-thy-path> <lemma_name> <suspect_premise> [extra py args...]
#
# Translates the host theory path to /workspace/... and execs the
# probe inside the docker compose `l4v` service. Requires the
# IsaREPL JAR to be built (tools/seL4-proof-search/Isa-Repl/target/
# IsaREPL.jar) — see Isa-Repl/run.sh.

set -euo pipefail

if [ $# -lt 3 ]; then
  echo "Usage: $0 <relative-thy-path> <lemma_name> <suspect_premise> [extra py args...]" >&2
  exit 2
fi

THY_REL="$1"
LEMMA="$2"
PREMISE="$3"
shift 3

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

# Build the in-container abs path.
THY_ABS_HOST="${REPO_ROOT}/${THY_REL#./}"
if [ ! -f "$THY_ABS_HOST" ]; then
  echo "theory not found on host: $THY_ABS_HOST" >&2
  exit 4
fi
THY_ABS_CONTAINER="/workspace/${THY_REL#./}"

# ---------------- container-orphan reaper -----------------------------------
# Same pattern as .claude/skills/isabelle_prover/scripts/_dx.sh: when this
# host wrapper is killed (timeout SIGTERM, OOM, etc.), `docker compose exec`
# does not propagate the signal to polyml/java children in the container.
# They become host orphans that accumulate across runs. Capture baseline
# container PIDs at entry, kill any new ones at exit.
__probe_baseline_file="/tmp/_probe_baseline_$$_$(date +%s%N | head -c 12).txt"
docker compose -f "$COMPOSE_FILE" exec -T l4v bash -c \
  "pgrep -f 'polyml|isabelle' 2>/dev/null | sort -u > '$__probe_baseline_file' || true" \
  >/dev/null 2>&1 || true

__probe_cleanup() {
  docker compose -f "$COMPOSE_FILE" exec -T l4v bash -c "
    if [ -f '$__probe_baseline_file' ]; then
      NEW=\$(comm -23 <(pgrep -f 'polyml|isabelle' 2>/dev/null | sort -u) <(sort -u '$__probe_baseline_file') 2>/dev/null)
      if [ -n \"\$NEW\" ]; then
        kill -9 \$NEW 2>/dev/null || true
      fi
      rm -f '$__probe_baseline_file'
    fi
  " >/dev/null 2>&1 || true
}
trap __probe_cleanup EXIT
trap '__probe_cleanup; exit 143' TERM
trap '__probe_cleanup; exit 130' INT

# NOT `exec` — must let the EXIT trap fire (see _dx.sh comment).
docker compose -f "$COMPOSE_FILE" exec -T l4v \
  python3 /workspace/tools/spec_strengthen/spec_premise_probe.py \
    --theory "$THY_ABS_CONTAINER" \
    --lemma  "$LEMMA" \
    --premise "$PREMISE" \
    "$@"
