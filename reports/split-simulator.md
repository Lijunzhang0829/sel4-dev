# Split Simulator — Per-Edge Decomposition Analysis

_For each Tier-3 (single-edge SWAP rejected) CSTR edge identified_
_by [move-simulator.md](move-simulator.md), this report runs an_
_import-closure analysis to determine whether SPLITTING the origin_
_session into a consumer-needed subset + an unused subset would_
_unlock a wall-positive intervention._

Method: for each (consumer, origin) edge, compute the transitive
closure of theories that consumer's own theories import from origin.
If this closure is a small fraction of origin's total content, SPLIT
becomes plausible — promote the closure as a new `+` parent.

## Summary by class

- **PROMISING-SPLIT**: 0 edges, total cost 0s
- **PARTIAL-SPLIT**: 0 edges, total cost 0s
- **MIXED**: 0 edges, total cost 0s
- **MONOLITHIC**: 6 edges, total cost 1660s
- **DAG-INCOMPLETE**: 1 edges, total cost 48s

## Per-edge analysis

| # | Consumer ← Origin | Cost (s) | #orig thys | #needed | Needed % | Wall(needed)@origin | Class |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `CBaseRefine` ← `CKernel` | 683 | 1 | 1 | 100% | 707 | MONOLITHIC |
| 2 | `InfoFlowCBase` ← `InfoFlow` | 365 | 44 | 40 | 100% | 361 | MONOLITHIC |
| 3 | `InfoFlowCBase` ← `Access` | 274 | 28 | 27 | 100% | 288 | MONOLITHIC |
| 4 | `CBaseRefine` ← `CSpec` | 187 | 7 | 6 | 96% | 145 | MONOLITHIC |
| 5 | `DBaseRefine` ← `DSpec` | 77 | 17 | 17 | 100% | 81 | MONOLITHIC |
| 6 | `CBaseRefine` ← `CParser` | 73 | 33 | 33 | 100% | 72 | MONOLITHIC |
| 7 | `CBaseRefine` ← `Simpl-VCG` | 48 | 14 | 0 | 0% | 0 | DAG-INCOMPLETE |

## Detailed recommendations

### MONOLITHIC

#### `CBaseRefine` ← `CKernel` (683s)

- consumer needs 100% of origin; SPLIT won't help — would still need to `+`-merge most of it.
- Origin breakdown: 1 theories total, 1 (100% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 707s, unneeded = 0s.
- Sample needed: CKernel.Kernel_C

#### `InfoFlowCBase` ← `InfoFlow` (365s)

- consumer needs 100% of origin; SPLIT won't help — would still need to `+`-merge most of it.
- Origin breakdown: 44 theories total, 40 (100% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 361s, unneeded = 20s.
- Sample needed: InfoFlow.ADT_IF, InfoFlow.ArchADT_IF, InfoFlow.ArchArch_IF
- Sample unneeded: InfoFlow.ExampleSystemPolicyFlows, InfoFlow.InfoFlow_Image_Toplevel, InfoFlow.PolicyExample

#### `InfoFlowCBase` ← `Access` (274s)

- consumer needs 100% of origin; SPLIT won't help — would still need to `+`-merge most of it.
- Origin breakdown: 28 theories total, 27 (100% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 288s, unneeded = 31s.
- Sample needed: Access.ADT_AC, Access.Access, Access.Access_AC
- Sample unneeded: Access.ExampleSystem

#### `CBaseRefine` ← `CSpec` (187s)

- consumer needs 96% of origin; SPLIT won't help — would still need to `+`-merge most of it.
- Origin breakdown: 7 theories total, 6 (96% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 145s, unneeded = 5s.
- Sample needed: CSpec.KernelInc_C, CSpec.Substitute, CSpec.shared_types_defs
- Sample unneeded: CSpec.KernelState_C

#### `DBaseRefine` ← `DSpec` (77s)

- consumer needs 100% of origin; SPLIT won't help — would still need to `+`-merge most of it.
- Origin breakdown: 17 theories total, 17 (100% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 81s, unneeded = 0s.
- Sample needed: DSpec.Asid_D, DSpec.CNode_D, DSpec.CSpace_D

#### `CBaseRefine` ← `CParser` (73s)

- consumer needs 100% of origin; SPLIT won't help — would still need to `+`-merge most of it.
- Origin breakdown: 33 theories total, 33 (100% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 72s, unneeded = 0s.
- Sample needed: CParser.Addr_Type, CParser.ArchArraysMemInstance, CParser.ArrayAssertion

### DAG-INCOMPLETE

#### `CBaseRefine` ← `Simpl-VCG` (48s)

- origin `Simpl-VCG` has 0 nodes in theory-DAG (likely lives outside verification/l4v/); cannot verify needed-subset. Treat as MONOLITHIC by default — splitting Isabelle-distribution sessions is out of scope.
- Origin breakdown: 14 theories total, 0 (0% of wall) needed by consumer.
- Wall in origin's own BLOB: needed = 0s, unneeded = 42s.
- Sample unneeded: Simpl-VCG.Generalise, Simpl-VCG.Hoare, Simpl-VCG.HoarePartial

## Methodology notes

- **Import closure** is approximate. Indirect imports via
  Eisbach methods, ML attribute lookups, or `crunch`-generated
  facts may not appear in the theory-DAG; the simulator might
  under-estimate the actually-needed subset.
- A **PROMISING-SPLIT** classification is necessary but not
  sufficient. Splitting a session means moving `.thy` files into
  a new directory, declaring a new `ROOT` session, and updating
  every downstream `imports "origin.X"` to point at the new
  session's namespace. Cost: invasive but mechanical.
- Estimates ignore amplification on the swapped-out current
  parent; if cur_parent is large its re-execution cost in the
  new heap context will exceed `cur_parent_size`. Treat the
  ratio `needed_wall/cur_parent_size` as a NECESSARY threshold,
  not a sufficient one. Validate any SPLIT empirically before
  committing.
