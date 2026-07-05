#!/usr/bin/env bash
# coevolve_pipeline.sh — single entrypoint: detect -> repair -> validate -> report.
# Runs on server A (has claude), drives server B (oracle). See coevolve/README #.
#   coevolve_pipeline.sh --seed <hash>            # validate on a known break
#   coevolve_pipeline.sh --artifact-diff <file>   # deployment (TODO: Δ apply)
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID="${RUN_ID:-$(echo "$@" | tr -c 'a-zA-Z0-9' _)}"
echo "[coevolve] detect->repair->validate: $*"
python3 "$HERE/scripts/pipeline.py" "$@" --run-id "$RUN_ID"
echo "[coevolve] assembling report ..."
python3 "$HERE/scripts/report.py" "/data/zljj/sel4-dev/coevolve/pipeline-runs/$RUN_ID"
