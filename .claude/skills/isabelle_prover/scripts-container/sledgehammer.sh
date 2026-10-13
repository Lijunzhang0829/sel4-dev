#!/usr/bin/env bash
# sledgehammer.sh — Invoke Isabelle's sledgehammer at a specific line in a .thy file.
# Returns ATP-found proof reconstructions ("Try this: by (metis ...)") that the
# agent can swap into the file as a linearised, search-free replacement for slow
# `auto` / `fastforce` / `force` calls. Often 5-50x faster verify at the cost of
# more lines (explicit lemma names) — a worthwhile trade for proofs whose
# sorry-cost is dominated by undirected backtracking search.
#
# Usage: sledgehammer.sh <theory_file> <line_number> [session]
#
# Env knobs (defaults shown):
#   SLEDGEHAMMER_TIMEOUT_S=60   per-prover timeout (sledgehammer's `timeout` opt)
#   SLEDGEHAMMER_WALL_S=240     hard ceiling for the whole script (kill if exceeded)
#
# Output (to stdout, machine-parseable):
#   file: <path>
#   line: <N>
#   session: <name>
#   wall_ms: <M>
#   status: success | no_proof | timeout | error
#   suggestions:
#     <suggestion 1>
#     <suggestion 2>
#     ...

set -euo pipefail

