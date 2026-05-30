# Lemma inventory — baseline summary

_Generated: 2026-05-29 21:31:52_


## Scope
- l4v root: `/home/lijun/seL4-docker-main/verification/l4v`
- arch: `ARM`
- excluded arch dirs: `AARCH64, ARM_HYP, RISCV64, X64`
- excluded subtrees: `.git, EVTutorial, camkes, doc, tutorial`
- DB: `/home/lijun/seL4-docker-main/reports/golden-baseline/lemma-inventory.db`

## Totals
- sessions: **56**
- theories scanned: **1113**
- lemma-like declarations: **36567**
- with `sorry`/`oops`/`sledgehammer` in body: **109**
- anonymous lemmas: **627**
- skipped (excluded): **855**
- unassigned (no owning session): **6**

## Lemmas per session (top 30)
| session | theories | lemmas | sorry/oops |
|---|---:|---:|---:|
| AInvs | 75 | 5730 | 0 |
| Refine | 37 | 5029 | 0 |
| Word_Lib | 51 | 2828 | 0 |
| Monads | 52 | 2776 | 2 |
| CRefine | 44 | 2733 | 2 |
| InfoFlow | 42 | 2633 | 0 |
| Lib | 46 | 2065 | 20 |
| CParser | 70 | 1737 | 8 |
| AutoCorres | 55 | 1484 | 9 |
| DRefine | 18 | 1333 | 0 |
| Simpl | 29 | 1322 | 11 |
| Access | 27 | 1267 | 0 |
| ExecSpec | 22 | 904 | 0 |
| SysInit | 14 | 651 | 0 |
| Sep_Algebra | 29 | 577 | 9 |
| CLib | 9 | 482 | 6 |
| AsmRefine | 12 | 467 | 0 |
| DSpecProofs | 10 | 455 | 5 |
| Concurrency | 5 | 388 | 4 |
| TakeGrant | 6 | 341 | 5 |
| InfoFlowC | 6 | 284 | 0 |
| SepDSpec | 6 | 226 | 0 |
| RefineOrphanage | 1 | 180 | 0 |
| LibTest | 17 | 143 | 4 |
| SysInitExamples | 2 | 111 | 0 |
| Bisim | 2 | 84 | 0 |
| DPolicy | 1 | 66 | 0 |
| AsmRefineTest | 6 | 36 | 3 |
| AutoCorresCRefine | 1 | 33 | 0 |
| ASpec | 9 | 29 | 0 |

## Lemma-kind distribution
| kind | count |
|---|---:|
| lemma | 33756 |
| lemmas | 2667 |
| theorem | 79 |
| schematic_goal | 40 |
| corollary | 25 |

## Top 20 theories by lemma count
| theory | lemmas |
|---|---:|
| `proof/refine/ARM/TcbAcc_R.thy` | 524 |
| `lib/Lib.thy` | 456 |
| `proof/refine/ARM/CSpace1_R.thy` | 400 |
| `lib/Word_Lib/More_Word.thy` | 380 |
| `lib/Word_Lib/Reversed_Bit_Lists.thy` | 380 |
| `proof/invariant-abstract/Invariants_AI.thy` | 378 |
| `proof/refine/ARM/CNodeInv_R.thy` | 369 |
| `proof/invariant-abstract/CSpace_AI.thy` | 360 |
| `proof/refine/ARM/CSpace_R.thy` | 356 |
| `spec/design/Structures_H.thy` | 356 |
| `spec/design/Types_H.thy` | 342 |
| `lib/Word_Lib/Bits_Int.thy` | 312 |
| `proof/invariant-abstract/DetSchedSchedule_AI.thy` | 303 |
| `lib/Monads/trace/Trace_RG.thy` | 297 |
| `proof/refine/ARM/Invariants_H.thy` | 297 |
| `lib/Word_Lib/Word_Lemmas.thy` | 289 |
| `proof/invariant-abstract/ARM/ArchVSpace_AI.thy` | 287 |
| `lib/Monads/nondet/Nondet_VCG.thy` | 285 |
| `lib/Monads/trace/Trace_VCG.thy` | 285 |
| `proof/refine/ARM/Retype_R.thy` | 271 |

## Duplicate lemma names across theories (top 20)
(Same name in multiple theories — check whether intentional or a hide_fact target.)
| name | occurrences |
|---|---:|
| `descendants` | 12 |
| `valid_list_post` | 12 |
| `foo` | 9 |
| `no_0_n` | 6 |
| `parency` | 6 |
| `untyped_inc_n` | 6 |
| `untyped_mdb_n` | 6 |
| `valid_list_post_no_parent` | 6 |
| `bind_eqI` | 5 |
| `finite_depth` | 5 |
| `global_data_defs` | 5 |
| `globals_list_def` | 5 |
| `list_empty` | 5 |
| `next_child` | 5 |
| `next_not_child` | 5 |
| `next_sib` | 5 |
| `next_slot` | 5 |
| `untyped_inc` | 5 |
| `valid_arch_caps` | 5 |
| `CombineStrip'` | 4 |
