#!/usr/bin/env python3
"""
spec-strengthen/scripts/spec_frame_gap.py

Pattern G family-survey detector.

Given a theory file, finds gaps in the `set_<op>_<field>[wp]` literal-
field frame family. A "gap" is a (op, field) pair where:
  - some existing `set_<op>_*[wp]` companion exists in the file
  - no `set_<op>_<field>[wp]` already exists in the l4v tree
  - no `crunch <field>[wp]: <wrapper>` derivation reaches <op>

The detector enumerates a fixed set of kernel-state field names (the
abstract spec's state record components) and cross-checks against the
file's existing companions and against crunch derivations.

Output is JSONL on stdout, one record per candidate:
  {
    "key":     "G:<theory_base>:<op>:<field>",
    "pattern": "G",
    "theory":  "<theory_path>",
    "op":      "set_object",
    "field":   "cdt",
    "anchor":  "set_object_machine_state[wp]" | null,
    "anchor_line": <int> | null,
    "status":  "clean" | "preflight_failed:direct" | "preflight_failed:crunch",
    "reason":  "<human-readable explanation when status != clean>"
  }

Usage:
  python3 spec_frame_gap.py <theory.thy> [--op <op>]

If --op is given, only that operation is surveyed; otherwise, all
set_<op> operations with at least one existing [wp] companion in the
file are enumerated.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# Abstract-spec kernel-state record fields likely to be the target of
# Pattern G frame lemmas. Hardcoded here instead of parsed dynamically
# from the Isabelle record definition — manageable list, rarely
# changes upstream.
STATE_FIELDS = [
    # already covered as the seed in KHeap_AI.thy
    "machine_state",
    # cdt + threading + scheduling + arch
    "cdt", "cur_thread", "idle_thread",
    "scheduler_action", "ready_queues", "cur_domain",
    "domain_index", "domain_time",
    "arch_state", "interrupt_irq_node", "interrupt_states",
    # additional record fields seen across the spec
    "kheap",  # set_object writes this; never a "frame" target
    "is_original_cap",
]


# Wrappers known to call set_object / set_cdt internally, used in the
# crunch-derived collision check.
COMMON_WRAPPERS = [
    "set_simple_ko", "set_cap", "set_endpoint", "set_notification",
    "set_ntfn", "set_thread_state", "set_bound_notification",
    "thread_set", "set_pt", "set_pd", "set_asid_pool",
    "cap_insert", "cap_move", "set_mrs", "set_message_info",
    "set_extra_badge",
]


# State fields that live INSIDE the abstract state's `exst` record
# (extensible state). Reading them goes through a projection like
# `domain_index s = domain_index_internal (exst s)`. Any op that calls
# `do_extended_op` writes `exst`, which may change these projections —
# `Invariants_AI:3405-3437` proves do_extended_op preserves non-`exst`
# top-level fields but does NOT prove preservation of `exst` sub-fields.
EXT_STATE_PROJECTED = {
    "domain_index", "domain_time", "cur_domain",
    "scheduler_action", "ready_queues",
}


# Path to abstract spec definitions, relative to repo root.
SPEC_ABSTRACT_REL = "verification/l4v/spec/abstract"


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _find_op_def_block(op: str, repo_root: Path) -> str | None:
    """Return the text of `op`'s definition block from spec/abstract/.

    Heuristic block extraction:
      - Find file containing `definition ... <op> ::` (or `<op> ::` near
        `definition`).
      - Take lines from the `definition` keyword up to the next top-level
        `definition`/`lemma`/`abbreviation`/`fun`/`primrec` or 80 lines,
        whichever is first.

    Returns None if no definition is found.
    """
    spec_dir = repo_root / SPEC_ABSTRACT_REL
    if not spec_dir.exists():
        return None

    # Find files where `<op> ::` appears after `definition` (in any form).
    try:
        result = subprocess.run(
            ["grep", "-rln", "-E", rf"\b{re.escape(op)}\s*::", str(spec_dir)],
            capture_output=True, text=True, check=False,
        )
        files = [Path(p) for p in result.stdout.splitlines() if p]
    except FileNotFoundError:
        return None

    op_re = re.compile(rf"^\s*{re.escape(op)}\s*::")
    end_kw_re = re.compile(r"^(?:definition|lemma|abbreviation|fun|primrec|theorem|locale|context|end)\b")

    for f in files:
        text = _read_text(f)
        if not text:
            continue
        lines = text.splitlines()
        # Locate the `<op> ::` line, then walk backwards to find the most
        # recent `definition` keyword.
        for i, ln in enumerate(lines):
            if op_re.match(ln):
                # Walk back ≤ 5 lines to find "definition"
                start = i
                for j in range(i, max(-1, i - 6), -1):
                    if lines[j].strip().startswith("definition"):
                        start = j
                        break
                # Walk forward to find next top-level keyword
                end = min(len(lines), start + 80)
                for k in range(start + 1, end):
                    if end_kw_re.match(lines[k]):
                        end = k
                        break
                return "\n".join(lines[start:end])
    return None


# Cheap memoization across the whole survey run.
_OP_BODY_CACHE: dict[str, str | None] = {}


def _op_body(op: str, repo_root: Path) -> str | None:
    if op not in _OP_BODY_CACHE:
        _OP_BODY_CACHE[op] = _find_op_def_block(op, repo_root)
    return _OP_BODY_CACHE[op]


def _extract_callees(body: str, max_n: int = 80) -> list[str]:
    """Pull plausible sub-op identifiers out of a definition body.

    Restrictive: only lowercase-underscore tokens of length ≥4 with no
    surrounding `'`/`\\<` (Isabelle syntax noise). Limited to `max_n` to
    bound recursion cost — 80 is generous enough to capture all callees
    in typical set_* op bodies (the [[0028 set_mrs:ms]] regression: the
    earlier 20-cap missed `store_word_offs` because it came late in the
    body after many local binders).
    """
    # Strip Isabelle syntax noise that creates lots of distractor tokens.
    body_clean = re.sub(r"\\<[a-zA-Z_]+>", " ", body)  # drop \<symbol>
    body_clean = re.sub(r"::?[ \t]*\"[^\"]*\"", " ", body_clean)  # drop type annots
    found = re.findall(r"\b([a-z][a-z0-9_]{3,})\b", body_clean)
    seen, out = set(), []
    # Skip Isabelle / HOL primitives that aren't user-defined ops.
    SKIP = {"do", "od", "let", "in", "case", "of", "if", "then", "else",
            "when", "unless", "return", "gets", "gets_the", "assert",
            "modify", "put", "get", "bind", "fst", "snd", "set",
            "and", "or", "not", "true", "false", "the", "some", "none",
            "definition", "where", "lambda", "leftarrow", "equiv",
            "lparr", "rparr", "obj_ref", "option", "message", "list",
            "length_type", "state_ext", "s_monad", "unit", "nat",
            "bool", "data", "word", "machine_word", "tcb",
            "take", "drop", "length", "min", "max", "nth"}
    for t in found:
        if t in seen or t in SKIP:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_n:
            break
    return out


def op_transitively_calls(op: str, target: str, repo_root: Path,
                          depth: int = 2) -> tuple[bool, str]:
    """Does op's definition, up to `depth` recursive calls, contain `target`?

    Returns (found, evidence_chain) where evidence_chain is "op→sub_op" path
    explaining why target was found, for the diagnostic reason string.
    """
    visited = set()
    # BFS over (name, path_chain)
    queue: list[tuple[str, str]] = [(op, op)]
    while queue:
        name, chain = queue.pop(0)
        if name in visited or chain.count("→") > depth:
            continue
        visited.add(name)
        body = _op_body(name, repo_root)
        if body is None:
            continue
        if target in body:
            return True, chain
        if chain.count("→") < depth:
            for sub in _extract_callees(body):
                if sub != name and sub not in visited:
                    queue.append((sub, f"{chain}→{sub}"))
    return False, ""


LEMMA_WP_RE = re.compile(
    r"^lemma\s+(?P<name>\w+)\s*\[wp\]\s*:",
    re.MULTILINE,
)


_KNOWN_3TOKEN_OPS = frozenset({
    "set_simple_ko",
    "set_thread_state",
    "set_bound_notification",
    "set_extra_badge",
    "set_message_info",
    "set_irq_state",
    "set_object_no",
    # Added 2026-06-09 after surveying DetSchedSchedule_AI / ArchVSpace_AI:
    # cross-AInvs occurrence count + spec/abstract def existence confirms
    # these as real 3-token ops (previously misparsed as 2-token).
    "set_asid_pool",       # 183 occurrences
    "set_vm_root",         #  21
    "set_scheduler_action", #  15
})


def find_set_op_wp_lemmas(theory_text: str) -> dict[str, list[tuple[str, int]]]:
    """Group existing `set_<op>_*[wp]` lemma names by <op>.

    Strategy:
      - Default op = first 2 underscore-separated tokens of the lemma
        name (e.g. `set_object_machine_state` → op=`set_object`).
      - If the first 3 tokens form a known 3-token operation
        (e.g. `set_simple_ko`, `set_thread_state`), use that instead.

    Returns {op: [(lemma_name, line_no), ...]}. Spurious "ops"
    (single-companion noise) are filtered out at survey time by
    requiring ≥1 companion for the op to be considered.
    """
    by_op: dict[str, list[tuple[str, int]]] = {}
    for m in LEMMA_WP_RE.finditer(theory_text):
        name = m.group("name")
        if not name.startswith("set_"):
            continue
        parts = name.split("_")
        if len(parts) < 3:
            continue
        # Try 3-token first (allowlist), then fall back to 2-token.
        op = None
        if len(parts) >= 4:
            three = "_".join(parts[:3])
            if three in _KNOWN_3TOKEN_OPS:
                op = three
        if op is None:
            op = f"{parts[0]}_{parts[1]}"
        line_no = theory_text.count("\n", 0, m.start()) + 1
        by_op.setdefault(op, []).append((name, line_no))

    # Filter: keep only ops with ≥2 companions in the file (real
    # families, not 1-off lemmas that share a prefix).
    return {op: lemmas for op, lemmas in by_op.items() if len(lemmas) >= 2}


def known_field_names(by_op: dict[str, list[tuple[str, int]]]) -> dict[str, set[str]]:
    """For each op, the set of field/predicate suffixes already
    covered by an existing `set_<op>_<X>[wp]`."""
    out: dict[str, set[str]] = {}
    for op, lemmas in by_op.items():
        suffixes = set()
        for name, _ in lemmas:
            # name = "set_object_machine_state" → suffix = "machine_state"
            assert name.startswith(op + "_"), name
            suffixes.add(name[len(op) + 1:])
        out[op] = suffixes
    return out


def find_anchor(theory_text: str, op: str) -> tuple[str | None, int | None]:
    """The last `set_<op>_*[wp]` lemma in the file — the natural
    insertion anchor for a new frame lemma."""
    matches = list(LEMMA_WP_RE.finditer(theory_text))
    last = None
    last_line = None
    for m in matches:
        name = m.group("name")
        if name.startswith(op + "_"):
            last = name
            last_line = theory_text.count("\n", 0, m.start()) + 1
    return last, last_line


def grep_repo(pattern: str, paths: list[str]) -> list[str]:
    """Run `grep -rEn <pattern> <paths...>` and return the hit lines."""
    try:
        result = subprocess.run(
            ["grep", "-rEn", pattern, *paths],
            capture_output=True, text=True, check=False,
        )
        return [ln for ln in result.stdout.splitlines() if ln]
    except FileNotFoundError:
        return []


def preflight(op: str, field: str, repo_root: Path) -> tuple[str, str]:
    """Return (status, reason).

    status:
      "clean"
      "preflight_failed:direct"    — set_<op>_<field> already exists
      "preflight_failed:crunch"    — crunch derivation reaches <op>
      "preflight_failed:dmo_path"  — op (transitively) calls do_machine_op,
                                     so `machine_state` frame is a
                                     semantic FP (do_machine_op writes
                                     machine_state.memory via storeWord
                                     and friends).
      "preflight_failed:dxo_path"  — op (transitively) calls do_extended_op
                                     AND field is projected from `exst`;
                                     a per-op lift over the ext-state
                                     subfield is required, which isn't a
                                     stock wp rule. See the
                                     `Invariants_AI:3405-3437` family —
                                     it covers non-`exst` fields only.
    """
    lemma_name = f"{op}_{field}"
    # Direct existence check
    direct_hits = grep_repo(
        rf"\b{lemma_name}\b",
        [str(repo_root / "verification/l4v/")],
    )
    if direct_hits:
        return ("preflight_failed:direct",
                f"{lemma_name} already exists in {len(direct_hits)} site(s)")

    # Crunch-derived check
    pattern_parts = [rf"crunch\s+{field}\b.*{w}" for w in COMMON_WRAPPERS + [op]]
    crunch_pat = "|".join(pattern_parts)
    crunch_hits = grep_repo(
        crunch_pat,
        [str(repo_root / "verification/l4v/proof/invariant-abstract/")],
    )
    if crunch_hits:
        return ("preflight_failed:crunch",
                f"{len(crunch_hits)} crunch derivation(s) reach {op}")

    # Semantic Gate A: do_machine_op writes machine_state.
    # Conservative gate — any do_machine_op call in op's transitive
    # def is treated as a semantic FP for machine_state frames. This
    # may over-reject a hypothetical "do_machine_op of a pure-read
    # action" but no such case exists in seL4 AInvs scope at this
    # writing. See [[0028]] / [[0032 set_extra_badge skip]] for the
    # empirical cases this gate is designed to catch.
    if field == "machine_state":
        hit, chain = op_transitively_calls(op, "do_machine_op", repo_root)
        if hit:
            return ("preflight_failed:dmo_path",
                    f"do_machine_op reachable via {chain} — writes "
                    f"machine_state.memory, so frame is semantic FP")

    # Semantic Gate B: do_extended_op on ext-state-projected fields.
    # `do_extended_op` replaces the entire `exst` record, so any
    # projection out of `exst` (domain_index, domain_time, etc.) may
    # change. The standard `Invariants_AI:3405-3437` meta-lift only
    # proves preservation of non-`exst` top-level fields. A per-op
    # lift is needed; see the [[0030]] / [[0034]] decision.md notes
    # for the deferred candidates this gate is designed to catch.
    if field in EXT_STATE_PROJECTED:
        hit, chain = op_transitively_calls(op, "do_extended_op", repo_root)
        if hit:
            return ("preflight_failed:dxo_path",
                    f"do_extended_op reachable via {chain} — exst "
                    f"replacement, no stock lift for {field}")

    return ("clean", "")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pattern G family-survey detector"
    )
    ap.add_argument("theory", type=Path,
                    help="Path to .thy file to scan")
    ap.add_argument("--op", default=None,
                    help="Restrict survey to one op (e.g. set_object). "
                         "Default: all set_<op> with ≥1 existing [wp] "
                         "companion in the file.")
    args = ap.parse_args()

    if not args.theory.exists():
        print(f"theory not found: {args.theory}", file=sys.stderr)
        return 4

    repo_root = Path.cwd()
    theory_text = args.theory.read_text(encoding="utf-8", errors="replace")
    theory_base = args.theory.stem
    theory_rel = str(args.theory)

    by_op = find_set_op_wp_lemmas(theory_text)
    suffixes_by_op = known_field_names(by_op)

    ops = [args.op] if args.op else list(by_op.keys())

    for op in ops:
        if op not in suffixes_by_op:
            print(f"# op '{op}' has no existing [wp] companions in {theory_rel}",
                  file=sys.stderr)
            continue
        anchor, anchor_line = find_anchor(theory_text, op)
        covered = suffixes_by_op[op]
        for field in STATE_FIELDS:
            if field in covered:
                continue
            if field == "kheap":
                # `kheap` is the heap/object store itself; these set_<op>
                # operations mutate it by design, so it is never a useful
                # literal-field frame target.
                continue
            status, reason = preflight(op, field, repo_root)
            record = {
                "key": f"G:{theory_base}:{op}:{field}",
                "pattern": "G",
                "theory": theory_rel,
                "op": op,
                "field": field,
                "anchor": anchor,
                "anchor_line": anchor_line,
                "status": status,
                "reason": reason,
                # Evidence type: mechanical (direct grep + crunch-derived
                # grep, both reliable). Tier 1 — clean candidates can be
                # executed with high confidence without further probe.
                "evidence": "mechanical",
                "tier": 1 if status == "clean" else None,
            }
            print(json.dumps(record, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
