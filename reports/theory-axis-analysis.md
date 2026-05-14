# Theory-Axis Analysis — Are Sessions Pillar-Alignable?

_For each PROOF-axis theory in the seL4 verification, compute its_
_transitive import closure and report which non-proof pillars it_
_touches (spec / haskell / c). The 4-pillar question per user_
_2026-05-14 reduces here to: how many proof theories are_
_single-axis (would survive in a pillar-aligned session) vs._
_cross-cutting (genuinely need both haskell and c heaps, so_
_must live in a CSTR-paying session no matter how we split)?_

Pillar source assignment from file path:
- `/spec/cspec/c/build/` → **c**
- `/spec/cspec/` → **c**
- `verification/seL4/src/` → **c**
- `/spec/design/` → **haskell**
- `/spec/haskell/` → **haskell**
- `/spec/abstract/` → **spec**
- `/proof/invariant-abstract/` → **spec**
- `/spec/` → **spec**
- `/proof/` → **proof**
- `/lib/` → **lib**
- `/tools/` → **lib**
- `verification/isabelle/` → **lib**

## Overall classification of proof-axis theories

| Class | Count | Total wall (s) | Description |
|---|---:|---:|---|
| cross-cut-HC | 51 | 4765 | needs BOTH haskell + c content (forced CSTR) |
| c-axis | 0 | 0 | needs c content only (could `+ CSpec` chain) |
| haskell-axis | 159 | 2928 | needs haskell content only (could `+ Refine` chain) |
| spec-only | 1 | 4 | needs spec content only (no haskell, no c) |
| minimal | 9 | 23 | self-contained / lib only |

### Examples of `haskell-axis`

- `Access.ADT_AC` (wall 0.3s)
- `Access.Access` (wall 6.8s)
- `Access.Access_AC` (wall 1.9s)
- `Access.ArchADT_AC` (wall 4.9s)
- `Access.ArchAccess` (wall 26.6s)

### Examples of `spec-only`

- `SepDSpec.AbstractSeparation_SD` (wall 3.8s)

### Examples of `cross-cut-HC`

- `AutoCorresCRefine.AutoCorresTest` (wall 0.0s)
- `CBaseRefine.Include_C` (wall 44.5s)
- `CRefine.ADT_C` (wall 207.6s)
- `CRefine.Arch_C` (wall 33.5s)
- `CRefine.AutoCorres_C` (wall 19.9s)

## Per-session distribution

For each major session, the axis-classification of its OWN
theories (those whose `.thy` files are in the session's nominal
directory). A session whose own theories all share ONE
classification is a strong candidate for pillar-alignment;
a session mixing classes is a candidate for SPLIT.

### `CBaseRefine`

_total: 2 proof theories, 45s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| cross-cut-HC | 1 | 44 | `Include_C` |
| minimal | 1 | 0 | `L4VerifiedLinks` |

### `CRefine`

_total: 46 proof theories, 1877s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 5 | 116 | `ArchMove_C`, `Fastpath_Defs`, `Fastpath_Equiv` |
| cross-cut-HC | 40 | 1756 | `ADT_C`, `Arch_C`, `AutoCorres_C` |
| minimal | 1 | 5 | `AutoCorresModifiesProofs` |

### `CRefineSyscall`

_total: 1 proof theories, 1s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| cross-cut-HC | 1 | 1 | `Intermediate_C` |

### `InfoFlowCBase`

_total: 1 proof theories, 6s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 1 | 6 | `Include_IF_C` |

### `InfoFlowC`

_total: 6 proof theories, 204s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 3 | 67 | `ADT_IF_Refine`, `ArchADT_IF_Refine`, `Example_Valid_StateH` |
| cross-cut-HC | 3 | 137 | `ADT_IF_Refine_C`, `ArchADT_IF_Refine_C`, `Noninterference_Refinement` |

### `InfoFlow`

_total: 44 proof theories, 380s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 41 | 365 | `ADT_IF`, `ArchADT_IF`, `ArchArch_IF` |
| minimal | 3 | 16 | `Noninterference_Base`, `Noninterference_Base_Alternatives`, `Noninterference_Base_Refinement` |

### `Refine`

_total: 42 proof theories, 1750s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 42 | 1750 | `ADT_H`, `ArchAcc_R`, `ArchMove_R` |

### `Access`

_total: 28 proof theories, 319s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 28 | 319 | `ADT_AC`, `Access`, `Access_AC` |

### `BaseRefine`

_total: 1 proof theories, 4s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 1 | 4 | `Include` |

### `DPolicy`

_total: 1 proof theories, 21s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 1 | 21 | `Dpolicy` |

### `SepDSpec`

_total: 8 proof theories, 14s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 5 | 10 | `Frame_SD`, `Helpers_SD`, `Lookups_D` |
| spec-only | 1 | 4 | `AbstractSeparation_SD` |
| minimal | 2 | 1 | `AbstractSeparationHelpers_SD`, `Sep_Tactic_Helper` |

### `DRefine`

_total: 18 proof theories, 171s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 16 | 170 | `Arch_DR`, `CNode_DR`, `Corres_D` |
| minimal | 2 | 1 | `MoreCorres`, `MoreHOL` |

### `DBaseRefine`

_total: 1 proof theories, 4s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 1 | 4 | `Include_D` |

### `Bisim`

_total: 2 proof theories, 22s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 2 | 22 | `Separation`, `Syscall_S` |

### `RefineOrphanage`

_total: 1 proof theories, 47s wall_

| Class | Count | Wall (s) | Sample theories |
|---|---:|---:|---|
| haskell-axis | 1 | 47 | `Orphanage` |

## Implications

- **23%** of proof theories are genuinely cross-cutting
  (need both haskell AND c content). These pay CSTR by their
  intrinsic nature — they assert relationships BETWEEN haskell
  and c representations. No structural change makes them mono-axis.
- **0** proof theories need c content only — these could
  in principle live in a C-axis-aligned session that `+`-merges
  CSpec without needing Refine.heap, avoiding CSTR.
- **159** proof theories need haskell content only — these
  could in principle live in a Haskell-axis session.

## Methodology caveats

- The theory DAG covers `verification/l4v/` thy files; some
  sessions (Simpl-VCG, HOL, Pure) live outside and have 0 DAG
  nodes. Theories in those sessions can't be classified here.
- 'Closure pillars' uses the DAG's `imports` edges. Indirect
  imports via Eisbach methods, `crunch`-generated facts, or ML
  attribute lookups are NOT in the DAG; some 'haskell-axis' or
  'c-axis' classifications may actually be cross-cutting at runtime.
- Theories without a wall measurement (not in their origin's BLOB)
  show 0s; they still count by theory-count but not by wall.
