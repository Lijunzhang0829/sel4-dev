# Haskell-Mega-Merge Patch — Summary

## Scope

- **Merge group** (11 sessions, 2972s total wall):
  - `Refine` (own 1591s)
  - `BaseRefine` (own 238s)
  - `RefineOrphanage` (own 39s)
  - `Access` (own 301s)
  - `InfoFlow` (own 347s)
  - `DRefine` (own 147s)
  - `DBaseRefine` (own 80s)
  - `DPolicy` (own 83s)
  - `DSpecProofs` (own 24s)
  - `SepDSpec` (own 97s)
  - `Bisim` (own 25s)

- **HaskellMega** new session:
  - `+` parent: `AInvs`
  - `sessions`: ASepSpec, CorresK, DSpec, Lib, SepTactics, Sep_Algebra
  - `directories`: 14 entries
  - `theories` (unconditional only): 21 entries

## Patch contents

- `proof_ROOT.new` — replaces verification/l4v/proof/ROOT
  (181 lines, was 257)
- `downstream_root_edits.md` — 6 downstream sessions
  need ROOT-decl edits
- `thy_import_rewrites.md` — 45 qualified imports in 38 files need rewriting
- `apply.sh` — automation script (applies #1 and #3, #2 still needs manual review)

## ⚠ Known limitations of this generator

- **Conditional theory blocks** (`theories [condition = ..., quick_and_dirty]`)
  are NOT yet preserved. Quick-and-dirty / skip-proofs builds will break
  if you try them under this patch. Canonical ARM build is unaffected.
- **`document_files`** blocks (`Bisim`'s `root.tex` / `Makefile`) are dropped.
  PDF documentation of Bisim session won't be generated under HaskellMega.

## Expected wall delta (PREDICTION)

**Direct savings**:
- InfoFlowCBase ← Access source-re-exec eliminated: **-274s**
- InfoFlowCBase ← InfoFlow source-re-exec eliminated: **-365s**
- Smaller (DPolicy ← Access etc.): **-50–80s**
- **Direct savings subtotal**: ~700s

**Amplification cost** (uncertain):
- Downstream sessions' `+` chain grows from Refine's heap
  (1591s content) to HaskellMega's
  heap (2972s content, ~2× larger)
- ML state pollution may amplify CBaseRefine/CRefine/InfoFlow* wall
  by 10–30% (range from prior CBaseRefine swap experience)
- Estimated amplification cost: **+500–1500s**

**Net**: range -800s to +800s. **Empirical validation required.**

## Validation plan

1. Apply in a git worktree (mega-merge branch).
2. Dry-run topological check:
   `isabelle build -n -d verification/l4v HaskellMega`
   Cost: ~5s. Passes ⇒ ROOT structure is valid.
3. Build HaskellMega only (wipe its heap first):
   `isabelle build -d verification/l4v HaskellMega`
   Cost: ~50–90 min Isabelle wall. Compare to sum of 11 prior session walls.
4. Rebuild downstream (CBaseRefine, CRefine, InfoFlowCBase, InfoFlowC):
   Cost: ~2–3h. Compare to current heaps/build_log.txt.
5. Compute net delta. If regression > 200s, roll back.