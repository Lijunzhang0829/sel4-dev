#!/usr/bin/env bash
set -euo pipefail

CONTAINER=${STEPWISE_CONTAINER:-sel4-l4v}
TARGET=/workspace/.stepwise-codex/container-site-packages
JAR=/workspace/tools/seL4-proof-search/Isa-Repl/target/IsaREPL.jar

docker exec "$CONTAINER" test -f "$JAR"
docker exec "$CONTAINER" mkdir -p "$TARGET"
docker exec "$CONTAINER" python3 -m pip install \
  --disable-pip-version-check \
  --upgrade \
  --target "$TARGET" \
  -r /workspace/regen/tools/stepwise-codex-requirements.txt

docker exec -e PYTHONPATH="$TARGET" "$CONTAINER" python3 -c \
  'import Levenshtein, rustworkx, treelib; print("Stepwise-Codex dependencies OK")'
