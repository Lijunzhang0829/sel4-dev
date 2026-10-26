#!/usr/bin/env bash
set -euo pipefail

base=/data/zljj/vero-a-unified/crefine-preparation-20261017-001
release="$base/crefine-owner-blind-aligned-a-20261025-001"
cd "$release/application"
export PYTHONPATH="$release/application"
export PYTHONDONTWRITEBYTECODE=1
export REGEN_MODEL_CONFIG="$release/deployment-model-config.json"

# Prepared fresh owner-blind run; invoke only when a new experiment is requested.
exec /data/zljj/vero-a-unified/20260928-004/venv/bin/python -u \
  -m regen.tools.generic_serial_application \
  --dataset "$release/dataset" \
  --output "$base/experiments/codex-crefine-owner-blind-sol-xhigh-effective48h-planning-included-fresh-20261025-001" \
  --model-config "$REGEN_MODEL_CONFIG" \
  --auth /data/zljj/vero-private/crefine-owner-blind-20261025-001/codex-auth.json \
  --model gpt-5.6-sol \
  --reasoning xhigh \
  --expected-contracts 309 \
  --budget-mode effective-time \
  --effective-seconds 172800 \
  --include-planning-time \
  --idle-timeout 900 \
  --preflight-seconds 900 \
  --check-timeout 900 \
  --candidate-timeout 120 \
  --provider-retry-limit 1 \
  --shared-host-lock-dir "$base/runtime/shared-native-slots-2-20261019" \
  --shared-host-slots 2 \
  "$@"
