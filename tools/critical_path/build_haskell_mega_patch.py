#!/usr/bin/env python3
"""
tools/critical_path/build_haskell_mega_patch.py

Generate the patch that merges 11 Haskell-side proof sessions into a
single `HaskellMega` session, per user 2026-05-17 directive.

Uses parse_roots' parsed structure (theory list, dirs, sessions) rather
than regex-parsing ROOT text directly — the latter mishandles in-block
comments.

Output: experiments/haskell-mega-merge/
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
L4V = REPO / "verification" / "l4v"
PROOF_ROOT = L4V / "proof" / "ROOT"
OUTDIR = REPO / "experiments" / "haskell-mega-merge"

MERGE_GROUP = [
    "Refine", "BaseRefine", "RefineOrphanage",
    "Access", "InfoFlow",
    "DRefine", "DBaseRefine", "DPolicy",
    "DSpecProofs", "SepDSpec",
    "Bisim",
]
MERGE_SET = set(MERGE_GROUP)
NEW = "HaskellMega"

sys.path.insert(0, str(REPO / "tools" / "lemma_inventory"))
import parse_roots  # noqa: E402


def relpath_from_proof(abs_path: Path) -> str:
    """Express an absolute path relative to verification/l4v/proof/."""
    proof_dir = L4V / "proof"
    try:
        return str(abs_path.relative_to(proof_dir))
    except ValueError:
        # outside proof tree — return absolute
        return str(abs_path)


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    parsed = parse_roots.parse_all_roots(L4V, arch="ARM")
    sessions_info = parsed["sessions"]

    # ─── 1. Validate merge group ──────────────────────────────────────────
    missing = [n for n in MERGE_GROUP if n not in sessions_info]
    if missing:
        print(f"WARNING: missing sessions: {missing}")
        return 1

    # ─── 2. HaskellMega's `sessions` decl ─────────────────────────────────
    # Union of all merge-group sessions' `imported_sessions`, minus the group
    # itself, minus AInvs's `+` chain (which is already heap-merged).
    union = set()
    for n in MERGE_GROUP:
        for s in sessions_info[n].get("imported_sessions", []):
            if s not in MERGE_SET:
                union.add(s)
    plus_chain = set()
    cur = "AInvs"
    while cur and cur in sessions_info:
        plus_chain.add(cur)
        cur = sessions_info[cur].get("parent")
    union -= plus_chain
    haskell_mega_sessions = sorted(union)

    # ─── 3. HaskellMega's `directories` ───────────────────────────────────
    # We anchor HaskellMega at `proof/`-relative paths. session_dir for each
    # merge-group session is absolute → make it relative to proof/.
    dirs = set()
    for n in MERGE_GROUP:
        info = sessions_info[n]
        if info.get("session_dir"):
            sd_rel = relpath_from_proof(Path(info["session_dir"]))
            dirs.add(sd_rel)
        for d in info.get("directories", []):
            d_rel = relpath_from_proof(Path(d))
            dirs.add(d_rel)
    haskell_mega_dirs = sorted(dirs)

    # ─── 4. HaskellMega's theories list ───────────────────────────────────
    # Each session's `unconditional_theories` are RELATIVE to its session_dir.
    # We need to express them relative to proof/. Just prefix with session_dir
    # (relative to proof/).
    theories = []  # list of (relative_path, origin_session)
    for n in MERGE_GROUP:
        info = sessions_info[n]
        sd_rel = relpath_from_proof(Path(info["session_dir"]))
        for t in info.get("unconditional_theories", []):
            # t is something like "Move_C" or "ARM/Refine" or "$L4V_ARCH/Refine"
            # Normalize to use $L4V_ARCH (we don't want to hardcode ARM in ROOT)
            t_normalized = t.replace("/ARM/", "/$L4V_ARCH/")
            if t_normalized.startswith("ARM/"):
                t_normalized = "$L4V_ARCH/" + t_normalized[4:]
            combined = f"{sd_rel}/{t_normalized}" if sd_rel else t_normalized
            theories.append((combined, n))

    # ─── 5. Emit HaskellMega ROOT entry ───────────────────────────────────
    lines = []
    lines.append("(* HaskellMega: merge of 11 Haskell-side proof sessions per user")
    lines.append("   2026-05-17 directive.")
    lines.append(f"   Merged from: {', '.join(MERGE_GROUP)}")
    lines.append("   See experiments/haskell-mega-merge/summary.md for wall-delta")
    lines.append("   hypothesis and validation plan. *)")
    lines.append(f"session {NEW} = AInvs +")
    lines.append(f'  description \\<open>Mega-merged Haskell-side proof '
                 f'(was: {", ".join(MERGE_GROUP)}).\\<close>')
    if haskell_mega_sessions:
        lines.append("  sessions")
        for s in haskell_mega_sessions:
            if "-" in s or "." in s or s[0].isdigit():
                lines.append(f'    "{s}"')
            else:
                lines.append(f"    {s}")
    if haskell_mega_dirs:
        lines.append("  directories")
        for d in haskell_mega_dirs:
            lines.append(f'    "{d}"')
    if theories:
        lines.append("  theories")
        for path, origin in theories:
            lines.append(f'    "{path}"   (* from {origin} *)')
    new_haskell_mega_block = "\n".join(lines) + "\n\n"
    (OUTDIR / "HaskellMega_entry.txt").write_text(new_haskell_mega_block)

    # ─── 6. Modify proof/ROOT: delete 11 session blocks, insert HaskellMega ─
    proof_text = PROOF_ROOT.read_text()
    new_proof = proof_text
    for n in MERGE_GROUP:
        # Match `session <NAME> ...` block to next `^session ` or EOF
        pat = re.compile(
            rf"^session\s+{re.escape(n)}\b[\s\S]*?(?=^session\s|\Z)",
            re.MULTILINE,
        )
        m = pat.search(new_proof)
        if m:
            new_proof = new_proof[:m.start()] + new_proof[m.end():]
        else:
            print(f"WARNING: regex didn't match {n}'s block")
    # Clean up runs of blank lines
    new_proof = re.sub(r"\n{4,}", "\n\n\n", new_proof)
    # Insert HaskellMega right after AInvs's block (AInvs is the `+` parent)
    ainvs_pat = re.compile(
        r"^session\s+AInvs\b[\s\S]*?(?=^session\s|\Z)",
        re.MULTILINE,
    )
    am = ainvs_pat.search(new_proof)
    if am:
        new_proof = (new_proof[:am.end()].rstrip()
                     + "\n\n"
                     + new_haskell_mega_block
                     + new_proof[am.end():].lstrip("\n"))
    else:
        new_proof += "\n\n" + new_haskell_mega_block
    (OUTDIR / "proof_ROOT.new").write_text(new_proof)

    # ─── 7. Downstream session ROOT edits ────────────────────────────────
    downstream_edits = []
    for sess_name, info in sessions_info.items():
        if sess_name in MERGE_SET:
            continue
        parent = info.get("parent")
        imp = info.get("imported_sessions", [])
        refs = []
        if parent in MERGE_SET:
            refs.append(("parent", parent))
        for s in imp:
            if s in MERGE_SET:
                refs.append(("sessions", s))
        if refs:
            downstream_edits.append({
                "session": sess_name,
                "refs": refs,
                "root_file": info.get("root_file", ""),
            })
    de_md = []
    de_md.append("# Downstream session ROOT edits\n")
    de_md.append("These sessions reference merge-group sessions in their ROOT")
    de_md.append("declarations. After merge:")
    de_md.append("- `= <merge-X> +` → `= HaskellMega +`")
    de_md.append("- `sessions <merge-X>` → drop (HaskellMega already in `+` chain)")
    de_md.append("  OR `sessions HaskellMega` if not in `+` chain.\n")
    for e in downstream_edits:
        de_md.append(f"### `{e['session']}` (in `{e['root_file']}`)")
        for kind, name in e["refs"]:
            de_md.append(f"- `{kind} {name}`  →  edit needed")
        de_md.append("")
    (OUTDIR / "downstream_root_edits.md").write_text("\n".join(de_md))

    # ─── 8. External .thy import rewrites ────────────────────────────────
    HEADER_RE = re.compile(r"\btheory\s+(\w+)\s*imports\s+([\s\S]*?)\bbegin\b")
    QUOTED_RE = re.compile(r'"([^"]+)"|\b([A-Za-z_][\w.]*)\b')
    rewrites = []
    for thy in L4V.rglob("*.thy"):
        if not thy.is_file():
            continue
        try:
            raw = thy.read_text(errors="replace")
        except OSError:
            continue
        sess = parse_roots.assign_session_for_thy(thy, parsed)
        if sess in MERGE_SET:
            continue  # internal — same-session imports don't need rewrite
        clean = parse_roots.strip_isabelle_comments(raw)
        m = HEADER_RE.search(clean)
        if not m:
            continue
        body = m.group(2)
        local = []
        for tm in QUOTED_RE.finditer(body):
            tok = tm.group(1) or tm.group(2)
            if not tok or tok == "imports":
                continue
            if "." in tok:
                ts = tok.split(".", 1)[0]
                if ts in MERGE_SET:
                    new_tok = NEW + "." + tok.split(".", 1)[1]
                    local.append((tok, new_tok))
        if local:
            rewrites.append({
                "file": str(thy.relative_to(REPO)),
                "session": sess,
                "rewrites": local,
            })
    rw_md = []
    rw_md.append("# External .thy import rewrites\n")
    rw_md.append("Every external .thy that references a merge-group theory")
    rw_md.append("by qualified name (`\"Refine.X\"` etc.) must change to")
    rw_md.append(f"`\"{NEW}.X\"`.\n")
    rw_md.append(f"Affected files: {len(rewrites)}\n")
    for r in rewrites:
        rw_md.append(f"### `{r['file']}` (session `{r['session']}`)")
        for old, new in r["rewrites"]:
            rw_md.append(f"- `{old}` → `{new}`")
        rw_md.append("")
    (OUTDIR / "thy_import_rewrites.md").write_text("\n".join(rw_md))

    # ─── 9. Apply script ─────────────────────────────────────────────────
    apply_lines = [
        "#!/usr/bin/env bash",
        "# Apply the haskell-mega-merge patch in a worktree.",
        "# Run from repo root. Requires sed.",
        "set -euo pipefail",
        f'EXP="{OUTDIR.relative_to(REPO)}"',
        "",
        "# 1. Replace proof/ROOT",
        f'cp "$EXP/proof_ROOT.new" verification/l4v/proof/ROOT',
        "",
        "# 2. Downstream session ROOT edits — manual",
        "echo \"=== MANUAL STEP: review downstream_root_edits.md and apply the edits ===\"",
        "echo \"Affected sessions:\"",
    ]
    for e in downstream_edits:
        apply_lines.append(f"echo \"  - {e['session']}\"")
    apply_lines.extend([
        "",
        "# 3. Rewrite external .thy imports",
        "echo \"=== Rewriting external .thy imports ===\"",
    ])
    # Generate sed commands
    for r in rewrites:
        f_rel = r["file"]
        for old, new in r["rewrites"]:
            apply_lines.append(
                f'sed -i \'s|"{re.escape(old)}"|"{new}"|g\' "{f_rel}"'
            )
    (OUTDIR / "apply.sh").write_text("\n".join(apply_lines) + "\n")
    (OUTDIR / "apply.sh").chmod(0o755)

    # ─── 10. Summary ─────────────────────────────────────────────────────
    scan = json.loads((REPO / "reports" / "session-duplication-scan.json").read_text())
    own_sizes = {s["session"]: s["own_total_elapsed"] for s in scan["session_summary"]}
    merge_wall_sum = sum(own_sizes.get(n, 0) for n in MERGE_GROUP)

    summary = []
    summary.append("# Haskell-Mega-Merge Patch — Summary\n")
    summary.append(f"## Scope\n")
    summary.append(f"- **Merge group** (11 sessions, {merge_wall_sum:.0f}s total wall):")
    for n in MERGE_GROUP:
        summary.append(f"  - `{n}` (own {own_sizes.get(n, 0):.0f}s)")
    summary.append("")
    summary.append("- **HaskellMega** new session:")
    summary.append(f"  - `+` parent: `AInvs`")
    summary.append(f"  - `sessions`: {', '.join(haskell_mega_sessions) or '(none)'}")
    summary.append(f"  - `directories`: {len(haskell_mega_dirs)} entries")
    summary.append(f"  - `theories` (unconditional only): {len(theories)} entries")
    summary.append("")
    summary.append("## Patch contents\n")
    summary.append(f"- `proof_ROOT.new` — replaces verification/l4v/proof/ROOT")
    summary.append(f"  ({len(new_proof.splitlines())} lines, was {len(proof_text.splitlines())})")
    summary.append(f"- `downstream_root_edits.md` — {len(downstream_edits)} downstream sessions")
    summary.append(f"  need ROOT-decl edits")
    summary.append(f"- `thy_import_rewrites.md` — {sum(len(r['rewrites']) for r in rewrites)} "
                  f"qualified imports in {len(rewrites)} files need rewriting")
    summary.append(f"- `apply.sh` — automation script (applies #1 and #3, "
                  f"#2 still needs manual review)")
    summary.append("")
    summary.append("## ⚠ Known limitations of this generator\n")
    summary.append("- **Conditional theory blocks** (`theories [condition = ..., quick_and_dirty]`)")
    summary.append("  are NOT yet preserved. Quick-and-dirty / skip-proofs builds will break")
    summary.append("  if you try them under this patch. Canonical ARM build is unaffected.")
    summary.append("- **`document_files`** blocks (`Bisim`'s `root.tex` / `Makefile`) are dropped.")
    summary.append("  PDF documentation of Bisim session won't be generated under HaskellMega.")
    summary.append("")
    summary.append("## Expected wall delta (PREDICTION)\n")
    summary.append("**Direct savings**:")
    summary.append("- InfoFlowCBase ← Access source-re-exec eliminated: **-274s**")
    summary.append("- InfoFlowCBase ← InfoFlow source-re-exec eliminated: **-365s**")
    summary.append("- Smaller (DPolicy ← Access etc.): **-50–80s**")
    summary.append("- **Direct savings subtotal**: ~700s")
    summary.append("")
    summary.append("**Amplification cost** (uncertain):")
    summary.append(f"- Downstream sessions' `+` chain grows from Refine's heap")
    summary.append(f"  ({own_sizes.get('Refine', 0):.0f}s content) to HaskellMega's")
    summary.append(f"  heap ({merge_wall_sum:.0f}s content, ~2× larger)")
    summary.append(f"- ML state pollution may amplify CBaseRefine/CRefine/InfoFlow* wall")
    summary.append(f"  by 10–30% (range from prior CBaseRefine swap experience)")
    summary.append(f"- Estimated amplification cost: **+500–1500s**")
    summary.append("")
    summary.append("**Net**: range -800s to +800s. **Empirical validation required.**")
    summary.append("")
    summary.append("## Validation plan\n")
    summary.append("1. Apply in a git worktree (mega-merge branch).")
    summary.append("2. Dry-run topological check:")
    summary.append("   `isabelle build -n -d verification/l4v HaskellMega`")
    summary.append("   Cost: ~5s. Passes ⇒ ROOT structure is valid.")
    summary.append("3. Build HaskellMega only (wipe its heap first):")
    summary.append("   `isabelle build -d verification/l4v HaskellMega`")
    summary.append("   Cost: ~50–90 min Isabelle wall. Compare to sum of 11 prior session walls.")
    summary.append("4. Rebuild downstream (CBaseRefine, CRefine, InfoFlowCBase, InfoFlowC):")
    summary.append("   Cost: ~2–3h. Compare to current heaps/build_log.txt.")
    summary.append("5. Compute net delta. If regression > 200s, roll back.")
    (OUTDIR / "summary.md").write_text("\n".join(summary))

    # Print final report
    print(f"Patch written to {OUTDIR.relative_to(REPO)}/")
    for p in sorted(OUTDIR.iterdir()):
        size = p.stat().st_size
        print(f"  {p.name:30s} {size:>8d} bytes")
    print()
    print(f"HaskellMega: {len(theories)} unconditional theories, "
          f"{len(haskell_mega_dirs)} dirs, "
          f"{len(haskell_mega_sessions)} sessions decls")
    print(f"Downstream ROOT edits needed: {len(downstream_edits)}")
    print(f"External .thy imports to rewrite: {sum(len(r['rewrites']) for r in rewrites)} "
          f"in {len(rewrites)} files")


if __name__ == "__main__":
    main()
