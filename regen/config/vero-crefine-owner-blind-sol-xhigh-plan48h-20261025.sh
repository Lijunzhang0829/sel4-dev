#!/usr/bin/env bash
set -euo pipefail

base=/data/zljj/vero-a-unified/crefine-preparation-20261017-001
release="$base/crefine-owner-blind-aligned-a-20261025-001"
cd "$release/application"
export PYTHONPATH="$release/vero-src:$release/application"
export PYTHONDONTWRITEBYTECODE=1

# Prepared fresh owner-blind run; invoke only when a new experiment is requested.
exec /data/zljj/vero-a-unified/20260928-004/venv/bin/python -u \
  -m regen.tools.vero_serial_application \
  --vero-source "$release/vero-src" \
  --benchmark "$release/benchmark/benchmark.json" \
  --dataset "$release/dataset" \
  --output "$base/experiments/vero-crefine-owner-blind-sol-xhigh-effective48h-planning-included-fresh-20261025-001" \
  --client /data/zljj/vero-a-unified/20260928-001/client/codex \
  --auth /data/zljj/vero-private/crefine-owner-blind-20261025-001/vero-auth.json \
  --auth-state-base /data/zljj/vero-private/crefine-owner-blind-20261025-001/vero-auth-state \
  --auth-lease-dir /tmp/regen-auth-chain-leases-1010 \
  --image sha256:65933e97a11542321961efe22999550b79e12cd909d147e50b3554c7517b3f9f \
  --proxy http://127.0.0.1:17890 \
  --model gpt-5.6-sol \
  --reasoning xhigh \
  --expected-contracts 309 \
  --budget-mode effective-time \
  --effective-seconds 172800 \
  --include-planning-time \
  --native-slots 1 \
  --shared-host-lock-dir "$base/runtime/shared-native-slots-2-20261019" \
  --shared-host-slots 2 \
  "$@"
