#!/usr/bin/env bash
# Run proof-type strengthen on a single .thy file or directory
# Usage: bash strengthen-proof.sh <thy_file_or_dir> [budget]
#
# Examples:
#   bash strengthen-proof.sh l4v/proof/invariant-abstract/Ipc_AI.thy
#   bash strengthen-proof.sh l4v/proof/invariant-abstract/Ipc_AI.thy 10.00

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout: <repo>/.claude/skills/isabelle_prover/scripts-container/
# Five "../" hops to reach the repo root.
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
cd "$REPO_ROOT"
exec bash run.sh "${1:?Usage: $0 <thy_file_or_dir> [budget]}" "${2:-5.00}" proof
