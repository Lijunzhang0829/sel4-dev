#!/usr/bin/env bash
# Replay the measurement for P:Syscall_AI:decode_inv_wf_no_real_cte.
set -euo pipefail
cd "/home/lijun/seL4-docker-main"
PATCH="$(dirname "$0")/range-patch.patch.txt"
B=$(bash "/home/lijun/seL4-docker-main/.claude/skills/isabelle_prover/scripts/check-theory.sh" "/home/lijun/seL4-docker-main/verification/l4v/proof/invariant-abstract/Syscall_AI.thy" "AInvs" 2>&1 | tail -1 | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
T=$(bash "/home/lijun/seL4-docker-main/.claude/skills/isabelle_prover/scripts/check-theory.sh" "/home/lijun/seL4-docker-main/verification/l4v/proof/invariant-abstract/Syscall_AI.thy" "AInvs" --patch "$PATCH" 2>&1 | tail -1 | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
python3 "spec-strengthen/scripts/spec_impact.py" "$PATCH" "/home/lijun/seL4-docker-main/verification/l4v/proof/invariant-abstract/Syscall_AI.thy" \
  --baseline-wall "$B" --trial-wall "$T" \
  --tree "verification/l4v/proof" --measurement-out "$(dirname "$0")/measurement.json"
