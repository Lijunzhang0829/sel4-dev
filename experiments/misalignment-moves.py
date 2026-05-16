#!/usr/bin/env python3
"""
experiments/misalignment-moves.py

Generates `experiments/misalignment-moves.patch`: a git-format patch that
applies the 8 theory-file moves from cross-cut sessions (CRefine /
InfoFlowC) to Haskell-axis sessions (Refine / InfoFlow).

Why this is a patch, not a direct file move:
  verification/l4v/ is mounted into the host filesystem with read-only-ish
  permissions for non-root host users (most target directories are
  root:root). The CSTR ROOT swap experiment followed the same convention:
  produce a patch in experiments/, apply it via `git apply` inside the
  Docker container (which runs as root).

Run from repo root:
    python3 experiments/misalignment-moves.py

Apply inside Docker:
    cd /sel4-project/verification/l4v
    git apply /workspace/experiments/misalignment-moves.patch
    isabelle build -j 1 -d . Refine InfoFlow  # verify
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
L4V = REPO / "verification" / "l4v"
OUT_PATCH = REPO / "experiments" / "misalignment-moves.patch"


# 5 renames: CRefine → Refine (ARM only)
#
# UPDATED 2026-05-14: the prior version of this script also moved 3
# InfoFlowC theories to InfoFlow, but `isabelle build -n` revealed that
# `ADT_IF_Refine.thy` directly imports `Refine.EmptyFail_H`. InfoFlow
# session's `+` chain is Access → AInvs → ASpec — no Refine.
# InfoFlowC works only because its parent InfoFlowCBase = CRefine + ...
# transitively heap-merges Refine. Moving these to InfoFlow would
# require adding `sessions Refine` to InfoFlow's ROOT — which creates a
# NEW CSTR equal to or larger than the one we're trying to remove.
# So the 3 InfoFlowC misalignments are NOT genuine in session terms;
# only the 5 CRefine ones survive.
RENAMES = [
    ("proof/crefine/ARM/IsolatedThreadAction.thy",
     "proof/refine/ARM/IsolatedThreadAction.thy"),
    ("proof/crefine/ARM/Fastpath_Equiv.thy",
     "proof/refine/ARM/Fastpath_Equiv.thy"),
    ("proof/crefine/ARM/Fastpath_Defs.thy",
     "proof/refine/ARM/Fastpath_Defs.thy"),
    ("proof/crefine/ARM/ArchMove_C.thy",
     "proof/refine/ARM/ArchMove_C.thy"),
    ("proof/crefine/Move_C.thy",
     "proof/refine/Move_C.thy"),
]

# Importer files needing bare-name → FQ-name updates (CRefine side only).
IMPORTER_EDITS = [
    ("proof/crefine/ARM/Ipc_C.thy", [
        ("  IsolatedThreadAction\n", '  "Refine.IsolatedThreadAction"\n'),
    ]),
    ("proof/crefine/ARM/Refine_C.thy", [
        ("imports Init_C Fastpath_Equiv Fastpath_C CToCRefine\n",
         'imports Init_C "Refine.Fastpath_Equiv" Fastpath_C CToCRefine\n'),
    ]),
    ("proof/crefine/ARM/Fastpath_C.thy", [
        ("  Fastpath_Defs\n", '  "Refine.Fastpath_Defs"\n'),
    ]),
    ("proof/crefine/ARM/CLevityCatch.thy", [
        ("  ArchMove_C\n", '  "Refine.ArchMove_C"\n'),
    ]),
]

# ROOT changes: list of (old_block, new_block) substitutions on proof/ROOT.
# Each block must match exactly; new_block replaces it.
ROOT_EDITS = [
    # --- Refine session: add the 5 moved theories to all 3 theories blocks ---
    # Block 1 (REFINE_QUICK_AND_DIRTY)
    ("""  theories [condition = "REFINE_QUICK_AND_DIRTY", quick_and_dirty]
    "$L4V_ARCH/Refine"
    "$L4V_ARCH/RAB_FN"
    "$L4V_ARCH/EmptyFail_H"
    "$L4V_ARCH/Init_R"
""",
     """  theories [condition = "REFINE_QUICK_AND_DIRTY", quick_and_dirty]
    "Move_C"
    "$L4V_ARCH/ArchMove_C"
    "$L4V_ARCH/IsolatedThreadAction"
    "$L4V_ARCH/Fastpath_Defs"
    "$L4V_ARCH/Fastpath_Equiv"
    "$L4V_ARCH/Refine"
    "$L4V_ARCH/RAB_FN"
    "$L4V_ARCH/EmptyFail_H"
    "$L4V_ARCH/Init_R"
