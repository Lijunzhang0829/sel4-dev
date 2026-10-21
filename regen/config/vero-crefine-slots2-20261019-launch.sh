#!/usr/bin/env bash
set -euo pipefail

base=/data/zljj/vero-a-unified/crefine-preparation-20261017-001
release="$base/vero-release-20261019-green-slots2-001"
cd "$release/application"
export PYTHONPATH="$release/vero-src:$release/application"
export PYTHONDONTWRITEBYTECODE=1

exec /data/zljj/vero-a-unified/20260928-004/venv/bin/python -u \
  -m regen.tools.vero_serial_application \
  --vero-source "$release/vero-src" \
  --benchmark "$release/benchmark/benchmark.json" \
  --dataset "$base/dataset" \
  --output "$base/experiments/vero-crefine-sol-medium-effective48h-slots2-20261019-001" \
  --client /data/zljj/vero-a-unified/20260928-001/client/codex \
  --auth /data/zljj/vero-private/codex-login-20260930/auth.json \
  --auth-state-base /data/zljj/vero-private/codex-login-20260930/.regen-vero-auth-state-crefine-slots2-20261019 \
  --auth-lease-dir /tmp/regen-auth-chain-leases-1010 \
  --image sha256:65933e97a11542321961efe22999550b79e12cd909d147e50b3554c7517b3f9f \
  --proxy http://127.0.0.1:17890 \
  --model gpt-5.6-sol \
  --reasoning medium \
  --expected-contracts 309 \
  --budget-mode effective-time \
  --effective-seconds 172800 \
  --native-slots 2 \
  --shared-host-lock-dir "$base/runtime/shared-native-slots-2-20261019" \
  --shared-host-slots 2 \
  "$@"
