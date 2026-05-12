# Single session-DAG analysis
Replaces the previous two-graph (theory + session) model with a single
session-granularity DAG. Edge weights collapse the theory-level
reprocessing cost down to inter-session aggregates.

## Construction time

| Step | Wall (s) |
|---|---:|
| parse ROOT | 0.022 |
| parse build_log.txt | 0.000 |
| load 29 theory_timings BLOBs | 0.149 |
| build graph + edge weights | 0.000 |
| **total** | **0.172** |

Graph size: 37 nodes, 52 edges (vs prior theory-DAG 1094 nodes / 1961 edges).

## Top CSTR swap recommendations (est_aggregate_saving > 50s)

| Session | Current parent | Swap with | Reprocess cost | Est. post-swap cost | Est. saving | Shared theories |
|---|---|---|---:|---:|---:|---:|
| CBaseRefine | CSpec | Refine | 3537.9s | 253.6s | **3284.3s** | 45 |
| CRefineSyscall | CBaseRefine | CRefine | 1546.0s | 0.0s | **1546.0s** | 43 |

## Redundant `sessions` clauses (parent ancestry overlap, current graph)

| Session | Redundant `sessions` entry | Why |
|---|---|---|
| Refine | AInvs | AInvs already in parent chain of Refine |
| SimplExportAndRefine | SimplExport | SimplExport already in parent chain of SimplExportAndRefine |
| SysInit | SepDSpec | SepDSpec already in parent chain of SysInit |
| SysInit | DSpecProofs | DSpecProofs already in parent chain of SysInit |
| AsmRefineTest | CParser | CParser already in parent chain of AsmRefineTest |
| AutoCorresSEL4 | CParser | CParser already in parent chain of AutoCorresSEL4 |

## New redundancies created by applying the swaps above

These clauses become no-ops only AFTER the recommended swaps are applied:

| Session | Newly-redundant `sessions` entry | Why |
|---|---|---|
| InfoFlowCBase | Refine | Refine already in parent chain of InfoFlowCBase |

## Top 15 sessions edges by reprocess weight

| Source (upstream) | Destination | Kind | Weight (s) | Shared thys |
|---|---|---|---:|---:|
| Refine | CBaseRefine | sessions | 3537.9 | 45 |
| CRefine | CRefineSyscall | sessions | 1546.0 | 43 |
| InfoFlow | InfoFlowCBase | sessions | 370.9 | 42 |
| Access | InfoFlowCBase | sessions | 273.1 | 27 |
| ASpec | CKernel | sessions | 87.3 | 56 |
| Access | DPolicy | sessions | 60.9 | 7 |
| ASpec | CorresK | sessions | 0.0 | 0 |
| ExecSpec | CorresK | sessions | 0.0 | 0 |
| ASpec | CorresK | sessions | 0.0 | 0 |
| ExecSpec | CorresK | sessions | 0.0 | 0 |
| CorresK | Refine | sessions | 0.0 | 0 |
| AInvs | Refine | sessions | 0.0 | 0 |
| CorresK | BaseRefine | sessions | 0.0 | 0 |
| AutoCorres | CBaseRefine | sessions | 0.0 | 0 |
| Refine | InfoFlowCBase | sessions | 0.0 | 0 |
