# Move Simulator — Residual CSTR Edges & Structural Interventions

_Empirical ranking of cross-session theory duplications still present_
_after the CBaseRefine + CRefineSyscall ROOT swaps._

Source: [reports/session-duplication-scan.json](session-duplication-scan.json)
(post-swap `heaps/db-archive/*.db` BLOBs).

## Top 20 edges by cost

| # | Consumer | Origin (re-exec'd) | Cost (s) | Tier | Intervention |
|---:|---|---|---:|---|---|
| 1 | `CBaseRefine` | `CKernel` | 683 | TIER-3-REJECT | SWAP would regress (origin 700s < cur_parent 1591s) |
| 2 | `InfoFlowCBase` | `InfoFlow` | 365 | TIER-3-REJECT | SWAP would regress (origin 347s < cur_parent 2104s) |
| 3 | `InfoFlowCBase` | `Access` | 274 | TIER-3-REJECT | SWAP would regress (origin 301s < cur_parent 2104s) |
| 4 | `CBaseRefine` | `CSpec` | 187 | TIER-3-REJECT | SWAP would regress (origin 125s < cur_parent 1591s) |
| 5 | `DSpec` | `ASpec` | 87 | TIER-2-NEUTRAL | SWAP candidate (small margin): `DSpec = ASpec +` |
| 6 | `DBaseRefine` | `DSpec` | 77 | TIER-3-REJECT | SWAP would regress (origin 159s < cur_parent 947s) |
| 7 | `CBaseRefine` | `CParser` | 73 | TIER-3-REJECT | SWAP would regress (origin 67s < cur_parent 1591s) |
| 8 | `DPolicy` | `Access` | 65 | TIER-2-NEUTRAL | SWAP candidate (small margin): `DPolicy = Access +` |
| 9 | `CBaseRefine` | `Simpl-VCG` | 48 | TIER-3-REJECT | SWAP would regress (origin 46s < cur_parent 1591s) |

## Tier breakdown

### Tier 1 — VIABLE SWAP (next actionable)

_Total cost in this tier: 0s (0 edges)_

(none)

### Tier 2 — NEUTRAL SWAP (small margin, verify empirically)

_Total cost in this tier: 152s (2 edges)_

| Consumer | Origin | Cost (s) | Notes |
|---|---|---:|---|
| `DSpec` | `ASpec` | 87 | origin only 159s bigger than cur_parent; verify amplification |
| `DPolicy` | `Access` | 65 | origin only 154s bigger than cur_parent; verify amplification |

### Stale `sessions X` declarations (ROOT hygiene)

_Total cost in this tier: 0s (0 edges)_

(none)

### Tier 3 — SWAP REJECTED (needs SPLIT/MERGE/multi-edge)

_Total cost in this tier: 1708s (7 edges)_

| Consumer | Origin | Cost (s) | Notes |
|---|---|---:|---|
| `CBaseRefine` | `CKernel` | 683 | needs SPLIT / MERGE / multi-edge restructuring |
| `InfoFlowCBase` | `InfoFlow` | 365 | needs SPLIT / MERGE / multi-edge restructuring |
| `InfoFlowCBase` | `Access` | 274 | needs SPLIT / MERGE / multi-edge restructuring |
| `CBaseRefine` | `CSpec` | 187 | needs SPLIT / MERGE / multi-edge restructuring |
| `DBaseRefine` | `DSpec` | 77 | needs SPLIT / MERGE / multi-edge restructuring |
| `CBaseRefine` | `CParser` | 73 | needs SPLIT / MERGE / multi-edge restructuring |
| `CBaseRefine` | `Simpl-VCG` | 48 | needs SPLIT / MERGE / multi-edge restructuring |

## Shared-ancestor pattern (Target (b) candidates)

_Unrelated session pairs sharing source-re-executed content via_
_a common ancestor neither has on its `+` chain. Not fixable by a_
_single SWAP between the pair; fix is to promote the shared_
_ancestor (typically ExecSpec/ASpec/Lib) so it's `+`-merged into_
_all consuming sessions._

| Session A | Session B | Cost in A (s) | Likely shared ancestor |
|---|---|---:|---|
| `CKernel` | `ASpec` | 82 | Word_Lib, Monads, ML_Utils |
| `CKernel` | `DSpec` | 82 | Word_Lib, ML_Utils, Monads |
| `SepDSpec` | `AInvs` | 65 | ASpec, Word_Lib, ML_Utils |
| `InfoFlowCBase` | `DPolicy` | 64 | AInvs, Access, ASpec |

## MERGE candidates (near-empty sessions)

_A session X whose own mass is small AND mostly duplicated is a candidate_
_for collapse into its consumer C (CRefineSyscall pre-swap was the prototype:_
_100% dup, 1546s aggregate, fixed by the CRefineSyscall = CRefine + swap)._

_FEASIBILITY: C must have all of X's `+` ancestors on C's own `+` chain;_
_otherwise moving X's theories loses their heap-merged context._

| Session | Own (s) | Dup % | Top consumer | Feasible? | Notes |
|---|---:|---:|---|:---:|---|
| `DSpec` | 159 | 100% | `ASpec` | ✗ | DSpec is `+` parent of ['SepDSpec'] (multi-step refactor) |
| `CSpec` | 125 | 100% | `CBaseRefine` | ✗ | `CBaseRefine` `+` chain missing ['CKernel', 'CParser', 'Simpl-VCG']; CSpec is `+` parent of ['SimplExport'] (multi-step refactor) |
| `DBaseRefine` | 80 | 96% | `DSpec` | ✗ | `DSpec` `+` chain missing ['AInvs', 'ASpec']; DBaseRefine is `+` parent of ['DRefine'] (multi-step refactor) |
| `CParser` | 67 | 100% | `CBaseRefine` | ✗ | `CBaseRefine` `+` chain missing ['Simpl-VCG']; CParser is `+` parent of ['CLib', 'CKernel', 'AsmRefine', 'AutoCorres'] (multi-step refactor) |
| `Simpl-VCG` | 46 | 100% | `CBaseRefine` | ✗ | Simpl-VCG is `+` parent of ['CParser'] (multi-step refactor) |

## Methodology caveats

- **Wall savings estimates ignore amplification offsets.** A SWAP
  promotes one session to `+` parent and demotes another to `sessions X` —
  the demoted side's content then source-re-executes in the consumer's
  enlarged ML state, costing some wall. We report `cost - cur_parent_own_total`
  as a first-order net; the empirical CBaseRefine swap showed actual gain
  was 1.3× the first-order prediction (better than naive estimate due to GC
  reduction). So Tier 1 estimates are conservative.
- **Single-session-pair view.** A residual edge may be fixable only if
  multiple edges change together. The simulator scores each in isolation.
- **Tier 3 ≠ unsolvable.** It just means single-edge SWAP is wrong; the
  fix may involve creating a new intermediate session (SPLIT), pruning
  imports (so `sessions X` actually pulls less content), or — as a
  long-term path — Isabelle gaining multi-parent `+` support.
