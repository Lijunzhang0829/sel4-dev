# Spec downstream map — what your strengthening will rebuild

When you change a `.thy` under `spec/abstract/` or
`proof/invariant-abstract/`, the change cascades through every session
that transitively imports it. This document maps the cascade so you can
budget verification time before starting.

Source: `verification/l4v/spec/ROOT` and `verification/l4v/proof/ROOT`.

## Session dependency tree

```
ASpec        ┐
             ├─ AInvs ──┬─ BaseRefine ─── Refine ──┬─ RefineOrphanage
             │          │                          └─→ CBaseRefine ─── CRefine ──┬─ CRefineSyscall
             │          │                                                        ├─ AutoCorresCRefine
             │          │                                                        └─→ InfoFlowCBase ─── InfoFlowC
             │          ├─ Access ─── InfoFlow ───────────────────────────────────↗
             │          ├─ DBaseRefine ─── DRefine ─── DPolicy
             │          └─ Bisim
             ↑
spec/abstract/

CSpec        ─── CBaseRefine ─── CRefine ──┬─ CRefineSyscall
                                           ├─ AutoCorresCRefine
                                           └─→ InfoFlowCBase ─── InfoFlowC

DSpec        ─── SepDSpec ─── DSpecProofs
```

Arrows `→` mark `sessions` declarations (cross-session imports without
parent-heap inheritance — they still trigger rebuilds).

## What rebuilds for each change

| You change a file in… | Sessions that need re-verification (in order) |
|---|---|
| `spec/abstract/` | ASpec → AInvs → BaseRefine → Refine → CBaseRefine → CRefine → {Access, DBaseRefine, Bisim, InfoFlowCBase} → {InfoFlow, DRefine, InfoFlowC} → DPolicy |
| `proof/invariant-abstract/` | AInvs → (same downstream as ASpec, minus ASpec itself) |
| `proof/refine/` (state-relation lemmas) | BaseRefine → Refine → CBaseRefine → CRefine → InfoFlowCBase → InfoFlowC |
| `proof/access-control/` | Access → InfoFlow → InfoFlowC |
| `proof/crefine/` | CRefine → {CRefineSyscall, AutoCorresCRefine, InfoFlowCBase} → InfoFlowC |

A change to `spec/abstract/` realistically forces the entire functional
verification chain (excluding `capDL` / Bisim if you don't touch what
they use). Budget accordingly.

## Wall-time budgets (rough, this machine, baseline branch)

| Session | Clean build wall |
|---|---|
| ASpec | ~3 min |
| AInvs | ~30 min |
| BaseRefine + Refine | ~1 h 17 min |
| Access | ~25 min |
| InfoFlow | ~45 min |
| CBaseRefine + CRefine | ~3 h |
| DBaseRefine + DRefine | ~30 min |
| DPolicy | ~15 min |
| Bisim | ~15 min |
| InfoFlowCBase + InfoFlowC | ~1 h+ |

These are clean-build numbers. Incremental rebuilds (when only a few
downstream files changed) are typically 20–60% of clean. Heap-cache
state matters; check `heaps/db-live/<SESSION>.db` mtime before assuming
"incremental".

## Verification strategy by change scope

### Strengthening that affects ≤ 2 files in invariant-abstract

1. `check-theory.sh <changed_file> AInvs --patch <p>` → must be `OK`.
2. `check-theory.sh <changed_file> AInvs --apply <p>`.
3. **Quick downstream scan:** `grep -rln '\b<lemma_name>\b'
   verification/l4v/proof/{refine,access-control,drefine,bisim}/` —
   pick the top 3 consumers and run `check-theory.sh` against each
   (no patch needed; they pick up the patched dependency via the
   session heap, which will rebuild for that file).
4. If consumers fail with `wp` unification errors → write follow-up
   patches per consumer.
5. Full `isabelle build AInvs Refine Access` only after the changed
   file and at least one consumer per downstream session are green.

### Strengthening that affects ≥ 3 files or touches spec/abstract/

1. Same Step 1–2 as above.
2. Skip the quick downstream scan — too many consumers to enumerate.
3. Schedule a full `isabelle build` of every downstream session in
   one batch. Run overnight or in background; treat it as the
   acceptance gate. Don't iterate on spec patches during the build.

### Strengthening in `proof/refine/` only

C-side (CRefine and below) is insulated from AInvs by the
`BaseRefine → CBaseRefine` boundary. But CRefine still imports Refine,
so functional changes in `_R` lemmas DO cascade through CRefine.

If you're strengthening a `_R` lemma:
- AInvs is *not* affected — don't rebuild it.
- `Refine` is the immediate session; `check-theory.sh <file> Refine
  --patch <p>` is the first gate.
- CRefine and InfoFlowCBase will need rebuild if the lemma is used in
  ccorres proofs (check with `grep '<lemma>' verification/l4v/proof/crefine/`).

## When to abandon a strengthening attempt

If you find yourself writing follow-up patches in ≥ 4 downstream files
to keep them green after a single strengthening, the spec patch is
probably too aggressive — the existing weak form was load-bearing as a
unification surface, not just redundancy. Either:

- Pivot to the conservative move: keep the weak lemma but tag it
  `[wp del]`, or add the strong form *alongside* the weak one (no
  deletions), or
- Revert and pick a different candidate. Don't grind through dozens of
  downstream fixes for one spec edit.
