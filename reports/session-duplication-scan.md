# Session-level theory duplication scan

_Source: `heaps/db-archive/*.db` `theory_timings` BLOBs. Each BLOB lists every theory the session loaded/executed, with elapsed/cpu/gc. A theory appearing in multiple sessions' BLOBs is being **reprocessed** in each (empirically — see CBaseRefine vs Refine.Finalise_R: 225.6s → 424.3s with different cpu+gc, ruling out heap-load attribution)._

## Headline numbers

- Sessions scanned: **28**
- Unique theory names across all sessions: **845**
- Theories duplicated across ≥2 sessions: **464**  (54.9%)
- Sum of all per-theory elapsed across all sessions: **19507.1s**
- Sum of duplication overhead (= sum_elapsed_across_sessions − max_single_session): **5585.3s**

> **Interpretation**: the duplication overhead is an upper-bound estimate of wall time potentially recoverable if each duplicated theory were processed exactly once across the session graph. Real recoverable wall is lower because of intra-session 8-thread parallelism (factor 3-6×) and because some re-execution may serve genuine purposes (locale re-interpretation in different ML contexts).

## Per-session duplication footprint

Sessions ranked by duplicated-elapsed (i.e., how much of THIS session's per-theory wall is on theories also processed elsewhere).

| session | #theories | own Σelapsed | dup #thys | dup Σelapsed | dup % | top co-appearing sessions |
|---|---:|---:|---:|---:|---:|---|
| CBaseRefine | 304 | 5598.7 | 274 | 5519.3 | 98.6% | Refine:3538s; AInvs:1416s; BaseRefine:342s |
| CRefine | 52 | 2334.1 | 44 | 1882.6 | 80.7% | CRefineSyscall:1867s; Refine:16s |
| Refine | 48 | 1733.8 | 47 | 1723.0 | 99.4% | CBaseRefine:1693s; InfoFlowC:26s; CRefine:4s |
| CRefineSyscall | 44 | 1546.1 | 43 | 1546.0 | 100.0% | CRefine:1546s |
| AInvs | 119 | 1052.6 | 119 | 1052.6 | 100.0% | CBaseRefine:1053s; SepDSpec:70s; DSpec:1s |
| InfoFlowCBase | 70 | 647.9 | 69 | 644.0 | 99.4% | InfoFlow:371s; Access:273s; DPolicy:63s |
| InfoFlow | 46 | 367.7 | 42 | 349.1 | 94.9% | InfoFlowCBase:349s |
| BaseRefine | 63 | 283.6 | 63 | 283.6 | 100.0% | CBaseRefine:284s |
| Access | 28 | 306.7 | 27 | 281.8 | 91.9% | InfoFlowCBase:282s; DPolicy:59s |
| ASpec | 107 | 270.5 | 105 | 270.0 | 99.8% | CBaseRefine:185s; DSpec:93s; CKernel:84s |
| DSpec | 83 | 174.4 | 83 | 174.4 | 100.0% | ASpec:93s; CKernel:84s; DBaseRefine:81s |
| DBaseRefine | 19 | 94.5 | 18 | 89.8 | 95.1% | DSpec:90s |
| CKernel | 61 | 785.5 | 56 | 87.3 | 11.1% | ASpec:87s; DSpec:87s |
| SepDSpec | 56 | 104.7 | 29 | 71.3 | 68.1% | CBaseRefine:71s; AInvs:71s; Refine:1s |
| DPolicy | 8 | 81.3 | 7 | 60.9 | 75.0% | Access:61s; InfoFlowCBase:61s |
| InfoFlowC | 7 | 234.0 | 1 | 30.0 | 12.8% | Refine:30s |
| CParser | 35 | 66.2 | 2 | 1.4 | 2.1% | ASpec:1s; DSpec:1s |
| SimplExport | 7 | 646.7 | 1 | 0.3 | 0.1% | AInvs:0s; CBaseRefine:0s; DSpec:0s |
| Bisim | 6 | 27.6 | 0 | 0.0 | 0.0% |  |
| CSpec | 8 | 148.9 | 0 | 0.0 | 0.0% |  |
| DRefine | 18 | 163.4 | 0 | 0.0 | 0.0% |  |
| DSpecProofs | 12 | 27.0 | 0 | 0.0 | 0.0% |  |
| HOL | 114 | 379.4 | 0 | 0.0 | 0.0% |  |
| Pure | 3 | 1.3 | 0 | 0.0 | 0.0% |  |
| RefineOrphanage | 1 | 49.1 | 0 | 0.0 | 0.0% |  |
| Simpl-VCG | 18 | 44.7 | 0 | 0.0 | 0.0% |  |
| SimplExportAndRefine | 8 | 2263.7 | 0 | 0.0 | 0.0% |  |
| Word_Lib | 66 | 73.2 | 0 | 0.0 | 0.0% |  |

## Top 30 theories by total cost across sessions

| theory | #sessions | Σelapsed (all) | max single | overhead estimate | sessions (elapsed) |
|---|---:|---:|---:|---:|---|
| `Refine.Finalise_R` | 2 | 649.8 | 424.3 | 225.6 | CBaseRefine:424s; Refine:226s |
| `Refine.Retype_R` | 2 | 382.0 | 294.4 | 87.7 | CBaseRefine:294s; Refine:88s |
| `Refine.Untyped_R` | 2 | 366.9 | 249.5 | 117.5 | CBaseRefine:250s; Refine:118s |
| `Refine.Invariants_H` | 2 | 359.4 | 230.6 | 128.8 | CBaseRefine:231s; Refine:129s |
| `Refine.IpcCancel_R` | 2 | 348.0 | 272.5 | 75.5 | CBaseRefine:272s; Refine:76s |
| `CRefine.Ipc_C` | 2 | 332.3 | 184.0 | 148.3 | CRefine:184s; CRefineSyscall:148s |
| `CRefine.StateRelation_C` | 2 | 315.4 | 166.2 | 149.2 | CRefineSyscall:166s; CRefine:149s |
| `CRefine.Retype_C` | 2 | 297.3 | 158.3 | 139.1 | CRefine:158s; CRefineSyscall:139s |
| `Refine.CSpace_R` | 2 | 282.5 | 181.8 | 100.8 | CBaseRefine:182s; Refine:101s |
| `Refine.Ipc_R` | 2 | 265.5 | 193.7 | 71.7 | CBaseRefine:194s; Refine:72s |
| `Refine.Detype_R` | 2 | 262.8 | 170.0 | 92.8 | CBaseRefine:170s; Refine:93s |
| `Refine.CNodeInv_R` | 2 | 260.0 | 157.9 | 102.1 | CBaseRefine:158s; Refine:102s |
| `CRefine.Tcb_C` | 2 | 238.8 | 206.6 | 32.2 | CRefine:207s; CRefineSyscall:32s |
| `Refine.CSpace1_R` | 2 | 237.9 | 152.7 | 85.1 | CBaseRefine:153s; Refine:85s |
| `CRefine.VSpace_C` | 2 | 195.6 | 104.8 | 90.8 | CRefine:105s; CRefineSyscall:91s |
| `Refine.CSpace_I` | 2 | 174.0 | 132.2 | 41.8 | CBaseRefine:132s; Refine:42s |
| `CRefine.SyscallArgs_C` | 2 | 170.5 | 93.5 | 76.9 | CRefine:94s; CRefineSyscall:77s |
| `CRefine.Finalise_C` | 2 | 166.1 | 84.4 | 81.7 | CRefine:84s; CRefineSyscall:82s |
| `AInvs.ArchRetype_AI` | 2 | 159.7 | 84.2 | 75.6 | CBaseRefine:84s; AInvs:76s |
| `Refine.ADT_H` | 2 | 158.2 | 102.5 | 55.8 | CBaseRefine:102s; Refine:56s |
| `Refine.Tcb_R` | 2 | 156.1 | 123.9 | 32.2 | CBaseRefine:124s; Refine:32s |
| `Refine.TcbAcc_R` | 2 | 150.1 | 101.5 | 48.6 | CBaseRefine:102s; Refine:49s |
| `CRefine.Invoke_C` | 2 | 130.7 | 81.8 | 48.9 | CRefine:82s; CRefineSyscall:49s |
| `Access.ArchRetype_AC` | 2 | 130.4 | 67.2 | 63.2 | Access:67s; InfoFlowCBase:63s |
| `CRefine.Wellformed_C` | 2 | 128.4 | 67.5 | 60.8 | CRefineSyscall:68s; CRefine:61s |
| `CRefine.Corres_C` | 2 | 120.8 | 66.0 | 54.8 | CRefine:66s; CRefineSyscall:55s |
| `CRefine.Arch_C` | 2 | 118.7 | 73.4 | 45.4 | CRefine:73s; CRefineSyscall:45s |
| `Refine.KHeap_R` | 2 | 115.3 | 68.1 | 47.1 | CBaseRefine:68s; Refine:47s |
| `AInvs.Invariants_AI` | 2 | 115.0 | 64.7 | 50.4 | CBaseRefine:65s; AInvs:50s |
| `Refine.VSpace_R` | 2 | 111.5 | 74.3 | 37.2 | CBaseRefine:74s; Refine:37s |

## How the cost model uses this

For the four critical-path reports (`reports/critical-path-{proof,spec,haskell,c}.md`), each theory's weight is its **total elapsed across all sessions where it appears**, not its single-session elapsed. This avoids underestimating the wall-time impact of modifying a theory that is reprocessed by multiple downstream sessions due to the session inheritance pattern documented in [proof/ROOT:106-117](../verification/l4v/proof/ROOT) (`session CBaseRefine = CSpec + sessions Refine ...`).