"""),
    # Block 2 (SKIP_REFINE_PROOFS)
    ("""  theories [condition = "SKIP_REFINE_PROOFS", quick_and_dirty, skip_proofs]
    "$L4V_ARCH/Refine"
    "$L4V_ARCH/RAB_FN"
    "$L4V_ARCH/EmptyFail_H"
    "$L4V_ARCH/Init_R"
""",
     """  theories [condition = "SKIP_REFINE_PROOFS", quick_and_dirty, skip_proofs]
    "Move_C"
    "$L4V_ARCH/ArchMove_C"
    "$L4V_ARCH/IsolatedThreadAction"
    "$L4V_ARCH/Fastpath_Defs"
    "$L4V_ARCH/Fastpath_Equiv"
    "$L4V_ARCH/Refine"
    "$L4V_ARCH/RAB_FN"
    "$L4V_ARCH/EmptyFail_H"
    "$L4V_ARCH/Init_R"
"""),
    # Block 3 (unconditional)
    ("""  theories
    "$L4V_ARCH/Refine"
    "$L4V_ARCH/RAB_FN"
    "$L4V_ARCH/EmptyFail_H"
    "$L4V_ARCH/Init_R"

(*
 * This theory is in a separate session because the proofs currently
 * work only for ARM, RISCV64, and AARCH64.
 *)
session RefineOrphanage""",
     """  theories
    "Move_C"
    "$L4V_ARCH/ArchMove_C"
    "$L4V_ARCH/IsolatedThreadAction"
    "$L4V_ARCH/Fastpath_Defs"
    "$L4V_ARCH/Fastpath_Equiv"
    "$L4V_ARCH/Refine"
    "$L4V_ARCH/RAB_FN"
    "$L4V_ARCH/EmptyFail_H"
    "$L4V_ARCH/Init_R"

(*
 * This theory is in a separate session because the proofs currently
 * work only for ARM, RISCV64, and AARCH64.
 *)
session RefineOrphanage"""),
    # InfoFlow/InfoFlowC ROOT changes removed — see RENAMES comment.
]


def make_rename_diff(old, new):
    return (
        f"diff --git a/{old} b/{new}\n"
        f"similarity index 100%\n"
        f"rename from {old}\n"
        f"rename to {new}\n"
    )


def make_unified_diff(path_in_l4v, old_text, new_text):
    """Return unified diff in git format for one file edit."""
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{path_in_l4v}",
        tofile=f"b/{path_in_l4v}",
        n=3,
    )
    diff_text = "".join(diff)
    if not diff_text:
        return ""
    return f"diff --git a/{path_in_l4v} b/{path_in_l4v}\n" + diff_text


def apply_edits(text, edits):
    """Apply each (old, new) substitution to text. Fail loudly if any
    old block doesn't appear exactly once."""
    for old, new in edits:
        count = text.count(old)
        if count == 0:
            raise RuntimeError(f"OLD block not found:\n{old[:200]}...")
        if count > 1:
            raise RuntimeError(f"OLD block matches {count} times (ambiguous):\n{old[:200]}...")
        text = text.replace(old, new, 1)
    return text


def main():
    if not L4V.is_dir():
        sys.exit(f"l4v not found at {L4V}")

    out = []
    out.append("# Generated by experiments/misalignment-moves.py\n")
    out.append("# Apply with: cd verification/l4v && git apply <patch>\n\n")

    # Renames first
    for old, new in RENAMES:
        old_full = L4V / old
        if not old_full.exists():
            sys.exit(f"Missing source for rename: {old_full}")
        out.append(make_rename_diff(old, new))
        out.append("\n")

    # Importer edits
    for path_rel, edits in IMPORTER_EDITS:
        src = (L4V / path_rel).read_text()
        new = apply_edits(src, edits)
        d = make_unified_diff(path_rel, src, new)
        if not d:
            print(f"WARNING: no diff produced for {path_rel}")
            continue
        out.append(d)
        out.append("\n")

    # ROOT edits
    root_path = "proof/ROOT"
    root_src = (L4V / root_path).read_text()
    root_new = apply_edits(root_src, ROOT_EDITS)
    out.append(make_unified_diff(root_path, root_src, root_new))

    OUT_PATCH.write_text("".join(out))
    print(f"Wrote {OUT_PATCH}")
    print(f"  Renames: {len(RENAMES)}")
    print(f"  Importer edits: {sum(len(e) for _, e in IMPORTER_EDITS)} lines in {len(IMPORTER_EDITS)} files")
    print(f"  ROOT edits: {len(ROOT_EDITS)} block substitutions")
    print(f"\nTotal patch size: {OUT_PATCH.stat().st_size} bytes")


if __name__ == "__main__":
    main()
