# Lemma Optimization Targets — Ranked by score

_For each top-cost theory in the post-swap canonical build, rank_
_individual lemmas by `log(1+theory_wall) × size × pressure_weight`._
_The score biases toward: lemmas in expensive theories, large_
_lemmas, and unhinted search-heavy tactics (the leverage points_
_per the principle: "reduce search path / give precise direction_
_to shorten lemma time")._

Pressure weights: high=3.0, medium=1.0, low=0.3.

Optimization paths:
- **HAMMER**: short search-heavy tactic — sledgehammer for metis reconstruction
- **HAMMER+ISAR**: long search-heavy chain — sledgehammer specific steps + Isar decompose
- **HINT-ADD**: hinted tactic — minimize rule set to shrink search
- **SUBPROOF-DRILL**: long Isar proof — locate slow inner `have` via proof-timing
- **DEFER**: theory wall too low for measurable wins
- **LOW-PRIORITY**: low-pressure `by`; unlikely to move
- **INSPECT**: needs case-by-case look

## Top 30 lemma targets (overall)

| # | Lemma | Theory (wall) | Size (L) | Top | Pressure | Score | Path |
|---:|---|---|---:|---|---|---:|---|
| 1 | `cteSwap_chain` | `CNodeInv_R` (145s) | 1016 | proof | high | 15191 | SUBPROOF-DRILL |
| 2 | `cteSwap_dlist_helper` | `CNodeInv_R` (145s) | 792 | proof | high | 11842 | SUBPROOF-DRILL |
| 3 | `cteInsert_corres` | `CSpace1_R` (94s) | 548 | using | high | 7484 | INSPECT |
| 4 | `cnc_tcb_helper` | `Retype_C` (154s) | 474 | proof | high | 7172 | SUBPROOF-DRILL |
| 5 | `cteSwap_corres` | `CSpace1_R` (94s) | 466 | supply | high | 6364 | INSPECT |
| 6 | `inv_untyped_corres'` | `Untyped_R` (137s) | 405 | apply | high | 5983 | HAMMER+ISAR |
| 7 | `createNewCaps_Cons` | `Detype_R` (87s) | 433 | proof | high | 5814 | SUBPROOF-DRILL |
| 8 | `rec_del_corres` | `CNodeInv_R` (145s) | 380 | proof | high | 5682 | SUBPROOF-DRILL |
| 9 | `transferCapsLoop_ccorres` | `Ipc_C` (150s) | 347 | unfolding | high | 5224 | INSPECT |
| 10 | `cteMove_valid_mdb_helper` | `CNodeInv_R` (145s) | 319 | proof | high | 4770 | SUBPROOF-DRILL |
| 11 | `transferCaps_corres` | `Tcb_R` (82s) | 350 | proof | high | 4634 | SUBPROOF-DRILL |
| 12 | `cteInsert_mdb'` | `CSpace_R` (103s) | 328 | apply | high | 4570 | HAMMER+ISAR |
| 13 | `cteMove_corres` | `CSpace_R` (103s) | 315 | supply | high | 4388 | INSPECT |
| 14 | `delete_invs'` | `Detype_R` (87s) | 307 | using | high | 4122 | INSPECT |
| 15 | `decodeUntypedInvocation_corres` | `Untyped_R` (137s) | 275 | proof | high | 4063 | SUBPROOF-DRILL |
| 16 | `absHeap_correct` | `ADT_H` (56s) | 333 | proof | high | 4040 | SUBPROOF-DRILL |
| 17 | `createObject_ccorres` | `Retype_C` (154s) | 267 | proof | high | 4040 | SUBPROOF-DRILL |
| 18 | `cteInsert_simple_corres` | `CSpace_R` (103s) | 272 | using | high | 3789 | INSPECT |
| 19 | `emptySlot_corres` | `Finalise_R` (221s) | 233 | unfolding | high | 3775 | INSPECT |
| 20 | `createObjects_ccorres_user_data` | `Retype_C` (154s) | 248 | proof | high | 3752 | SUBPROOF-DRILL |
| 21 | `cancelBadgedSends_ccorres` | `Recycle_C` (56s) | 290 | apply | high | 3524 | HAMMER+ISAR |
| 22 | `mdb_chunked_n'` | `Untyped_R` (137s) | 236 | using | high | 3487 | INSPECT |
| 23 | `createObjects_ccorres_pde` | `Retype_C` (154s) | 207 | proof | high | 3132 | SUBPROOF-DRILL |
| 24 | `Arch_createObject_ccorres` | `Retype_C` (154s) | 207 | proof | high | 3132 | SUBPROOF-DRILL |
| 25 | `decodeCNodeInvocation_corres` | `CNodeInv_R` (145s) | 202 | apply | high | 3020 | HAMMER+ISAR |
| 26 | `insertNewCap_corres` | `Untyped_R` (137s) | 203 | apply | high | 2999 | HAMMER+ISAR |
| 27 | `next_m_n` | `CSpace1_R` (94s) | 216 | using | high | 2950 | INSPECT |
| 28 | `createNewCaps_valid_cap` | `Retype_R` (95s) | 215 | proof | high | 2941 | SUBPROOF-DRILL |
| 29 | `retype_state_relation` | `Retype_R` (95s) | 202 | proof | high | 2763 | SUBPROOF-DRILL |
| 30 | `createObjects_valid_pspace'` | `Retype_R` (95s) | 202 | apply | high | 2763 | HAMMER+ISAR |

## Distribution by recommended path

- **HAMMER**: 109 lemmas
- **HAMMER+ISAR**: 303 lemmas
- **HINT-ADD**: 552 lemmas
- **SUBPROOF-DRILL**: 58 lemmas
- **INSPECT**: 1973 lemmas
- **LOW-PRIORITY**: 837 lemmas
- **DEFER**: 0 lemmas

## Top HAMMER+ISAR targets (the high-leverage path)

| Lemma | Theory | Size | Pressure | Top tactic | Rationale |
|---|---|---:|---|---|---|
| `inv_untyped_corres'` | `Untyped_R` | 405 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `cteInsert_mdb'` | `CSpace_R` | 328 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `cancelBadgedSends_ccorres` | `Recycle_C` | 290 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `decodeCNodeInvocation_corres` | `CNodeInv_R` | 202 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `insertNewCap_corres` | `Untyped_R` | 203 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `createObjects_valid_pspace'` | `Retype_R` | 202 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `invokeUntyped_invs''` | `Untyped_R` | 173 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `finaliseCap_ccorres` | `Finalise_C` | 191 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `lookupIPCBuffer_ccorres` | `SyscallArgs_C` | 180 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |
| `resetUntypedCap_corres` | `Untyped_R` | 152 | high | apply | long apply chain with unhinted search — sledgehammer the slow step OR Isar-decompose into named `have` blocks to bound each tactic's search space |

## Methodology notes

- **theory_wall** is the elapsed in the theory's ORIGIN session
  (where it's first compiled), not the amplified cost in CSTR-
  duplicating downstream sessions. Optimizing a lemma reduces
  its origin cost AND (proportionally) the cost in every CSTR
  consumer — the multiplier kicks in for free.
- **size** is the line count of the proof body, from `proof_parser`.
  It correlates with optimization surface, but is NOT a direct
  cost proxy. A 5-line `by metis (...)` can be slower than a
  500-line structured Isar proof. Run `scan-slow-proofs.sh` to
  get sorry-cost (the gold standard) before committing budget.
- **search_pressure** is a body-scan: high if any unhinted
  `auto/force/fastforce/blast/metis/smt` appears in the proof body,
  medium if hinted, low otherwise.
- This report is PRE-EMPIRICAL: it ranks candidates before any
  Isabelle measurement. Use `scan-slow-proofs.sh` to refine the
  top-N down to the lemmas with actual high sorry-cost before
  spending the skill's $100/session budget. Excluded 2
  pure-ML theories (Kernel_C / Substitute) and SimplExport* —
  those have no lemmas to optimize.

_Skipped 1 theories where .thy file was not found (likely tools/library)._
