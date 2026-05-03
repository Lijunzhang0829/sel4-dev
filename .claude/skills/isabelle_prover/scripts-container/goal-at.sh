#!/usr/bin/env bash
# goal-at.sh — Get proof state at a specific line in a .thy file
#
# Usage: ./goal-at.sh <theory_file> <line_number> [session]

set -euo pipefail

# Defensive: if a host-side path was passed (bypassing scripts/ wrapper), translate.
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout: <repo>/.claude/skills/isabelle_prover/scripts-container/
# Five "../" hops to reach the repo root (mounted at /workspace inside container).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
L4V_DIR="${L4V_DIR:-${REPO_ROOT}/verification/l4v}"
ISA_HOME="${ISABELLE_HOME:-${REPO_ROOT}/verification/isabelle}"

THEORY_BASE="$(basename "$THEORY_FILE" .thy)"
TMPDIR="$(mktemp -d)"
trap "rm -rf $TMPDIR" EXIT

# Ensure session heap exists (ISABELLE_HEAPS honours /tmp/isabelle_settings)
HEAP_DIR="$(L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux"
if [ ! -f "${HEAP_DIR}/${SESSION}" ]; then
  echo "[goal-at] ${SESSION} heap missing, rebuilding..." >&2
  L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" build -b -d "$L4V_DIR" "$SESSION" >&2 2>&1
fi

TMPNAME="Tmp_$(head -c8 /dev/urandom | xxd -p)"
export THEORY_FILE LINE SESSION THEORY_BASE TMPDIR TMPNAME

python3 -c '
import re, os

theory_file = os.environ["THEORY_FILE"]
line = int(os.environ["LINE"])
session = os.environ["SESSION"]
theory_base = os.environ["THEORY_BASE"]
tmpdir = os.environ["TMPDIR"]
tmpname = os.environ["TMPNAME"]

with open(theory_file) as f:
    lines = f.readlines()

# Keep lines up to LINE, add print_state + oops
kept = lines[:line]
kept.append("  print_state\n")
kept.append("  oops\n")

# Count open begin/end blocks to know how many "end" we need
text = "".join(kept)
# Match standalone "begin" (not inside strings/comments)
begins = len(re.findall(r"\bbegin\b", text))
ends = sum(1 for l in kept if l.strip() == "end")
needed = begins - ends
for _ in range(max(needed, 0)):
    kept.append("end\n")

content = "".join(kept)

# Replace theory name
content = re.sub(
    r"^(theory\s+)" + re.escape(theory_base),
    r"\g<1>" + tmpname, content, count=1, flags=re.MULTILINE
)

# Qualify bare imports with session name (tokenize to handle mixed lines)
# Use chr(34) for double-quote to avoid breaking the surrounding single-quoted shell string
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

export L4V_ARCH="${L4V_ARCH:-ARM}"
OUTPUT=$("$ISA_HOME/bin/isabelle" process \
  -o quick_and_dirty=true \
  -l "$SESSION" \
  -d "$L4V_DIR" \
  -T "$TMPDIR/$TMPNAME" 2>&1)

# Extract goal block
echo "$OUTPUT" | python3 -c '
import sys
text = sys.stdin.read()
lines = text.split("\n")
in_goal = False
result = []
for line in lines:
    if line.startswith("proof (") or line.startswith("goal ("):
        in_goal = True
        result = []
    if in_goal:
        if line.startswith("###") or line.startswith("val ") or line.startswith("***"):
            break
        result.append(line)
if result:
    print("\n".join(result))
else:
    for line in lines:
        if line.startswith("***"):
            print(line)
'
