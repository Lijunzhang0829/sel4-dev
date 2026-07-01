#!/usr/bin/env bash
# Cost-model proposer: ranks tactic-substitution candidates for a slow proof.
#
# Pure host-side text processing — no docker exec, no session lock, no Isabelle.
# Wall: ~50–500 ms (priors + log walk + .thy read).
#
# Usage: bash propose-tactic.sh <file.thy> <line> [session]
#
# See references/tactic-cost-priors.{jsonl,md} for the priors and
# scripts/propose_tactic.py for the implementation.
exec python3 "$(dirname "$0")/propose_tactic.py" "$@"
