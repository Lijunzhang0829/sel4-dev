#!/usr/bin/env bash
# Regression guard for the l4v dual-mount path-fingerprint churn (see memory
# l4v-dual-mount-heap-churn / reports/.../infra-l4v-path-canonicalization.md).
#
# The container mounts the SAME l4v at two paths: the /workspace repo-root mount
# and the dedicated /sel4-project mount. The 29 prebuilt heaps are fingerprinted
# at the /sel4-project path. Any tool that hardcodes the /workspace l4v path makes
# scala-isabelle/Isabelle rebuild the whole session chain and thrash the heaps.
#
# RULE: never hardcode the /workspace l4v path in a .py/.sh tool. Read it from
# L4V_DIR (default /sel4-project/verification/l4v), like ab_agent.py /
# react_agent.py / poc_repl_server.py already do.
#
# Exit 1 (and list offenders) if any non-comment hardcode is found. Run manually
# or wire as a git pre-commit hook: tools/guard-l4v-path.sh --install-hook
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"

# Build the forbidden needle from pieces so the literal full path never appears
# in this file — otherwise a naive substring sweep would rewrite the guard itself.
NEEDLE='/work''space/verification/l4v'

if [ "${1:-}" = "--install-hook" ]; then
  hook="$(git rev-parse --git-path hooks/pre-commit)"
  printf '#!/usr/bin/env bash\nexec tools/guard-l4v-path.sh\n' > "$hook"
  chmod +x "$hook"
  echo "installed pre-commit hook -> $hook"
  exit 0
fi

hits="$(grep -rnF "$NEEDLE" \
          --include='*.py' --include='*.sh' \
          tools lemma-staticize spec-strengthen 2>/dev/null \
        | grep -vE ':[[:space:]]*#' \
        | grep -v 'guard-l4v-path.sh' || true)"

if [ -n "$hits" ]; then
  echo "ERROR: hardcoded ${NEEDLE} found (causes heap churn)." >&2
  echo 'Use: os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")' >&2
  echo "Offenders:" >&2
  echo "$hits" >&2
  exit 1
fi
echo "OK: no hardcoded ${NEEDLE} in tool code."
