#!/usr/bin/env bash
# tools/check_theory.sh — minimal stand-in for the skill's check-theory.sh
# while xxd is unavailable in the sel4-l4v container.
#
# Mirrors the core flow of .claude/skills/isabelle_prover/scripts-container/
# check-theory.sh (copy + qualify imports + isabelle process), but uses
# `python3 -c 'import secrets...'` for the unique temp name instead of xxd.
#
# Usage:
#   tools/check_theory.sh <thy_file> <session>
#   tools/check_theory.sh <thy_file> <session> --patch <patch_file>
#   tools/check_theory.sh <thy_file> <session> --apply <patch_file>
#
# Patch format: same as the skill's — one or more blocks separated by `---`,
# each block is `START_LINE END_LINE\n<replacement-text>`.
#
# Output:
#   - Last line: `VERDICT=<pass|fail|timeout> WALL_MS=<n>`
#   - On fail: full isabelle output above, for diagnosis.

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <thy_file> <session> [--patch|--apply <patch_file>]" >&2
  exit 1
fi

THEORY_FILE_HOST="$1"
SESSION="$2"
shift 2
MODE=""
PATCH_FILE_HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --patch) MODE="patch"; PATCH_FILE_HOST="$2"; shift 2 ;;
    --apply) MODE="apply"; PATCH_FILE_HOST="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Translate host paths to container paths.
HOST_REPO="/home/lijun/seL4-docker-main"
to_container() {
  local p="$1"
  case "$p" in
    "$HOST_REPO"|"$HOST_REPO"/*) echo "/workspace${p#$HOST_REPO}" ;;
    /*)                          echo "$p" ;;
    *)                           echo "/workspace/$p" ;;
  esac
}
THEORY_FILE="$(to_container "$(realpath "$THEORY_FILE_HOST")")"
[ -n "$PATCH_FILE_HOST" ] && PATCH_FILE="$(to_container "$(realpath "$PATCH_FILE_HOST")")" || PATCH_FILE=""

# Source paths relative to /sel4-project (not /workspace) so heap fingerprints match.
SEL4_THEORY_FILE="${THEORY_FILE/\/workspace\/verification\/l4v//sel4-project/verification/l4v}"
L4V_DIR="/sel4-project/verification/l4v"

THEORY_BASE="$(basename "${THEORY_FILE_HOST%.thy}")"

docker exec -i -e CHECK_THEORY_TIMEOUT_S="${CHECK_THEORY_TIMEOUT_S:-900}" \
            -e MODE="$MODE" \
            -e PATCH_FILE="$PATCH_FILE" \
            -e THEORY_FILE="$SEL4_THEORY_FILE" \
            -e THEORY_BASE="$THEORY_BASE" \
            -e SESSION="$SESSION" \
            -e L4V_DIR="$L4V_DIR" \
            sel4-l4v bash -s <<'CONTAINER_EOF'
set -uo pipefail
TMPDIR="$(mktemp -d)"
trap "rm -rf $TMPDIR" EXIT
TMPNAME="Tmp_$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
cp "$THEORY_FILE" "$TMPDIR/${TMPNAME}.thy"

# Apply patch if --patch
if [ "$MODE" = "patch" ] && [ -n "$PATCH_FILE" ]; then
  PATCH_FILE="$PATCH_FILE" TMP="$TMPDIR/${TMPNAME}.thy" python3 - <<'PYEOF'
import os
fn = os.environ["TMP"]
pf = os.environ["PATCH_FILE"]
with open(fn) as f: lines = f.readlines()
with open(pf) as f: text = f.read()
patches = []
for block in [b.strip() for b in text.strip().split('---') if b.strip()]:
    bl = block.split('\n')
    s, e = map(int, bl[0].split())
    patches.append((s, e, '\n'.join(bl[1:])))
patches.sort(key=lambda p: p[0], reverse=True)
for s, e, r in patches:
    lines[s-1:e] = [r + '\n']
with open(fn, 'w') as f: f.writelines(lines)
PYEOF
fi

# Replace theory header
sed -i "s/^theory ${THEORY_BASE}/theory ${TMPNAME}/" "$TMPDIR/${TMPNAME}.thy"

# Qualify bare imports
TMP="$TMPDIR/${TMPNAME}.thy" SESSION="$SESSION" python3 - <<'PYEOF'
import os, re
fn = os.environ["TMP"]; sess = os.environ["SESSION"]
with open(fn) as f: c = f.read()
m = re.search(r'(imports\s*\n?)(.*?)(begin)', c, re.DOTALL)
if not m: raise SystemExit(0)
def qual(t, s):
    out, i = [], 0
    while i < len(t):
        ch = t[i]
        if ch == '"':
            j = t.index('"', i+1) + 1
            out.append(t[i:j]); i = j
        elif ch.isspace():
            out.append(ch); i += 1
        else:
            j = i
            while j < len(t) and not t[j].isspace(): j += 1
            tok = t[i:j]
            if '.' not in tok: tok = s + '.' + tok
            out.append(tok); i = j
    return ''.join(out)
new = qual(m.group(2), sess)
c = c[:m.start(2)] + new + c[m.end(2):]
with open(fn, 'w') as f: f.write(c)
PYEOF

WALL_TIMEOUT_S="${CHECK_THEORY_TIMEOUT_S}"
START=$(date +%s%N)
set +e
OUTPUT=$(timeout --kill-after=10s "${WALL_TIMEOUT_S}s" \
  isabelle process -l "$SESSION" -d "$L4V_DIR" \
  -T "$TMPDIR/${TMPNAME}" 2>&1)
RC=$?
set -e
END=$(date +%s%N)
ELAPSED_MS=$(( (END - START) / 1000000 ))
TIMED=0
if [ $RC -eq 124 ] || [ $RC -eq 137 ]; then TIMED=1; fi

# Print isabelle output as-is, then a final VERDICT line we can parse.
echo "$OUTPUT"
if [ $TIMED -eq 1 ]; then
  echo "VERDICT=timeout WALL_MS=${ELAPSED_MS} RC=${RC}"
elif [ $RC -ne 0 ]; then
  echo "VERDICT=fail WALL_MS=${ELAPSED_MS} RC=${RC}"
else
  echo "VERDICT=pass WALL_MS=${ELAPSED_MS} RC=${RC}"
fi
exit $RC
CONTAINER_EOF
RC=$?

# If --apply requested AND the same patch passes, copy the patched file over the original.
if [ "$MODE" = "apply" ] && [ $RC -eq 0 ] && [ -n "$PATCH_FILE_HOST" ]; then
  python3 - "$THEORY_FILE_HOST" "$PATCH_FILE_HOST" <<'PYEOF'
import sys
thy, pf = sys.argv[1], sys.argv[2]
with open(thy) as f: lines = f.readlines()
with open(pf) as f: text = f.read()
patches = []
for block in [b.strip() for b in text.strip().split('---') if b.strip()]:
    bl = block.split('\n')
    s, e = map(int, bl[0].split())
    patches.append((s, e, '\n'.join(bl[1:])))
patches.sort(key=lambda p: p[0], reverse=True)
for s, e, r in patches:
    lines[s-1:e] = [r + '\n']
with open(thy, 'w') as f: f.writelines(lines)
print("APPLIED to", thy)
PYEOF
fi

exit $RC