# Defensive host-path translation
_translate_host_path() {
  local p="$1"
  local host_root="${HOST_REPO_ROOT:-/home/lijun/seL4-docker-main}"
  case "$p" in
    "$host_root"|"$host_root"/*) echo "/workspace${p#$host_root}" ;;
    *) echo "$p" ;;
  esac
}
set -- "$(_translate_host_path "$1")" "${@:2}"

THEORY_FILE="$(realpath "${1:?Usage: $0 <theory_file> <line> [session]}")"
LINE="${2:?Usage: $0 <theory_file> <line> [session]}"
SESSION="${3:-AInvs}"
PER_PROVER_TIMEOUT_S="${SLEDGEHAMMER_TIMEOUT_S:-60}"
WALL_TIMEOUT_S="${SLEDGEHAMMER_WALL_S:-240}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout: <repo>/.claude/skills/isabelle_prover/scripts-container/
# Five "../" hops to reach the repo root (mounted at /workspace inside container).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
L4V_DIR="${L4V_DIR:-${REPO_ROOT}/verification/l4v}"
ISA_HOME="${ISABELLE_HOME:-${REPO_ROOT}/verification/isabelle}"

# Session-scoped lock — sledgehammer drives `isabelle process` on the session
# heap, same locking discipline as check-theory.sh / proof-timing.sh.
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

HEAP_DIR="$(L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux"
if [ ! -f "${HEAP_DIR}/${SESSION}" ]; then
  echo "[sledgehammer] ${SESSION} heap missing, rebuilding..." >&2
  L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" build -b -d "$L4V_DIR" "$SESSION" >&2 2>&1
fi

THEORY_BASE="$(basename "$THEORY_FILE" .thy)"
TMPDIR="$(mktemp -d)"
trap "rm -rf $TMPDIR" EXIT
TMPNAME="Tmp_$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"

# Build temp .thy: keep everything up to but not including the target line, then
# insert `sledgehammer [timeout=N]` (which queries an ATP without committing the
# goal) followed by `oops` (which closes the proof block by abandoning it). This
# keeps the file well-formed without needing the original tactic — sledgehammer
# is a query, not a proof method that closes goals.
export THEORY_FILE LINE SESSION THEORY_BASE TMPDIR TMPNAME PER_PROVER_TIMEOUT_S
python3 -c '
import os, re

theory_file = os.environ["THEORY_FILE"]
line = int(os.environ["LINE"])
session = os.environ["SESSION"]
theory_base = os.environ["THEORY_BASE"]
tmpdir = os.environ["TMPDIR"]
tmpname = os.environ["TMPNAME"]
per_prover_timeout = int(os.environ["PER_PROVER_TIMEOUT_S"])

with open(theory_file) as f:
    lines = f.readlines()

kept = lines[:line - 1]
kept.append("  sledgehammer [timeout = " + str(per_prover_timeout) + "]\n")
kept.append("  oops\n")

# Balance begin/end
text = "".join(kept)
begins = len(re.findall(r"\bbegin\b", text))
ends = sum(1 for l in kept if l.strip() == "end")
for _ in range(max(begins - ends, 0)):
    kept.append("end\n")

content = "".join(kept)

# Rename theory
content = re.sub(
    r"^(theory\s+)" + re.escape(theory_base),
    r"\g<1>" + tmpname, content, count=1, flags=re.MULTILINE
)

# Qualify bare imports with session name
def qualify_imports(imports_text, session):
    dq = chr(34)
    result = []
    i = 0
    while i < len(imports_text):
        if imports_text[i] == dq:
            j = imports_text.index(dq, i + 1) + 1
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
            if "." not in token:
                token = session + "." + token
            result.append(token)
            i = j
    return "".join(result)
m = re.search(r"(imports\s*\n?)(.*?)(begin)", content, re.DOTALL)
if m:
    content = content[:m.start(2)] + qualify_imports(m.group(2), session) + content[m.end(2):]

with open(os.path.join(tmpdir, tmpname + ".thy"), "w") as f:
    f.write(content)
'

# Sledgehammer needs the bash_process server (to spawn external ATPs:
# z3, cvc4, e, vampire) which `isabelle process` does NOT start — that
# surfaces as "Bad bash_process server address" from sledgehammer. The fix
# is to use `isabelle build` (which DOES start bash_process) against a
# mini ROOT, then extract the `Try this:` suggestions from the session
# build database via `isabelle build_log -M "Try this:"`. This is the
# canonical Isabelle 2024 path for collecting per-theory output from a
# non-interactive build.
SH_SESSION_NAME="${TMPNAME}_sh"
cat > "$TMPDIR/ROOT" <<EOF
session ${SH_SESSION_NAME} = ${SESSION} +
  theories "${TMPNAME}"
EOF

export L4V_ARCH="${L4V_ARCH:-ARM}"
START=$(date +%s%N)
set +e
BUILD_OUTPUT=$(timeout --kill-after=10s "${WALL_TIMEOUT_S}s" \
  "$ISA_HOME/bin/isabelle" build \
    -o "quick_and_dirty=true" \
    -d "$L4V_DIR" \
    -d "$TMPDIR" \
    "${SH_SESSION_NAME}" 2>&1)
BUILD_RC=$?
# After the build completes (or times out), pull theory output via build_log.
# The session DB lives in the standard Isabelle heaps location and is
# searchable by the session name we just used.
LOG_OUTPUT=""
if [ $BUILD_RC -eq 0 ]; then
  # Pull ALL theory output (no -M filter) — we need to see both "Try this:"
  # success messages AND "No proof found" / "Sledgehammer ... timed out"
  # negative results to set status correctly.
  LOG_OUTPUT=$("$ISA_HOME/bin/isabelle" build_log -v "${SH_SESSION_NAME}" 2>&1 || true)
fi
OUTPUT="${BUILD_OUTPUT}
=== build_log -M 'Try this' ===
${LOG_OUTPUT}"
RC=$BUILD_RC
set -e
END=$(date +%s%N)
ELAPSED_MS=$(( (END - START) / 1000000 ))

# Decide overall status
# Note: build_log output prefixes prover suggestions with "<prover>: Try this: ...",
# so we match "Try this:" anywhere in the line, not just at column 0.
STATUS="error"
if [ $RC -eq 124 ] || [ $RC -eq 137 ]; then
  STATUS="timeout"
else
  if echo "$OUTPUT" | grep -q "Try this:"; then
    STATUS="success"
  elif echo "$OUTPUT" | grep -qE "(No proof found|Sledgehammer.*timed out|Sledgehammer: no proof)"; then
    STATUS="no_proof"
  fi
fi

# Parse + emit suggestions. Strip the "<prover>: " prefix when present so each
# line is a clean "Try this: <method>" form ready to copy into a patch.
# Drop "Duplicate proof" lines — they're just other provers reporting the same find.
SUGGESTIONS=$(echo "$OUTPUT" \
  | grep -E "(^|: )Try this:" \
  | sed -E 's/^[[:space:]]*([a-zA-Z0-9_]+: )?Try this:/Try this:/' \
  | grep -E "Try this:[[:space:]]+\S" \
  | sort -u || true)

# Persist the full sledgehammer transcript as evidence for the agent to grep through
WORKSPACE_ROOT="${L4V_DIR%/verification/l4v}"
RUN_ID_FILE="${WORKSPACE_ROOT}/logs/.current-run-id"
LOG_PATH=""
if [ -f "$RUN_ID_FILE" ] && [ -s "$RUN_ID_FILE" ]; then
  AUTO_RUN_ID="$(cat "$RUN_ID_FILE")"
  LOG_DIR="${WORKSPACE_ROOT}/logs/sledgehammer/${AUTO_RUN_ID}"
  mkdir -p "$LOG_DIR"
  LOG_BASENAME="${THEORY_BASE}_L${LINE}_$(date +%s).log"
  LOG_PATH="${LOG_DIR}/${LOG_BASENAME}"
  printf 'sledgehammer run\nfile: %s\nline: %s\nsession: %s\nwall_ms: %s\nstatus: %s\nrc: %s\n---\n%s\n' \
    "${THEORY_FILE#${WORKSPACE_ROOT}/}" "$LINE" "$SESSION" "$ELAPSED_MS" "$STATUS" "$RC" "$OUTPUT" > "$LOG_PATH"
fi

# Auto-append a kind:"sledgehammer" record to attempts JSONL so accounting +
# token-attribution include sledgehammer activity.
if [ -f "$RUN_ID_FILE" ] && [ -s "$RUN_ID_FILE" ]; then
  AUTO_RUN_ID="$(cat "$RUN_ID_FILE")"
  AUTO_LOG="${WORKSPACE_ROOT}/logs/attempts-${AUTO_RUN_ID}.jsonl"
  AUTO_TARGET="${THEORY_FILE#${WORKSPACE_ROOT}/}:L${LINE}"
  AUTO_VERDICT="pass"
  case "$STATUS" in
    no_proof|timeout|error) AUTO_VERDICT="fail" ;;
  esac

  ATTEMPTS_LOG="$AUTO_LOG" \
  AUTO_TARGET="$AUTO_TARGET" \
  SESSION_ENV="$SESSION" \
  AUTO_VERDICT="$AUTO_VERDICT" \
  WALL_MS="$ELAPSED_MS" \
  STATUS_ENV="$STATUS" \
  SUGG_TEXT="$SUGGESTIONS" \
  LOG_PATH_ENV="$LOG_PATH" \
  WORKSPACE_ROOT_ENV="$WORKSPACE_ROOT" \
  python3 - <<'PYEOF'
import os, json, datetime
sugg = [s.strip() for s in os.environ.get("SUGG_TEXT", "").split("\n") if s.strip()]
log_path_full = os.environ.get("LOG_PATH_ENV") or ""
log_path_rel = ""
if log_path_full and log_path_full.startswith(os.environ["WORKSPACE_ROOT_ENV"] + "/"):
    log_path_rel = log_path_full[len(os.environ["WORKSPACE_ROOT_ENV"]) + 1:]
rec = {
    "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "source": "auto",
    "kind": "sledgehammer",
    "target": os.environ["AUTO_TARGET"],
    "session": os.environ["SESSION_ENV"],
    "verdict": os.environ["AUTO_VERDICT"],
    "wall_ms": int(os.environ["WALL_MS"]),
    "sledgehammer_status": os.environ.get("STATUS_ENV", ""),
    "suggestion_count": len(sugg),
    "suggestions": sugg[:8],
    "log_path": log_path_rel,
    "notes": "auto-logged by sledgehammer.sh",
}
with open(os.environ["ATTEMPTS_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
PYEOF
fi

# Stdout output for the caller
echo "file: ${THEORY_FILE}"
echo "line: ${LINE}"
echo "session: ${SESSION}"
echo "wall_ms: ${ELAPSED_MS}"
echo "status: ${STATUS}"
[ -n "${LOG_PATH}" ] && echo "log: ${LOG_PATH#${WORKSPACE_ROOT}/}"
echo "suggestions:"
if [ -n "${SUGGESTIONS}" ]; then
  echo "${SUGGESTIONS}" | sed 's/^/  /'
else
  echo "  (none)"
fi
[ "$STATUS" != "success" ] && exit 1 || exit 0
