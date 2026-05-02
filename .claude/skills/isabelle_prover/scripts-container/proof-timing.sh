#!/bin/bash
# proof-timing.sh — Measure per-proof timing via sorry-substitution
#
# Usage: ./proof-timing.sh <theory_file> [session]
#
# Uses isabelle build (same as check-theory.sh) for each measurement.
# For each large proof block, replaces it with sorry and measures elapsed time.

set -euo pipefail

# Defensive: if the caller passed a host-side path, rewrite to /workspace.
# Canonical entry point is $ISA_SCRIPTS/proof-timing.sh (the wrapper).
_translate_host_path() {
  local p="$1"
  local host_root="${HOST_REPO_ROOT:-/home/lijun/seL4-docker-main}"
  case "$p" in
    "$host_root"|"$host_root"/*) echo "/workspace${p#$host_root}" ;;
    *) echo "$p" ;;
  esac
}
set -- "$(_translate_host_path "$1")" "${@:2}"

THEORY_FILE="$(realpath "${1:?Usage: $0 <theory_file> [session]}")"
SESSION="${2:-AInvs}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout: <repo>/claude/.claude/skills/isabelle_prover/scripts-container/
# Five "../" hops to reach the repo root (mounted at /workspace inside container).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
L4V_DIR="${L4V_DIR:-${REPO_ROOT}/verification/l4v}"
ISA_HOME="${ISABELLE_HOME:-${REPO_ROOT}/verification/isabelle}"

# Session-scoped lock (see check-theory.sh for rationale). Nested scan -> timing
# calls skip re-acquire via $ISABELLE_LOCK_HELD.
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

# Ensure session heap exists (ISABELLE_HEAPS honours /tmp/isabelle_settings)
HEAP_DIR="$(L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux"
if [ ! -f "${HEAP_DIR}/${SESSION}" ]; then
  echo "[proof-timing] ${SESSION} heap missing, rebuilding..." >&2
  L4V_ARCH="${L4V_ARCH:-ARM}" "$ISA_HOME/bin/isabelle" build -b -d "$L4V_DIR" "$SESSION" >&2 2>&1
fi

SESSION_DIR="$(dirname "$THEORY_FILE")"
THEORY_BASE="$(basename "$THEORY_FILE" .thy)"

export THEORY_FILE SESSION SESSION_DIR THEORY_BASE L4V_DIR ISA_HOME
export L4V_ARCH="${L4V_ARCH:-ARM}"

python3 << 'PYEOF'
import os, re, sys, uuid, time, subprocess, tempfile, shutil

THEORY_FILE = os.environ["THEORY_FILE"]
SESSION = os.environ["SESSION"]
SESSION_DIR = os.environ["SESSION_DIR"]
THEORY_BASE = os.environ["THEORY_BASE"]
L4V_DIR = os.environ["L4V_DIR"]
ISA_HOME = os.environ["ISA_HOME"]
L4V_ARCH = os.environ.get("L4V_ARCH", "ARM")

with open(THEORY_FILE) as f:
    orig_lines = f.readlines()

# ── Parse proof blocks ──────────────────────────────────────────────

def parse_proofs():
    proofs = []
    lemma_line = None
    lemma_name = None
    for i, line in enumerate(orig_lines):
        m = re.match(r'^lemma\s+(\S+)', line)
        if m:
            lemma_line = i
            lemma_name = m.group(1).rstrip(':')
        if line.strip() in ('done', 'qed') and lemma_line is not None:
            proof_start = None
            for j in range(lemma_line + 1, i + 1):
                s = orig_lines[j].strip()
                if s.startswith(('apply', 'using', 'by ', 'proof', 'unfolding')):
                    proof_start = j
                    break
            if proof_start is not None:
                proofs.append({
                    'name': lemma_name,
                    'proof_start': proof_start,
                    'proof_end': i,
                    'size': i - proof_start + 1,
                })
            lemma_line = None
    proofs.sort(key=lambda p: p['size'], reverse=True)
    return proofs

# ── Build temp theory and measure via isabelle build ─────────────────

def measure(sorry_range=None):
    """Create temp theory, build with isabelle build, return (ms, has_error)."""
    tmpdir = tempfile.mkdtemp()
    try:
        uid = uuid.uuid4().hex[:8]
        tmpname = f"Tmp_{uid}"

        new = list(orig_lines)
        # Rename theory
        for i, line in enumerate(new):
            if re.match(rf'^theory\s+{re.escape(THEORY_BASE)}\b', line):
                new[i] = re.sub(rf'^(theory\s+){re.escape(THEORY_BASE)}', rf'\g<1>{tmpname}', line)
                break
        # Apply sorry substitution
        if sorry_range:
            s, e = sorry_range
            new = new[:s] + ['  sorry\n'] + new[e + 1:]

        thy_path = os.path.join(tmpdir, f"{tmpname}.thy")
        with open(thy_path, 'w') as f:
            f.writelines(new)

        # Qualify bare imports with session name (tokenize to handle mixed lines)
        def qualify_imports(imports_text, session):
            result = []
            i = 0
            while i < len(imports_text):
                if imports_text[i] == '"':
                    j = imports_text.index('"', i + 1) + 1
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
        with open(thy_path) as f:
            content = f.read()
        m = re.search(r'(imports\s*\n?)(.*?)(begin)', content, re.DOTALL)
        if m:
            content = content[:m.start(2)] + qualify_imports(m.group(2), SESSION) + content[m.end(2):]
        with open(thy_path, 'w') as f:
            f.write(content)

        # Create mini ROOT (use tmpname as session name to avoid DB conflicts)
        with open(os.path.join(tmpdir, 'ROOT'), 'w') as f:
            f.write(f'session {tmpname} = {SESSION} + theories "{tmpname}"\n')

        # Build
        t0 = time.monotonic()
        result = subprocess.run(
            [os.path.join(ISA_HOME, 'bin', 'isabelle'), 'build',
             '-d', L4V_DIR, '-d', tmpdir, tmpname],
            capture_output=True, text=True,
            env={**os.environ, 'L4V_ARCH': L4V_ARCH},
        )
        ms = int((time.monotonic() - t0) * 1000)
        has_err = result.returncode != 0
        return ms, has_err
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ── Main ────────────────────────────────────────────────────────────

def main():
    proofs = parse_proofs()
    top10 = ', '.join(f"{p['name']}({p['size']}L)" for p in proofs[:10])

    print(f"File: {THEORY_FILE}")
    print(f"Session: {SESSION}")
    print(f"Total proofs: {len(proofs)}")
    print(f"Top 10 by size: {top10}")
    print()

    # Baseline
    print("── Baseline (full proofs) ──")
    base_ms, base_err = measure()
    print(f"  Baseline: {base_ms}ms ({'ERRORS' if base_err else 'OK'})")

    if base_ms < 5000:
        print(f"\n  File compiles in <5s. No slow proofs to optimize.")
        return

    # Measure top N
    N = min(10, len(proofs))
    print(f"\n── Sorry-substitution (top {N} proofs) ──")
    results = []

    for pr in proofs[:N]:
        sorry_ms, sorry_err = measure(sorry_range=(pr['proof_start'], pr['proof_end']))
        cost = base_ms - sorry_ms
        results.append({**pr, 'sorry_ms': sorry_ms, 'cost_ms': cost})
        status = "ERR" if sorry_err else "OK"
        print(f"  {pr['name']:50s}  {pr['size']:3d}L  sorry={sorry_ms:6d}ms  cost={cost:6d}ms  [{status}]")

    # Summary
    results.sort(key=lambda r: r['cost_ms'], reverse=True)
    print(f"\n── Summary ──")
    print(f"  Baseline: {base_ms}ms")
    print(f"  Slow proofs (>3s):")
    any_slow = False
    for r in results:
        if r['cost_ms'] > 3000:
            any_slow = True
            pct = r['cost_ms'] / base_ms * 100
            print(f"    {r['name']:50s}  {r['cost_ms']:6d}ms  ({pct:.0f}% of total)")
    if not any_slow:
        print(f"    (none found)")

main()
PYEOF
