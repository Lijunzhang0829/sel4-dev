#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTAINER=${STEPWISE_CONTAINER:-sel4-l4v}
ENV_FILE=${REGEN_ENV_FILE:-"$ROOT/.env"}

value_from_env_file() {
  local key=$1 file=$2
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

if [[ -f "$ENV_FILE" ]]; then
  export REGEN_MODEL=${REGEN_MODEL:-$(value_from_env_file REGEN_MODEL "$ENV_FILE")}
  export REGEN_CODEX_RELAY_URL=${REGEN_CODEX_RELAY_URL:-$(value_from_env_file REGEN_CODEX_RELAY_URL "$ENV_FILE")}
  export REGEN_CODEX_RELAY_REPO=${REGEN_CODEX_RELAY_REPO:-$(value_from_env_file REGEN_CODEX_RELAY_REPO "$ENV_FILE")}
  CODEX_RELAY_ENV=${CODEX_RELAY_ENV:-$(value_from_env_file CODEX_RELAY_ENV "$ENV_FILE")}
fi

CODEX_RELAY_ENV=${CODEX_RELAY_ENV:-/home/lijun/codex-relay/.env}
STEPWISE_SOURCE_COMMIT=${STEPWISE_SOURCE_COMMIT:-$(git -C "$ROOT/tools/seL4-proof-search" rev-parse HEAD)}
if [[ -z "${CODEX_RELAY_TOKEN:-}" && -f "$CODEX_RELAY_ENV" ]]; then
  export CODEX_RELAY_TOKEN=$(value_from_env_file CODEX_RELAY_TOKEN "$CODEX_RELAY_ENV")
fi
if [[ -z "${CODEX_RELAY_TOKEN:-}" ]]; then
  echo "CODEX_RELAY_TOKEN is unavailable" >&2
  exit 2
fi

docker exec \
  -e CODEX_RELAY_TOKEN \
  -e REGEN_MODEL="${REGEN_MODEL:-gpt-5.5}" \
  -e REGEN_CODEX_RELAY_URL="${REGEN_CODEX_RELAY_URL:-http://127.0.0.1:18080/v1/codex}" \
  -e REGEN_CODEX_RELAY_REPO="${REGEN_CODEX_RELAY_REPO:-default}" \
  -e ISABELLE_HOME=/workspace/verification/isabelle \
  -e ISABELLE_PATH=/workspace/verification/isabelle \
  -e ISA_REPL_PATH=/workspace/tools/seL4-proof-search/Isa-Repl/target/IsaREPL.jar \
  -e JAVA_HOME="${STEPWISE_JAVA_HOME:-/root/.isabelle/contrib/jdk-21.0.3/x86_64-linux}" \
  -e STEPWISE_PROVER_ROOT=/workspace/tools/seL4-proof-search/seL4-prover \
  -e STEPWISE_SOURCE_COMMIT="$STEPWISE_SOURCE_COMMIT" \
  -e L4V_ARCH="${L4V_ARCH:-ARM}" \
  -e PYTHONPATH=/workspace/.stepwise-codex/container-site-packages:/workspace/tools/seL4-proof-search/seL4-prover:/workspace \
  -w /workspace \
  "$CONTAINER" python3 /workspace/regen/stepwise_codex.py "$@"
