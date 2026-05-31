#!/usr/bin/env bash
# check-theory.sh — Fast isolated check of a single .thy file
#
# Copies to temp dir, qualifies imports, uses `isabelle process` for fast
# verification (~8s overhead vs ~19s for isabelle build).
# Supports --patch for testing modifications WITHOUT touching the original file.
# Supports --apply to commit a verified patch to the original file.
#
# Usage:
#   check-theory.sh <file> [session]                       # verify as-is
#   check-theory.sh <file> [session] --patch <patch_file>  # verify with patch applied
#   check-theory.sh <file> [session] --apply <patch_file>  # apply patch to original
#
# Patch file format (one replacement per block, separated by ---):
#   START_LINE END_LINE
#   replacement text
#   (can be multiple lines)
#   ---
#
# Examples:
#   check-theory.sh l4v/proof/.../Ipc_AI.thy AInvs
#   check-theory.sh l4v/proof/.../Ipc_AI.thy AInvs --patch /tmp/my_patch.txt
#   check-theory.sh l4v/proof/.../Ipc_AI.thy AInvs --apply /tmp/my_patch.txt

set -euo pipefail

# Defensive host-path translation: if a caller (e.g. the agent invoking this
# script directly instead of going through scripts/) passes a host-side
# path under /home/lijun/seL4-docker-main/, rewrite it to the container path
# under /workspace. The canonical contract is to use $ISA_SCRIPTS/check-theory.sh
# (the wrapper) — this shim only repairs accidental bypasses.
_translate_host_path() {
  local p="$1"
  local host_root="${HOST_REPO_ROOT:-/home/lijun/seL4-docker-main}"
  case "$p" in
    "$host_root"|"$host_root"/*) echo "/workspace${p#$host_root}" ;;
    *) echo "$p" ;;
  esac
}

set -- "$(_translate_host_path "$1")" "${@:2}"

THEORY_FILE="$(realpath "${1:?Usage: $0 <file> [session] [--patch|--apply <patch_file>]}")"
SESSION="${2:-AInvs}"
MODE=""
PATCH_FILE=""

shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --patch) MODE="patch"; PATCH_FILE="$(_translate_host_path "$2")"; shift 2 ;;
    --apply) MODE="apply"; PATCH_FILE="$(_translate_host_path "$2")"; shift 2 ;;
    *) shift ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout: <repo>/.claude/skills/isabelle_prover/scripts-container/
# Four "../" hops to reach the repo root (mounted at /workspace inside container).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
L4V_DIR="${L4V_DIR:-${REPO_ROOT}/verification/l4v}"
ISA_HOME="${ISABELLE_HOME:-${REPO_ROOT}/verification/isabelle}"

# Session-scoped lock: Isabelle build/process on a given session writes to a
# shared heap + log DB. Two instances in parallel corrupt state. Fail fast if
# another caller already holds the lock. Nested calls (e.g. scan -> timing)
# carry $ISABELLE_LOCK_HELD to skip the re-acquisition.
if [ -z "${ISABELLE_LOCK_HELD:-}" ]; then
  LOCK_FILE="/tmp/isabelle-session-${SESSION}.lock"
  exec 200>"$LOCK_FILE"
  if ! flock -n 200; then
    echo "Error: another Isabelle tool is already running on session '$SESSION' (lock: $LOCK_FILE). Invoke skill tools serially — see SKILL.md." >&2
    exit 4
  fi
  export ISABELLE_LOCK_HELD="${SESSION}:$$"
fi

if [ ! -f "$THEORY_FILE" ]; then
  echo "Error: $THEORY_FILE not found" >&2
  exit 1
fi

# Ensure session heap exists. Heap location follows Isabelle settings (ISABELLE_HEAPS),
# which inside sel4-dev resolves to /isabelle/$L4V_ARCH per /tmp/isabelle_settings.
HEAP_DIR="$(L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux"
if [ ! -f "${HEAP_DIR}/${SESSION}" ]; then
  echo "[check-theory] ${SESSION} heap missing, rebuilding..." >&2
  L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" build -b -d "$L4V_DIR" "$SESSION" >&2 2>&1
  if [ ! -f "${HEAP_DIR}/${SESSION}" ]; then
    echo "Error: ${SESSION} heap build failed" >&2
    exit 1
  fi
fi

THEORY_BASE="$(basename "$THEORY_FILE" .thy)"

# --apply mode: verify first, then write to original
if [ "$MODE" = "apply" ]; then
  echo "Verifying patch before applying..."
  # Recurse with --patch to verify (this also auto-logs a kind:patch record)
  VERIFY_START=$(date +%s%N)
  "$0" "$THEORY_FILE" "$SESSION" --patch "$PATCH_FILE"
  RC=$?
  VERIFY_END=$(date +%s%N)
  VERIFY_MS=$(( (VERIFY_END - VERIFY_START) / 1000000 ))
  if [ $RC -ne 0 ]; then
    echo "Patch verification failed. Original file NOT modified." >&2
    exit 1
  fi
  # Apply patch to original file (reverse order to keep line numbers valid)
  APPLY_START=$(date +%s%N)
  python3 -c "
import sys
with open('$THEORY_FILE') as f:
    lines = f.readlines()
with open('$PATCH_FILE') as f:
    patch_text = f.read()
blocks = [b.strip() for b in patch_text.strip().split('---') if b.strip()]
patches = []
for block in blocks:
    block_lines = block.split('\n')
    header = block_lines[0].split()
    start, end = int(header[0]), int(header[1])
    replacement = '\n'.join(block_lines[1:])
    patches.append((start, end, replacement))
patches.sort(key=lambda p: p[0], reverse=True)
for start, end, replacement in patches:
    lines[start-1:end] = [replacement + '\n']
with open('$THEORY_FILE', 'w') as f:
    f.writelines(lines)
"
  APPLY_END=$(date +%s%N)
  APPLY_MS=$(( (APPLY_END - APPLY_START) / 1000000 ))
  echo "Patch applied to $THEORY_FILE"

  # Auto-log the apply event + persist the patch under
  # logs/patches/<run_id>/<sha>.patch as evidence. The recursive --patch above
  # already logged a kind:patch record with the same patch_sha and the verify
  # wall_ms; this kind:apply twin records that the file was actually written
  # and pins the patch path for later inspection.
  WORKSPACE_ROOT="${L4V_DIR%/verification/l4v}"
  RUN_ID_FILE="${WORKSPACE_ROOT}/logs/.current-run-id"
  if [ -f "$RUN_ID_FILE" ] && [ -s "$RUN_ID_FILE" ]; then
    AUTO_RUN_ID="$(cat "$RUN_ID_FILE")"
    PATCH_DIR_ABS="${WORKSPACE_ROOT}/logs/patches/${AUTO_RUN_ID}"
    AUTO_LOG="${WORKSPACE_ROOT}/logs/attempts-${AUTO_RUN_ID}.jsonl"
    AUTO_TARGET="${THEORY_FILE#${WORKSPACE_ROOT}/}"
    mkdir -p "$PATCH_DIR_ABS"

    ATTEMPTS_LOG="$AUTO_LOG" \
    AUTO_TARGET="$AUTO_TARGET" \
    SESSION_ENV="$SESSION" \
    APPLY_MS="$APPLY_MS" \
    VERIFY_MS="$VERIFY_MS" \
    PATCH_FILE_ENV="$PATCH_FILE" \
    PATCH_DIR_ABS_ENV="$PATCH_DIR_ABS" \
    WORKSPACE_ROOT_ENV="$WORKSPACE_ROOT" \
    python3 - <<'PYEOF'
import os, json, hashlib, datetime, shutil
pf = os.environ["PATCH_FILE_ENV"]
text = open(pf).read()
sha = hashlib.sha256(text.encode()).hexdigest()[:12]
dest = os.path.join(os.environ["PATCH_DIR_ABS_ENV"], f"{sha}.patch")
if not os.path.exists(dest):
    shutil.copy(pf, dest)
patch_path_rel = dest[len(os.environ["WORKSPACE_ROOT_ENV"]) + 1:]
blocks = [b.strip() for b in text.strip().split("---") if b.strip()]
added = removed = 0
for block in blocks:
    bl = block.split("\n")
    h = bl[0].split()
    s, e = int(h[0]), int(h[1])
    removed += (e - s + 1)
    added += len(bl[1:])
rec = {
    "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "source": "auto",
    "kind": "apply",
    "target": os.environ["AUTO_TARGET"],
    "session": os.environ["SESSION_ENV"],
    "verdict": "pass",
    "wall_ms": int(os.environ["APPLY_MS"]),
    "verify_ms": int(os.environ["VERIFY_MS"]),
    "patch_sha": sha,
    "lines_added": added,
    "lines_removed": removed,
    "patch_bytes": len(text),
    "patch_path": patch_path_rel,
    "notes": "auto-logged by check-theory.sh (apply)",
}
with open(os.environ["ATTEMPTS_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
PYEOF
  fi
  exit 0
fi

# Create temp copy
TMPDIR="$(mktemp -d)"
trap "rm -rf $TMPDIR" EXIT

TMPNAME="Tmp_$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
cp "$THEORY_FILE" "$TMPDIR/${TMPNAME}.thy"

# Apply patch to temp copy if --patch
if [ "$MODE" = "patch" ] && [ -n "$PATCH_FILE" ]; then
  python3 -c "
import sys
with open('$TMPDIR/${TMPNAME}.thy') as f:
    lines = f.readlines()
with open('$PATCH_FILE') as f:
    patch_text = f.read()
# Apply patches in reverse order so line numbers stay valid
blocks = [b.strip() for b in patch_text.strip().split('---') if b.strip()]
patches = []
for block in blocks:
    block_lines = block.split('\n')
    header = block_lines[0].split()
    start, end = int(header[0]), int(header[1])
    replacement = '\n'.join(block_lines[1:])
    patches.append((start, end, replacement))
patches.sort(key=lambda p: p[0], reverse=True)
for start, end, replacement in patches:
    lines[start-1:end] = [replacement + '\n']
with open('$TMPDIR/${TMPNAME}.thy', 'w') as f:
    f.writelines(lines)
"
fi

# Replace theory name in header
sed -i "s/^theory ${THEORY_BASE}/theory ${TMPNAME}/" "$TMPDIR/${TMPNAME}.thy"

# Qualify bare imports with session name
python3 -c "
import re
def qualify_imports(imports_text, session):
    # Tokenize: quoted strings verbatim, bare identifiers (no dot) get session prefix
    result = []
    i = 0
    while i < len(imports_text):
        if imports_text[i] == '\"':
            j = imports_text.index('\"', i + 1) + 1
            result.append(imports_text[i:j])
            i = j
        elif imports_text[i].isspace():
            result.append(imports_text[i])
            i += 1
        else:
            j = i
            while j < len(imports_text) and not imports_text[j].isspace():
                j += 1
            token = imports_text[i:j]
            if '.' not in token:
                token = session + '.' + token
            result.append(token)
            i = j
    return ''.join(result)
with open('$TMPDIR/${TMPNAME}.thy') as f:
    content = f.read()
m = re.search(r'(imports\s*\n?)(.*?)(begin)', content, re.DOTALL)
if m:
    new_imports = qualify_imports(m.group(2), '${SESSION}')
    content = content[:m.start(2)] + new_imports + content[m.end(2):]
with open('$TMPDIR/${TMPNAME}.thy', 'w') as f:
    f.write(content)
"

# Verify using `isabelle process` — loads session heap directly, ~8s less overhead
# than `isabelle build` which creates a temporary session.
#
# Wall-time ceiling: kill the process if it runs longer than
# CHECK_THEORY_TIMEOUT_S (default 600 = 10 min). This catches search-
# explosion patches that would otherwise burn 10–30 min wall on a single
# verify attempt. Returns RC=124 (timeout's standard exit) on hit.
export L4V_ARCH="${L4V_ARCH:-ARM}"
WALL_TIMEOUT_S="${CHECK_THEORY_TIMEOUT_S:-600}"
START=$(date +%s%N)
set +e
OUTPUT=$(timeout --kill-after=10s "${WALL_TIMEOUT_S}s" \
  "$ISA_HOME/bin/isabelle" process -l "$SESSION" -d "$L4V_DIR" \
  -T "$TMPDIR/${TMPNAME}" 2>&1)
RC=$?
set -e
END=$(date +%s%N)
ELAPSED_MS=$(( (END - START) / 1000000 ))
TIMED_OUT=0
if [ $RC -eq 124 ] || [ $RC -eq 137 ]; then
  TIMED_OUT=1
  OUTPUT="${OUTPUT}
*** check-theory.sh: killed after ${WALL_TIMEOUT_S}s wall-time ceiling (CHECK_THEORY_TIMEOUT_S). Search-explosion guard."
fi

# Auto-append to attempts JSONL of the active run, so every check-theory
# invocation is recorded even if the agent forgets. RUN_ID is published by
# run.sh at /workspace/logs/.current-run-id during a strengthen run.
# When --patch is used, patch_sha + LOC fields are also included so downstream
# joiners can group attempts by patch identity.
WORKSPACE_ROOT="${L4V_DIR%/verification/l4v}"
RUN_ID_FILE="${WORKSPACE_ROOT}/logs/.current-run-id"
if [ -f "$RUN_ID_FILE" ] && [ -s "$RUN_ID_FILE" ]; then
  AUTO_RUN_ID="$(cat "$RUN_ID_FILE")"
  AUTO_LOG="${WORKSPACE_ROOT}/logs/attempts-${AUTO_RUN_ID}.jsonl"
  AUTO_KIND="baseline"; [ "$MODE" = "patch" ] && AUTO_KIND="patch"; [ "$MODE" = "apply" ] && AUTO_KIND="apply"
  AUTO_VERDICT="pass"
  [ $RC -ne 0 ] && AUTO_VERDICT="fail"
  [ "$TIMED_OUT" -eq 1 ] && AUTO_VERDICT="timeout"
  AUTO_TARGET="${THEORY_FILE#${WORKSPACE_ROOT}/}"

  ATTEMPTS_LOG="$AUTO_LOG" \
  AUTO_KIND="$AUTO_KIND" \
  AUTO_TARGET="$AUTO_TARGET" \
  SESSION_ENV="$SESSION" \
  AUTO_VERDICT="$AUTO_VERDICT" \
  WALL_MS="$ELAPSED_MS" \
  PATCH_FILE_ENV="${PATCH_FILE:-}" \
  python3 - <<'PYEOF'
import os, json, hashlib, datetime
rec = {
    "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "source": "auto",
    "kind": os.environ["AUTO_KIND"],
    "target": os.environ["AUTO_TARGET"],
    "session": os.environ["SESSION_ENV"],
    "verdict": os.environ["AUTO_VERDICT"],
    "wall_ms": int(os.environ["WALL_MS"]),
    "notes": "auto-logged by check-theory.sh",
}
pf = os.environ.get("PATCH_FILE_ENV") or ""
if pf and os.path.isfile(pf):
    text = open(pf).read()
    rec["patch_sha"] = hashlib.sha256(text.encode()).hexdigest()[:12]
    blocks = [b.strip() for b in text.strip().split("---") if b.strip()]
    added = removed = 0
    for block in blocks:
        bl = block.split("\n")
        h = bl[0].split()
        s, e = int(h[0]), int(h[1])
        removed += (e - s + 1)
        added += len(bl[1:])
    rec["lines_added"] = added
    rec["lines_removed"] = removed
    rec["patch_bytes"] = len(text)
with open(os.environ["ATTEMPTS_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
PYEOF
fi

if [ $RC -eq 0 ]; then
  echo "OK (${ELAPSED_MS}ms)"
else
  echo "FAILED (${ELAPSED_MS}ms)"
  # Extract error lines
  ERR_LINES=$(echo "$OUTPUT" | grep "^\*\*\*" | head -20)
  if [ -z "$ERR_LINES" ]; then
    echo "No *** lines found. Showing full output:"
    echo "$OUTPUT"
  else
    echo "$ERR_LINES"
  fi
  exit 1
fi
