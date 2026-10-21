#!/usr/bin/env bash
# Reproduce this strengthen run.
cd "/home/lijun/seL4-docker-main"
SLOT=Q N=2 MODEL=sonnet APPLY=0 \
  spec-strengthen/strengthen.sh "verification/l4v/proof/invariant-abstract/Finalise_AI.thy" --session AInvs
