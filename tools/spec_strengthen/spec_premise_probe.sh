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

exec docker compose -f "$COMPOSE_FILE" exec -T l4v \
  python3 /workspace/tools/spec_strengthen/spec_premise_probe.py \
    --theory "$THY_ABS_CONTAINER" \
    --lemma  "$LEMMA" \
    --premise "$PREMISE" \
    "$@"
