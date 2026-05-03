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

# Compute the in-container path of scripts-container/ from this script's
# location relative to the host repo root. The compose service mounts
# $REPO_ROOT to /workspace, so:
#   <REPO_ROOT>/claude/.claude/skills/.../scripts/  →
#   /workspace/claude/.claude/skills/.../scripts-container/
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

exec docker compose -f "$COMPOSE_FILE" exec -T l4v \
  bash "${CONTAINER_SCRIPT_DIR}/${WRAPPER_NAME}" \
  "${translated_args[@]}"
