# Promote Simulator — Shared-Ancestor Heap-Merge Candidates

_Target (b) from the original critical-path summary: for each_
_origin session whose theories are source-re-executed in 2+_
_downstream consumers, estimate the wall savings of promoting_
_the origin to a `+`-merged heap ancestor of each consumer._

Filter: candidates with potential savings ≥ 100s only
(per user 2026-05-17 instruction — no small wins).

**Method**: per shared theory T appearing in K sessions, current
wall = sum of elapsed across K BLOBs. Best achievable = max(elapsed)
(T runs once in the lightest ambient ML state, heap-loaded elsewhere).
Savings = sum − max. This is an UPPER BOUND ignoring amplification.

## Summary

| Origin | #shared theories | Total cost (s) | Best achievable (s) | Potential savings (s) | Consumers |
|---|---:|---:|---:|---:|---|
| `CKernel` | 1 | 1492 | 786 | **707** | CBaseRefine |
| `InfoFlow` | 40 | 745 | 409 | **337** | InfoFlowCBase |
| `Access` | 27 | 628 | 297 | **331** | InfoFlowCBase, DPolicy |
| `CSpec` | 7 | 340 | 197 | **143** | CBaseRefine |

_Cumulative potential: **1518s** across 4 candidates._

## Per-candidate feasibility

### `CKernel` — potential savings 707s

_1 theories shared, total 1492s wall across all BLOBs_

| Consumer | #thys re-exec'd | Cost in consumer (s) | Verdict | Explanation |
|---|---:|---:|---|---|
| `CBaseRefine` | 1 | 785.6 | SWAP-REJECTED | swap would regress (origin 700s ≤ parent 1591s); insertion would require restructuring `Refine` to inherit from `CKernel` |

**Roll-up**:
- SWAP-REJECTED: 1/1 consumers
- **Viable savings (single-edge swap or stale-decl cleanup)**: 0s
- **Blocked by single-`+` constraint**: 786s

### `InfoFlow` — potential savings 337s

_40 theories shared, total 745s wall across all BLOBs_

| Consumer | #thys re-exec'd | Cost in consumer (s) | Verdict | Explanation |
|---|---:|---:|---|---|
| `InfoFlowCBase` | 40 | 384.5 | SWAP-REJECTED | swap would regress (origin 347s ≤ parent 2104s); insertion would require restructuring `CRefine` to inherit from `InfoFlow` |

**Roll-up**:
- SWAP-REJECTED: 1/1 consumers
- **Viable savings (single-edge swap or stale-decl cleanup)**: 0s
- **Blocked by single-`+` constraint**: 384s

### `Access` — potential savings 331s

_27 theories shared, total 628s wall across all BLOBs_

| Consumer | #thys re-exec'd | Cost in consumer (s) | Verdict | Explanation |
|---|---:|---:|---|---|
| `InfoFlowCBase` | 27 | 278.4 | SWAP-REJECTED | swap would regress (origin 301s ≤ parent 2104s); insertion would require restructuring `CRefine` to inherit from `Access` |
| `DPolicy` | 7 | 61.3 | SWAP-VIABLE | swap candidate (origin 301s > parent 147s, margin +154s) |

**Roll-up**:
- SWAP-REJECTED: 1/2 consumers
- SWAP-VIABLE: 1/2 consumers
- **Viable savings (single-edge swap or stale-decl cleanup)**: 61s
- **Blocked by single-`+` constraint**: 278s

### `CSpec` — potential savings 143s

_7 theories shared, total 340s wall across all BLOBs_

| Consumer | #thys re-exec'd | Cost in consumer (s) | Verdict | Explanation |
|---|---:|---:|---|---|
| `CBaseRefine` | 7 | 189.4 | SWAP-REJECTED | swap would regress (origin 125s ≤ parent 1591s); insertion would require restructuring `Refine` to inherit from `CSpec` |

**Roll-up**:
- SWAP-REJECTED: 1/1 consumers
- **Viable savings (single-edge swap or stale-decl cleanup)**: 0s
- **Blocked by single-`+` constraint**: 189s

## Bottom line

Across all candidates with potential savings ≥ 100s:
- **Viable (single-edge structural change)**: 61s
- **Blocked by Isabelle single-`+`-parent rule**: 1638s

If `viable` is ~0 and `blocked` is large, the residual cost is the
irreducible CSTR floor under the current Isabelle model — same
conclusion as move_simulator / split_simulator but now quantified
at the theory-shared-cost granularity rather than edge granularity.

## What 'STALE-DECL' would unlock

A `STALE-DECL` consumer has the origin ALREADY in its `+` chain,
but the origin's theories still appear in the consumer's BLOB —
meaning the `sessions <origin>` declaration in the consumer's ROOT
is redundant. Removing it doesn't change wall (the heap-merge is
already happening) but cleans up the ROOT and may avoid spurious
re-execution if Isabelle's resolution prefers `sessions` over `+`.

These are 0-risk ROOT cleanups but won't save wall.
