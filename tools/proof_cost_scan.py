#!/usr/bin/env python3
"""Per-proof sorry-cost measurement primitives for l4v.

Uses the lemma inventory parser's top-level-command boundaries to substitute a
complete proof body with ``sorry`` without breaking nested Isar proofs. For a
candidate lemma, builds a temporary session extending the owning session, times
the baseline and sorry-substituted file, and reports the wall-time delta.

Must run inside the sel4-l4v container, where Isabelle is on PATH and
/workspace points at this repository.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path


# ─── Per-file session lookup ──────────────────────────────────────────────────
#
# Why this exists: scan-slow-proofs.sh and the original scan_topN_global.py both
# took a single --session argument and applied it to every .thy in the scan-dir.
# That is wrong whenever the scan-dir spans multiple sessions:
#
#   proof/infoflow/             InfoFlow + InfoFlowC + InfoFlowCBase
#   proof/refine/ARM/           Refine + RefineOrphanage
#
# Files in a "wrong" session would error out with `*** Cannot load theory
# "<session>.<theory>"` because the temp session's heap chain doesn't include
# the file's actual session content. On InfoFlow this took out 11/51 files
# (22%) silently — the per-file scan log just showed [ERR] markers and the
# downstream cost numbers were 0.
#
# The fix: look up each .thy's actual owning session via the inventory built by
# tools/lemma_inventory/build.py. Inventory's `theories` table is the precise
# (path → session) map — derived from parsing every l4v ROOT file with directory
# prefix matching (deepest match wins).
#
# Container-vs-host paths: inventory stores rel paths under L4V_ROOT (e.g.
# `proof/infoflow/Foo.thy`). The scanner running inside the container sees
# paths like `/workspace/verification/l4v/proof/infoflow/Foo.thy`. We strip the
# prefix using the L4V_ROOT-aware path resolution helpers below.

# Default inventory location, host repo path. The container's /workspace mounts
# the host repo, so this path also works inside the container.
DEFAULT_INVENTORY_DB = "/workspace/reports/inventory/baseline.db"


@lru_cache(maxsize=1)
def _load_session_map(inventory_db: str) -> dict[str, str]:
    """Read inventory.db once, return {rel_path: session} dict.

    rel_path is keyed exactly as inventory stores it — relative to l4v root
    (e.g. `proof/infoflow/Foo.thy`).
    """
    if not Path(inventory_db).exists():
        return {}
    conn = sqlite3.connect(inventory_db)
    try:
        rows = conn.execute(
            "SELECT path, session FROM theories WHERE session IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return dict(rows)


def _to_inventory_rel(thy_path: Path, l4v_root: Path | str) -> str | None:
    """Convert an absolute container/host path to inventory's rel-path key.

    inventory keys look like 'proof/infoflow/Foo.thy' (relative to l4v_root).
    Container paths look like '/workspace/verification/l4v/proof/infoflow/Foo.thy'.
    Host paths look like '/home/lijun/seL4-docker-main/verification/l4v/proof/...'.
    Both forms strip down to the same inventory key.
    """
    abs_p = thy_path.resolve()
    candidates = [
        Path(l4v_root).resolve(),
        Path("/sel4-project/verification/l4v"),  # container heap-fingerprint path
        Path("/workspace/verification/l4v"),     # container mount path
    ]
    for root in candidates:
        try:
            return str(abs_p.relative_to(root))
        except ValueError:
            continue
    return None


def session_for_thy(thy_path: Path, l4v_root: Path | str,
                    inventory_db: str = DEFAULT_INVENTORY_DB,
                    fallback: str | None = None) -> str | None:
    """Return the session that owns this .thy file, or `fallback` if not found.

    Uses inventory.db (preferred). Falls back to None/fallback if:
      - Inventory missing or doesn't have this file
      - Path translation fails
    """
    rel = _to_inventory_rel(thy_path, l4v_root)
    if rel is None:
        return fallback
    smap = _load_session_map(inventory_db)
    return smap.get(rel, fallback)

# ─── Lemma extraction (mirrored from tools/lemma_inventory/extract_lemmas.py) ──

LEMMA_KINDS = ("lemma", "theorem", "corollary", "proposition", "schematic_goal")

TERMINATING_COMMANDS = (
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

_LEMMA_START_RE = re.compile(
    r"(^|\n)\s*(?P<kind>lemma|theorem|corollary|proposition|schematic_goal)\b",
    re.MULTILINE,
)
_NAME_RE = re.compile(
    r"\s*(?:\(\s*in\s+[A-Za-z_][\w'\s,]*?\s*\)\s*)?"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<name>[A-Za-z_][\w']*)"
    r"\s*(?:\[[^\]]*\]\s*)?"
    r"[:=]"
)
_PROOF_KEYWORDS = ("apply", "by", "proof", "using", "unfolding", "supply",
                   "subgoal", "show", "thus", "hence", "have", "moreover",
                   "obtain", "fix", "assume", "next", "qed", "done",
                   "oops", "sorry", "including", "sledgehammer")
_PROOF_START_RE = re.compile(r"^\s*(" + "|".join(_PROOF_KEYWORDS) + r")\b")
_NEXT_TOPLEVEL_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in TERMINATING_COMMANDS) + r")\b",
    re.MULTILINE,
)


def _strip_comments(src: str) -> str:
    """Strip nested (* ... *) preserving newlines and string literals."""
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


def _line_of(offset: int, line_index: list[int]) -> int:
    import bisect
    return bisect.bisect_left(line_index, offset) + 1


def parse_lemmas_with_body_lines(thy_path: Path) -> list[dict]:
    """Return list of dicts: lemma_line, body_start_line, body_end_line, name, body_size_lines."""
    raw = thy_path.read_text(errors="replace")
    clean = _strip_comments(raw)
    line_index = [i for i, c in enumerate(clean) if c == "\n"]
    n = len(clean)
    out = []
    for m in _LEMMA_START_RE.finditer(clean):
        kind_pos = m.start("kind")
        head_start = m.end()
        nm = _NAME_RE.match(clean, head_start)
        if not nm:
            continue
        name = nm.group("name")
        after_sep = nm.end()
        # Find proof start: first line whose first token is a proof keyword
        pos = after_sep
        proof_start_off = None
        while pos < n:
            line_start = pos
            nl = clean.find("\n", pos)
            line_end = n if nl == -1 else nl
            line = clean[line_start:line_end]
            if line.strip() and _PROOF_START_RE.match(line):
                proof_start_off = line_start
                break
            pos = line_end + 1
        if proof_start_off is None:
            continue
        # Find body end: next top-level command after proof start
        nxt = _NEXT_TOPLEVEL_RE.search(clean, proof_start_off + 1)
        body_end_off = nxt.start() if nxt else n

        # Convert offsets to 1-indexed line numbers in the ORIGINAL file
        # (newlines preserved by _strip_comments → line numbers align)
        lemma_line = _line_of(kind_pos, line_index)
        body_start_line = _line_of(proof_start_off, line_index)
        # body extends UP TO but not including body_end_off; trim trailing blank lines
        # body_end_line = line containing the LAST non-whitespace char before body_end_off
        body_text = clean[proof_start_off:body_end_off]
        # Find the last non-blank line of body (relative to file line numbers)
        # Walk back from body_end_off
        end = body_end_off - 1
        while end > proof_start_off and clean[end] in (" ", "\t", "\n"):
            end -= 1
        body_end_line = _line_of(end, line_index) if end >= proof_start_off else body_start_line
        body_size_lines = body_end_line - body_start_line + 1
        out.append(dict(
            name=name,
            lemma_line=lemma_line,
            body_start_line=body_start_line,
            body_end_line=body_end_line,
            body_size_lines=body_size_lines,
        ))
    return out


# ─── Build / time ──────────────────────────────────────────────────────────────

def build_temp_session(thy_path: Path, session: str, l4v_dir: str,
                       sorry_lines: tuple[int, int] | None = None) -> tuple[int, bool, str]:
    """Build a temp session that extends `session` and contains a copy of thy_path
    (optionally with body lines [s..e] replaced by `  sorry`).
    Returns (wall_ms, has_err, isabelle_stderr_tail)."""
    raw = thy_path.read_text(errors="replace")
    lines = raw.split("\n")
    if sorry_lines is not None:
        s, e = sorry_lines
        # 1-indexed inclusive [s..e] → python slice [s-1:e]
        lines = lines[:s-1] + ["  sorry"] + lines[e:]
    new_text = "\n".join(lines)
    base = thy_path.stem  # e.g., "Ipc_AC"

    with tempfile.TemporaryDirectory() as td:
        uid = uuid.uuid4().hex[:8]
        tmp_name = f"Tmp_{uid}"
        tmp_thy = Path(td) / f"{tmp_name}.thy"

        # ── Rename theory header AND its qualified self-references ───────────
        #
        # [BUG 5 — fixed 2026-05-05] Some .thy files reference their own
        # constants/definitions using the qualified form `<TheoryName>.<id>`,
        # e.g. FinalCaps.thy contains
        #     lslot \<in> FinalCaps.slots_holding_overlapping_caps cap s
        # Plain `theory FinalCaps → theory Tmp_xxx` left those qualified
        # references pointing to the (now non-existent) `FinalCaps` namespace,
        # producing
        #     *** Undefined constant: "FinalCaps.slots_holding_overlapping_caps"
        # Fix: after renaming the header, also rewrite every word-boundary
        # occurrence of `<base>.` in the body to `<tmp_name>.`. The `\.`
        # disambiguates from `theory <base>` (no following `.`) so the header
        # match isn't disturbed.
        m = re.search(r"^(theory\s+)" + re.escape(base) + r"\b", new_text, re.MULTILINE)
        if m:
            new_text = new_text[:m.start(1)] + m.group(1) + tmp_name + new_text[m.end():]
            new_text = re.sub(r"\b" + re.escape(base) + r"\.", tmp_name + ".", new_text)

        # ── Qualify imports with session prefix ──────────────────────────────
        #
        # [BUG 4 — fixed 2026-05-05] Original behaviour treated EVERY quoted
        # import (e.g. `"Foo"`) as a relative file path and left it untouched.
        # But many l4v sources use the QUOTED form for plain theory NAMES
        # (style choice, not a path), e.g.
        #     imports "ArchSyscall_IF" "ArchPasUpdates"
        # In a temp session under /tmp/xxx, `"ArchSyscall_IF"` is then resolved
        # as /tmp/xxx/ArchSyscall_IF.thy and isabelle errors out
        #     *** Cannot load theory file "/tmp/xxx/ArchSyscall_IF.thy"
        # Fix: distinguish "quoted theory name" from "quoted relative path".
        # If the quoted string contains no `/` and no `.`, treat it as a plain
        # theory name and apply the same `<session>.<name>` qualification we
        # use for bare tokens. Quoted strings with `/` or `.` are kept verbatim
        # (they ARE relative paths or already-qualified names).
        m = re.search(r"(imports\s*\n?)(.*?)(begin)", new_text, re.DOTALL)
        if m:
            imports_chunk = m.group(2)
            # [BUG 6 — fixed 2026-05-05] Imports section may contain inline
            # Isabelle comments, e.g. Syscall_IF.thy:
            #     imports
            #         "ArchPasUpdates" (*Only needed for idle thread stuff*)
            #         "ArchTcb_IF"
            # The naive tokenizer below (split on whitespace) treats `(*Only`
            # as a single non-whitespace token, finds no `.` in it, and
            # rewrites it to `InfoFlow.(*Only`. Isabelle then chokes on the
            # mangled `imports` block before reaching `begin` and emits
            # `keyword "begin" expected, but bad input was found: .`. Fix:
            # strip nested `(* ... *)` from the imports chunk before
            # tokenizing. _strip_comments preserves whitespace/newlines.
            imports_chunk = _strip_comments(imports_chunk)
            out_q, i = [], 0
            while i < len(imports_chunk):
                ch = imports_chunk[i]
                if ch == '"':
                    j = imports_chunk.index('"', i+1) + 1
                    inner = imports_chunk[i+1:j-1]
                    if "/" in inner or "." in inner or not inner:
                        # Genuine path / pre-qualified / empty — pass verbatim.
                        out_q.append(imports_chunk[i:j])
                    else:
                        # Quoted simple theory name — unquote and qualify.
                        out_q.append(session + "." + inner)
                    i = j
                elif ch.isspace():
                    out_q.append(ch); i += 1
                else:
                    j = i
                    while j < len(imports_chunk) and not imports_chunk[j].isspace():
                        j += 1
                    tok = imports_chunk[i:j]
                    if "." not in tok:
                        tok = session + "." + tok
                    out_q.append(tok); i = j
            new_text = new_text[:m.start(2)] + "".join(out_q) + new_text[m.end(2):]

        tmp_thy.write_text(new_text)

        root = Path(td) / "ROOT"
        root.write_text(f'session {tmp_name} = {session} + theories "{tmp_name}"\n')

        env = os.environ.copy()
        env["L4V_ARCH"] = env.get("L4V_ARCH", "ARM")

        t0 = time.monotonic()
        result = subprocess.run(
            ["isabelle", "build", "-o", "quick_and_dirty=true",
             "-d", l4v_dir, "-d", str(td), tmp_name],
            capture_output=True, text=True, env=env,
        )
        ms = int((time.monotonic() - t0) * 1000)
        return ms, result.returncode != 0, (result.stderr or "")[-1500:] + "\n" + (result.stdout or "")[-500:]


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("thy_file", type=Path)
    ap.add_argument("session")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--l4v-dir", default=os.environ.get("L4V_DIR", "/sel4-project/verification/l4v"))
    ap.add_argument("--json-out", type=Path, default=None,
                    help="Optional JSON dump of per-proof results")
    args = ap.parse_args()

    print(f"File: {args.thy_file}", flush=True)
    print(f"Session: {args.session}", flush=True)

    proofs = parse_lemmas_with_body_lines(args.thy_file)
    proofs.sort(key=lambda p: p["body_size_lines"], reverse=True)
    print(f"Total proofs: {len(proofs)}", flush=True)
    top_str = ", ".join(f"{p['name']}({p['body_size_lines']}L)" for p in proofs[:10])
    print(f"Top 10 by body size: {top_str}", flush=True)

    print("\n── Baseline (full proofs) ──", flush=True)
    base_ms, base_err, base_log = build_temp_session(args.thy_file, args.session, args.l4v_dir)
    print(f"  Baseline: {base_ms}ms ({'ERRORS' if base_err else 'OK'})", flush=True)
    if base_err:
        print("  --- baseline stderr tail ---", flush=True)
        print(base_log, flush=True)
        return 1

    if base_ms < 5000:
        print(f"\n  File baseline < 5s; nothing to optimise. Done.", flush=True)
        return 0

    n = min(args.top, len(proofs))
    print(f"\n── Sorry-substitution (top {n} proofs by body size) ──", flush=True)
    results = []
    for pr in proofs[:n]:
        sorry_ms, sorry_err, sorry_log = build_temp_session(
            args.thy_file, args.session, args.l4v_dir,
            sorry_lines=(pr["body_start_line"], pr["body_end_line"]),
        )
        cost = base_ms - sorry_ms
        status = "ERR" if sorry_err else "OK"
        print(f"  {pr['name']:50s}  {pr['body_size_lines']:3d}L  "
              f"sorry={sorry_ms:6d}ms  cost={cost:6d}ms  [{status}]", flush=True)
        if sorry_err:
            print(f"    [stderr tail] {sorry_log[:300]}", flush=True)
        results.append({**pr, "sorry_ms": sorry_ms, "cost_ms": cost,
                        "sorry_err": sorry_err})

    print(f"\n── Summary ──", flush=True)
    print(f"  Baseline: {base_ms}ms")
    print(f"  Slow proofs (cost > 3000 ms):")
    results.sort(key=lambda r: r["cost_ms"], reverse=True)
    any_slow = False
    for r in results:
        if r["cost_ms"] > 3000 and not r["sorry_err"]:
            any_slow = True
            print(f"    {r['name']:50s}  cost={r['cost_ms']:6d}ms  size={r['body_size_lines']:3d}L  L{r['body_start_line']}")
    if not any_slow:
        print("    (none)")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            dict(file=str(args.thy_file), session=args.session,
                 baseline_ms=base_ms, results=results), indent=2))
        print(f"\nWrote {args.json_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
