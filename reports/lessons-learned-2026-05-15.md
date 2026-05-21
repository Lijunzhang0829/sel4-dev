# Lessons Learned — 2026-05-15

Correction event: a contact with the seL4 maintainer (Klein, via
devel@sel4.systems) refuted two core claims that had propagated through
our analytical chain. This document is the single canonical record of
what was wrong, what was right, and the rule-of-inference to apply
going forward.

## What was wrong

### Error 1 — Conflated session-level static dep with rebuild trigger

Our docs repeatedly framed "session X has Y as a `sessions` declaration
or in dep closure" as equivalent to "a change in Y invalidates X". The
maintainer's correction:

> "ASpec does not build on ExecSpec as its base session. ASpec will only
> be rebuilt if the content hash of those theories changes. You can try
> it out: build ASpec, then change something in e.g. CSpace.lhs in
> Haskell, and rebuild ASpec. The Makefile will regenerate all ExecSpec
> theories, and Isabelle will recognise that no relevant content has
> changed and not rebuild ASpec."

Isabelle uses **per-theory content-hash invalidation**, not session-
level dep-set membership.

A Haskell edit:
1. Triggers the Makefile to regenerate `spec/design/X.thy`
2. Isabelle compares CONTENT HASH of X against last build
3. If unchanged (e.g., the edit only affected layout / comments) → no
   downstream rebuild
4. If changed → downstream sessions that import X have their content
   hash recomputed → only those that ACTUALLY import X get marked
   stale → only those rebuild

This is much finer-grained than "session X depends on Y → Y change
invalidates X".

### Error 2 — Misattributed source-re-execution to rebuild mechanism

We described `sessions ExecSpec` in ASpec's ROOT as "causing the
entire 315s of ExecSpec to be source-re-executed in ASpec's ML state
every time ASpec builds." The maintainer:

> "this makes no sense. ML state is irrelevant here."

**ML state matters for WALL TIME WITHIN A SINGLE BUILD** (the BLOB
measurements correctly show source-re-execution wall in the consuming
session) — but ML state has nothing to do with whether the consuming
session gets RE-BUILT in response to an upstream edit. The two things
were conflated.

## What remains accurate

- **CSTR scan data** (BLOB measurements). Per-build source-re-execution
  cost IS real. The 5585s pre-swap → 1768s post-swap reduction in
  aggregate dup overhead is a per-build wall measurement, valid
  regardless of rebuild trigger mechanism.
- **CBaseRefine / CRefineSyscall ROOT swap empirical results**. The
  −8389s (−33.1%) canonical wall reduction was measured directly via
  `isabelle build` of the canonical session set. No claim about
  rebuild triggering was needed to validate it.
- **Static 2D theory classification** (source pillar × dep pillar
  class). These are pure dependency-graph facts. The cells
  `proof × needs-H-AND-C = 45 / 1931s`, `proof × needs-C-NOT-H = 0`,
  etc. are all correct as STATIC dep facts.
- **Misalignment candidates** (theories in cross-cut sessions whose
  static dep doesn't include c). These are accurate static-dep
  findings; whether moving them improves anything depends on per-build
  effects (TBD via empirical test), not on rebuild triggering.

## What was retracted

- **KernelTypes extraction proposal** (`experiments/kerneltypes-
  extraction-proposal.md`). Status updated to WITHDRAWN; the
  proposal's primary motivation (haskell-change-locality improvement
  for ASpec/AInvs) does not hold under content-hash gating.
- **The change-locality matrix** in
  `reports/layer2-theory/theory-baseline-2026-05-14.md`. Reframed as
  "static-dependency reach matrix" with explicit caveat that it is an
  UPPER BOUND, not a typical-case rebuild estimate.
- **Target (d) wall impact estimates** in
  `reports/layer1-root-swap/critical-path-summary.md` and
  `critical-path-haskell.md`. Top-of-file caveats added pointing to
  this lessons-learned doc.

## Rule of inference going forward

Before claiming "edit to X invalidates session Y" or computing
"sessions rebuilt when pillar X changes":

1. **Identify the SPECIFIC theory** the edit regenerates (or which
   `.thy` file it modifies directly).
2. **Check whether Y transitively imports THAT theory** (not just
   "Y's dep closure touches pillar X").
3. If yes, also check whether the regeneration changes the theory's
   content hash. (Often Haskell-side changes that only affect
   formatting / type-signature-equivalent reorderings produce
   identical generated `.thy` content → content hash unchanged → no
   downstream effect.)
4. **Distinguish two cost models** explicitly:
   - **Per-build wall** (CSTR amplification, ML state pollution) —
     measured by BLOB inspection, valid as a static fact about each
     individual build.
   - **Rebuild frequency** (how often the session needs to rebuild as
     a function of user activity) — measured by content-hash gating,
     not by static dep-set membership.

If a claim mixes these, it's probably wrong.

## Contact / source

The correction came from a private email exchange with Klein after the
user (Li-Jun Zhang) sent a question to devel@sel4.systems asking about
the ASpec→ExecSpec session structure. The full reply is in the user's
mailbox; key excerpts are quoted verbatim above and in the WITHDRAWN
banner on `experiments/kerneltypes-extraction-proposal.md`.
