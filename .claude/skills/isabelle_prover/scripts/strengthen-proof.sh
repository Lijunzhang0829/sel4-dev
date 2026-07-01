#!/usr/bin/env bash
# Host-side strengthen-proof wrapper. Unlike the other scripts, this one calls
# the host's run.sh directly (Claude CLI must stay on host), so we don't route
# through the container here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout: <repo>/.claude/skills/isabelle_prover/scripts/
# Five "../" hops to reach the seL4-docker-main repo root.
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
cd "$REPO_ROOT"
exec bash run.sh "${1:?Usage: $0 <thy_file_or_dir> [budget]}" "${2:-5.00}" proof
