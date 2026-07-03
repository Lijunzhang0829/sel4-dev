#!/usr/bin/env bash
# Shared helper — invoke the container-native counterpart of this wrapper.
# Sourced by each skill script wrapper in scripts/.
# Translates host paths under $REPO_ROOT to /workspace/, then docker-exec's
# scripts-container/<name>.sh with the same args.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
# Layout in this repo (after consolidation, commit 3e27380):
#   <repo>/.claude/skills/isabelle_prover/scripts/_dx.sh
# Four "../" hops from scripts/ → isabelle_prover/ → skills/ → .claude/ → <repo>.
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
WRAPPER_NAME="$(basename "${BASH_SOURCE[1]}")"

# ---------------- container-orphan reaper -----------------------------------
# When the host wrapper is killed (timeout SIGTERM, user SIGINT, OOM, etc.),
# `docker compose exec` does NOT propagate the signal to polyml/java children
# inside the container. They become host orphans, consume RAM (~3 GB each),
# and accumulate across runs until memory is exhausted. The trap below
# captures the set of polyml/isabelle PIDs in the container at script entry,
# and on exit kills any NEW PIDs that appeared during this run. Existing
# daemons (Isa-REPL ml_server, etc.) are preserved because they're in the
# baseline snapshot.
__dx_baseline_file="/tmp/_dx_baseline_$$_$(date +%s%N | head -c 12).txt"
docker compose -f "$COMPOSE_FILE" exec -T l4v bash -c \
  "pgrep -f 'polyml|isabelle' 2>/dev/null | sort -u > '$__dx_baseline_file' || true" \
  >/dev/null 2>&1 || true

__dx_cleanup() {
  docker compose -f "$COMPOSE_FILE" exec -T l4v bash -c "
    if [ -f '$__dx_baseline_file' ]; then
      NEW=\$(comm -23 <(pgrep -f 'polyml|isabelle' 2>/dev/null | sort -u) <(sort -u '$__dx_baseline_file') 2>/dev/null)
      if [ -n \"\$NEW\" ]; then
        kill -9 \$NEW 2>/dev/null || true
      fi
      rm -f '$__dx_baseline_file'
    fi
  " >/dev/null 2>&1 || true
}
trap __dx_cleanup EXIT
trap '__dx_cleanup; exit 143' TERM
trap '__dx_cleanup; exit 130' INT

# Compute the in-container path of scripts-container/ from this script's
# location relative to the host repo root. The compose service mounts
# $REPO_ROOT to /workspace, so:
#   <REPO_ROOT>/.claude/skills/.../scripts/  →
#   /workspace/.claude/skills/.../scripts-container/
SCRIPT_DIR_REL="${SCRIPT_DIR#${REPO_ROOT}/}"
CONTAINER_SCRIPT_DIR="/workspace/${SCRIPT_DIR_REL%/scripts}/scripts-container"

# Translate any host path beginning with $REPO_ROOT to /workspace/
translated_args=()
for arg in "$@"; do
  case "$arg" in
    "$REPO_ROOT"|"$REPO_ROOT"/*) arg="/workspace${arg#$REPO_ROOT}" ;;
  esac
  translated_args+=("$arg")
done

# Forward env vars that the container scripts honor. `docker compose exec -e`
# expects either "NAME=value" or just "NAME" (inherits from current env).
# We pass NAME=value explicitly so it works regardless of shell quirks.
docker_env_args=()
for var in CHECK_THEORY_TIMEOUT_S SLEDGEHAMMER_TIMEOUT_S PER_PROVER_TIMEOUT_S \
           STRENGTHEN_DISABLE_TACTIC_COST_MODEL ISABELLE_LOCK_HELD \
           L4V_ARCH RUN_SESSION_CLEAN_REBUILD RUN_PROOF_TIMING_AFTER; do
  val="${!var-}"
  if [ -n "$val" ]; then
    docker_env_args+=(-e "$var=$val")
  fi
done

# NOTE: NOT `exec` — we need the EXIT trap above to fire on script
# termination (including SIGTERM from `timeout`). Letting the docker
# compose call return normally allows the trap to reap container
# orphans before the host shell exits.
docker compose -f "$COMPOSE_FILE" exec -T "${docker_env_args[@]}" l4v \
  bash "${CONTAINER_SCRIPT_DIR}/${WRAPPER_NAME}" \
  "${translated_args[@]}"
