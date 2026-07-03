#!/usr/bin/env bash
# Session-level downstream validation of a GREEN repair (the L1 oracle is
# "session builds green"; per-file check-theory alone leaves this debt).
# Applies final.patch to the tree, builds AInvs (AARCH64), records, reverts.
set -u
REPO=/data/zljj/sel4-dev
CT=/workspace/.claude/skills/isabelle_prover/scripts-container/check-theory.sh
for c in "$@"; do
  case_dir=$REPO/coevolve/cases/$c
  gt_file=$(python3 -c "import json;print(sorted(json.load(open('$case_dir/ground_truth.json')))[0])")
  echo "=== [$c] apply repair to tree: $gt_file ==="
  cd $REPO && docker compose exec -T -e L4V_ARCH=AARCH64 -e L4V_DIR=/sel4-project/verification/l4v \
    l4v bash $CT /sel4-project/verification/l4v/$gt_file AInvs --apply /workspace/coevolve/cases/$c/repair/final.patch 2>&1 | tail -3
  echo "=== [$c] downstream: full AInvs (AARCH64) build ==="
  docker compose exec -T l4v bash -c \
    "export ISABELLE_HOME_USER=/root/.isabelle-aarch64 L4V_ARCH=AARCH64; \
     isabelle build -b -d /sel4-project/verification/l4v AInvs" 2>&1 | tail -4
  rc=$?
  verdict=$([ $rc -eq 0 ] && echo SESSION-GREEN || echo SESSION-RED)
  echo "[$c] DOWNSTREAM VERDICT: $verdict (rc=$rc)"
  echo "{\"downstream_AInvs\": \"$verdict\"}" > $case_dir/repair/downstream.json
  echo "=== [$c] revert tree ==="
  git -C $REPO/verification/l4v checkout -- $gt_file
done
echo ALL-DONE
