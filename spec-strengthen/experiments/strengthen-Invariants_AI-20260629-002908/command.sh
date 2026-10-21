#!/usr/bin/env bash
# Reproduce this strengthen run.
cd "/home/lijun/seL4-docker-main"
SLOT=P N=3 MODEL=sonnet APPLY=0 \
  spec-strengthen/strengthen.sh "verification/l4v/proof/invariant-abstract/Invariants_AI.thy" --session AInvs
