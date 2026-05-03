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
# Layout (after consolidation, commit 3e27380):
#   <repo>/.claude/skills/isabelle_prover/scripts-container/
# Four "../" hops to reach the repo root (mounted at /workspace inside container).
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
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
#
# [FIX 2026-05-03] The original parse_proofs() used "first done/qed line after
# `^lemma`" as the proof boundary. Three concrete failure modes that drove every
# Access scan to "Baseline: 0ms" (all sorry builds errored out before measuring):
#
#   (1) Isar `proof … qed` blocks containing nested `subgoal`/`show … done`
#       were truncated at the FIRST inner `done`. Sorry-substitution then deleted
#       lines including the lemma's actual end, leaving the OUTER `qed` orphaned.
#       Isabelle: `*** Bad context for command "qed"`.
#   (2) Only `^lemma` was matched — `theorem`, `corollary`, `proposition`,
#       `schematic_goal` were silently skipped (no entry, no measurement).
#   (3) `lemma (in locale) name:` and `lemma [attr] name:` did not match the
#       narrow `^lemma\s+\S+` pattern, so locale-bound lemmas were dropped.
#
# The replacement below uses the "next-top-level command" boundary detection
# already proven in tools/lemma_inventory/extract_lemmas.py: the proof body
# extends from the first proof-keyword line after the lemma name, up to (but
# not including) the next theory-level command (lemma/definition/end/…). All
# nested Isar constructs sit inside that span correctly.
#
# Comments (* ... *) are stripped first so commented-out lemmas don't false-match.
# Newlines are preserved during stripping, so line numbers stay aligned with
# orig_lines (the rest of the script indexes via proof_start/proof_end into
# orig_lines for sorry substitution).

def parse_proofs():
    LEMMA_KIND_RE = re.compile(
        r"^\s*(?P<kind>lemma|theorem|corollary|proposition|schematic_goal)\b"
    )
    # Optional `(in locale)` then optional `[attrs]` then identifier name.
    NAME_RE = re.compile(
        r"\s*(?:\(\s*in\s+[A-Za-z_][\w'\s,]*?\s*\)\s*)?"
        r"(?:\[[^\]]*\]\s*)?"
        r"(?P<name>[A-Za-z_][\w']*)"
    )
    PROOF_KEYWORDS = ("apply", "by", "proof", "using", "unfolding", "supply",
                      "subgoal", "show", "thus", "hence", "have", "moreover",
                      "obtain", "fix", "assume", "next", "qed", "done",
                      "oops", "sorry", "including", "sledgehammer")
    PROOF_START_RE = re.compile(r"^\s*(" + "|".join(PROOF_KEYWORDS) + r")\b")
    # Theory-level commands that terminate a proof body when seen at start of line.
    TOP_KEYWORDS = (
        "lemma", "theorem", "corollary", "proposition", "schematic_goal", "lemmas",
        "definition", "fun", "function", "primrec", "abbreviation", "notation",
        "no_notation", "declare", "axiomatization", "consts", "locale", "sublocale",
        "context", "interpretation", "instance", "instantiation", "class", "datatype",
        "record", "type_synonym", "code_datatype", "codatatype", "end", "begin",
        "ML", "ML_file", "ML_command", "ML_val", "setup", "local_setup",
        "attribute_setup", "method_setup", "syntax", "no_syntax", "translations",
        "term", "value", "thm", "find_theorems", "find_consts", "named_theorems",
        "partial_function", "termination", "defs", "overloading", "bundle",
        "unbundle", "lift_definition", "free_constructors", "oracle", "section",
        "subsection", "subsubsection", "chapter", "paragraph", "text", "txt",
        "crunch", "crunches", "crunch_ignore", "requalify_consts", "requalify_facts",
        "requalify_types", "global_naming", "qualified_consts",
    )
    NEXT_TOP_RE = re.compile(
        r"^\s*(" + "|".join(re.escape(k) for k in TOP_KEYWORDS) + r")\b"
    )

    # Strip nested (* ... *) preserving newlines and string literals.
    def _strip(src):
        out, i, depth, n, in_str = [], 0, 0, len(src), False
        while i < n:
            if not in_str and src.startswith("(*", i):
                depth, end = 1, i + 2
                while end < n and depth > 0:
                    if src.startswith("(*", end):
                        depth += 1; end += 2
                    elif src.startswith("*)", end):
                        depth -= 1; end += 2
                    elif src[end] == "\n":
                        out.append("\n"); end += 1
                    else:
                        end += 1
                i = end
                continue
            if not in_str and src[i] == '"':
                in_str = True
            elif in_str and src[i] == '"':
                in_str = False
            out.append(src[i])
            i += 1
        return "".join(out)

    cleaned_lines = _strip("".join(orig_lines)).split("\n")
    # Pad so cleaned_lines indices match orig_lines (handles files w/o trailing \n).
    while len(cleaned_lines) < len(orig_lines):
        cleaned_lines.append("")

    proofs = []
    n = len(cleaned_lines)
    i = 0
    while i < n:
        m = LEMMA_KIND_RE.match(cleaned_lines[i])
        if not m:
            i += 1
            continue
        nm = NAME_RE.match(cleaned_lines[i][m.end():])
        if not nm:
            # Anonymous lemma (`lemma "stmt" by simp`) or multi-line header — skip.
            i += 1
            continue
        name = nm.group("name")
        lemma_line = i
        # Find proof_start: first subsequent line whose first token is a proof keyword.
        proof_start = None
        for j in range(lemma_line + 1, n):
            line = cleaned_lines[j]
            if line.strip() and PROOF_START_RE.match(line):
                proof_start = j
                break
        if proof_start is None:
            i += 1
            continue
        # proof_end = line BEFORE the next top-level command after proof_start
        # (or last line of file if none). Trim trailing blank lines.
        proof_end = n - 1
        for j in range(proof_start + 1, n):
            if NEXT_TOP_RE.match(cleaned_lines[j]):
                proof_end = j - 1
                break
        while proof_end > proof_start and not cleaned_lines[proof_end].strip():
            proof_end -= 1
        proofs.append({
            'name': name,
            'proof_start': proof_start,
            'proof_end': proof_end,
            'size': proof_end - proof_start + 1,
        })
        # Advance past this lemma's body to avoid double-counting nested lemma matches.
        i = proof_end + 1
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

        # Build. quick_and_dirty=true is required because sorry-substitution
        # uses `sorry` in proof bodies; without this flag Isabelle errors out
        # with "Cheating requires quick_and_dirty mode!" before measuring
        # anything. The baseline (no sorry) also runs under quick_and_dirty
        # for symmetry — same flag, same loader path, only the proof body
        # text differs between baseline and substituted runs.
        t0 = time.monotonic()
        result = subprocess.run(
            [os.path.join(ISA_HOME, 'bin', 'isabelle'), 'build',
             '-o', 'quick_and_dirty=true',
             '-d', L4V_DIR, '-d', tmpdir, tmpname],
            capture_output=True, text=True,
            env={**os.environ, 'L4V_ARCH': L4V_ARCH},
        )
        ms = int((time.monotonic() - t0) * 1000)
        has_err = result.returncode != 0
        if has_err:
            import sys
            sys.stderr.write("\n[proof-timing] build error rc=%d\n%s%s\n" %
                             (result.returncode,
                              result.stderr[-1500:] if result.stderr else "",
                              result.stdout[-500:] if result.stdout else ""))
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
