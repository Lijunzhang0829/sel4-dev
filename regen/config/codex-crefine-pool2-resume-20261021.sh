#!/usr/bin/env bash
set -euo pipefail

base=/data/zljj/vero-a-unified/crefine-preparation-20261017-001
application="$base/application-pool2-20261020-001"
cd "$application"
export PYTHONPATH="/data/zljj/vero-a-unified/experiment-stability-20261015-005/vero-src:$application"
export PYTHONDONTWRITEBYTECODE=1
export REGEN_MODEL_CONFIG=/data/zljj/vero-a-unified/experiment-stability-20261015-005/deployment-model-config.json

exec /data/zljj/vero-a-unified/20260928-004/venv/bin/python -u \
  -m regen.tools.generic_serial_application \
  --dataset "$base/dataset" \
  --output "$base/experiments/codex-crefine-sol-medium-effective48h-pool2-fresh-20261021-002" \
  --model-config "$REGEN_MODEL_CONFIG" \
  --auth /data/zljj/vero-private/codex-login-20260930/auth.json \
  --model gpt-5.6-sol \
  --reasoning medium \
  --expected-contracts 309 \
  --budget-mode effective-time \
  --effective-seconds 172800 \
  --idle-timeout 900 \
  --preflight-seconds 900 \
  --check-timeout 900 \
  --candidate-timeout 120 \
  --provider-retry-limit 1 \
  --shared-host-lock-dir "$base/runtime/shared-native-slots-2-20261019" \
  --shared-host-slots 2 \
  --continue-from "$base/experiments/codex-crefine-sol-medium-effective48h-pool2-fresh-20261021-001" \
  --resume-authorization "User-authorized recovery after independent Codex OAuth renewal on 2026-10-21; preserve fresh-run planning cost, effective time, and all audited state" \
  "$@"
