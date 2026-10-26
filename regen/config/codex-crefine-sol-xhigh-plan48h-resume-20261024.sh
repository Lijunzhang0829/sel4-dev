#!/usr/bin/env bash
set -euo pipefail

base=/data/zljj/vero-a-unified/crefine-preparation-20261017-001
source_release="$base/crefine-dual-sol-xhigh-planning-time-release-20261024-001"
recovery_release="$base/codex-crefine-sol-xhigh-recovery-release-20261024-001"
cd "$recovery_release/application"
export PYTHONPATH="$recovery_release/application"
export PYTHONDONTWRITEBYTECODE=1
export REGEN_MODEL_CONFIG="$source_release/deployment-model-config.json"

exec /data/zljj/vero-a-unified/20260928-004/venv/bin/python -u \
  -m regen.tools.generic_serial_application \
  --dataset "$base/dataset" \
  --output "$base/experiments/codex-crefine-sol-xhigh-effective48h-planning-included-cont-20261024-001" \
  --continue-from "$base/experiments/codex-crefine-sol-xhigh-effective48h-planning-included-fresh-20261024-001" \
  --resume-authorization "User authorized resuming the same Codex CRefine experiment after disk cleanup on 2026-10-24; repair unintended progress stopping; preserve all historical planning, generation and token costs, the accepted plan and native slot binding" \
  --workspace-recovery-repair \
  --source-application-root "$source_release/application" \
  --model-config "$REGEN_MODEL_CONFIG" \
  --auth /data/zljj/vero-private/codex-login-20260930/auth.json \
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
