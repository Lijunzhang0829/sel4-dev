# Target (a) — KernelTypes Extraction Proposal

Break the spec↔haskell back-coupling identified in
[reports/theory-baseline-2026-05-14.md](../reports/theory-baseline-2026-05-14.md)
by extracting the 12 shared-type theories out of `ExecSpec` into a new
`KernelTypes` base session that both ASpec and ExecSpec inherit from.

---

## 1. The architectural finding (why this is meaningful)

`spec/design/skel/ARM/Arch_Structs_B.thy` carries the comment:

> `(* Architecture-specific data types shared by spec and abstract. *)`

**seL4 designers explicitly recognized these types are cross-pillar shared**,
but the physical layout puts them inside the haskell-derived `ExecSpec`
session. ASpec then back-imports via `sessions ExecSpec` in its ROOT.
The result: 49 ASpec/CSpec/DSpec theories transitively depend on Haskell
content, which means **any Haskell change cascades up into spec rebuilds**.

This is an architectural debt, not a design intent. Fix (a) reifies
the comment into a structural reality.

## 2. Concrete edges to remove

Per [reports/theory-baseline-2026-05-14.md](../reports/theory-baseline-2026-05-14.md),
the 9 spec→haskell direct edges via 5 haskell targets are:

| Haskell target | Spec importer | Direct cost |
|---|---|---:|
| `ExecSpec.MachineTypes` | `CKernel.Kernel_C` | (706s downstream impact) |
| `ExecSpec.MachineTypes` | `CSpec.Kernel_C` | — |
| `ExecSpec.MachineTypes` | `ExecSpec.MachineMonad` | (internal — same session) |
| `ExecSpec.InvocationLabels_H` | `ASpec.ArchDecode_A` | — |
| `ExecSpec.InvocationLabels_H` | `ASpec.Decode_A` | — |
| `ExecSpec.Event_H` | `ASpec.Syscall_A` | — |
| `ExecSpec.Event_H` | `DSpec.Syscall_D` | — |
| `ExecSpec.Arch_Structs_B` | `ASpec.Arch_Structs_A` | — |
| `ExecSpec.ArchLabelFuns_H` | `ASpec.InvocationLabels_A` | — |

## 3. The 12 theories to extract into `KernelTypes`

The 5 direct targets above, plus their transitive ExecSpec deps (so the
new session is a closed subset):

| Theory | Wall (s) | What it defines |
|---|---:|---|
| `ExecSpec.MachineTypes` | 9.3 | machine word, registers, arch fundamentals |
| `ExecSpec.MachineMonad` | 0.6 | machine monad abstractions |
| `ExecSpec.MachineOps` | 1.9 | machine operations |
| `ExecSpec.MachineExports` | 0.7 | re-exports for downstream |
| `ExecSpec.Platform` | 1.1 | platform constants |
| `ExecSpec.Kernel_Config` | 0.5 | build-time config |
| `ExecSpec.Setup_Locale` | 0.3 | Arch locale setup |
| `ExecSpec.Event_H` | 1.7 | `syscall` / `event` datatypes |
| `ExecSpec.ArchInvocationLabels_H` | 13.1 | arch invocation enum |
| `ExecSpec.InvocationLabels_H` | 6.1 | invocation label enum |
| `ExecSpec.ArchLabelFuns_H` | 0.2 | arch label functions |
| `ExecSpec.Arch_Structs_B` | 2.6 | arch-specific data types |
| **TOTAL** | **38.1** | |

ExecSpec has ~315s total own wall; we're extracting ~12% of it as the
shared base. Most of the extracted theories are small (datatypes,
configs, label enums).

## 4. Current ROOT structure

```
session ASpec in "abstract" = Word_Lib +
  sessions
    "HOL-Library"
    Lib
    ExecSpec          ← THE PROBLEMATIC EDGE (source-re-executes ExecSpec)
  directories "$L4V_ARCH"
  theories ...

session ExecSpec in "design" = Word_Lib +
  sessions
    Lib
    "HOL-Eisbach"
  directories "$L4V_ARCH" "../machine" "../machine/$L4V_ARCH"
  theories "API_H" "$L4V_ARCH/ArchIntermediate_H"
```

The `sessions ExecSpec` in ASpec causes the entire 315s of ExecSpec to
be **source-re-executed** in ASpec's ML state every time ASpec builds.
Even though only 38s of content is actually needed.

## 5. Proposed new ROOT structure

```
session KernelTypes in "design" = Word_Lib +
  options [document = false]
  sessions
    Lib
    "HOL-Eisbach"
  directories
    "$L4V_ARCH"
    "../machine"
    "../machine/$L4V_ARCH"
  theories
    "Setup_Locale"
    "Kernel_Config"
    "Platform"
    "$L4V_ARCH/MachineTypes"
    "MachineMonad"
    "MachineOps"
    "MachineExports"
    "Event_H"
    "$L4V_ARCH/ArchInvocationLabels_H"
    "InvocationLabels_H"
    "$L4V_ARCH/ArchLabelFuns_H"
    "$L4V_ARCH/Arch_Structs_B"

session ExecSpec in "design" = KernelTypes +
  options [document = false]
  theories "API_H" "$L4V_ARCH/ArchIntermediate_H"
  (* removed: sessions Lib HOL-Eisbach (inherited via KernelTypes) *)
  (* the 12 theories above no longer declared here *)

session ASpec in "abstract" = KernelTypes +     ← changed from Word_Lib
  options [document=pdf]
  sessions
    "HOL-Library"
    Lib
  (* removed: sessions ExecSpec — no longer needed *)
  directories "$L4V_ARCH"
  theories
    "Syscall_A"
    "Intro_Doc"
    "Glossary_Doc"
```

(DSpec gets a similar adjustment.)

## 6. Expected impact

### CSTR cost reduction

ASpec currently source-re-executes 315s of ExecSpec content for its
49 H-touching theories. After this refactor:
- ASpec inherits the 38s shared content via `+ KernelTypes` heap-merge (millisecond)
- The other ~277s of ExecSpec content (`API_H`, `CSpace_H`, etc.) is no
  longer pulled in (ASpec doesn't actually need it)
- **Estimated wall reduction in ASpec build: ~250s** (most of the
  current `sessions ExecSpec` cost vanishes)

Plus second-order: any session that builds ASpec with non-CBaseRefine
parent chain (AInvs, etc.) also stops paying the ExecSpec re-execution
amplification.

### 2D matrix shift expected

| Before | After |
|---|---|
| `spec × needs-H-NOT-C = 49 / 960s` | `spec × needs-H-NOT-C ≈ 5–10 / <100s` |
| `spec × needs-spec-only = 25 / 81s` | `spec × needs-spec-only ≈ 70 / ~900s` |

(Estimated; the residual `needs-H-NOT-C` are theories that legitimately
need haskell-side content beyond the shared types.)

### Change-locality matrix shift expected

| Pillar change | Before — sessions invalidated | After — sessions invalidated |
|---|---:|---:|
| `spec` | 34 (12045s) | 34 (12045s) — same; spec is foundational |
| `haskell` | 33 (12045s) | **~10 (~3000s) — only haskell-side sessions rebuild** |
| `c` | 10 (7085s) | 10 (7085s) |
| `proof` | 24 (9874s) | 24 (9874s) |

**The big win is the `haskell` row.** A Haskell change today invalidates
almost everything (because ASpec depends on ExecSpec). After (a), a
Haskell change only invalidates Haskell-side content (`Refine`, `Access`,
`InfoFlow`, the `proof × needs-H-NOT-C` theories) plus the cross-cut
sessions. **ASpec/AInvs no longer rebuild on Haskell change.**

This is the user-facing benefit: editing Haskell prototype becomes
much cheaper.

## 7. Implementation steps

1. **Add KernelTypes ROOT entry** (verification/l4v/spec/ROOT)
2. **Remove the 12 theory declarations from ExecSpec** (they'll be
   inherited via `+ KernelTypes`)
3. **Change ExecSpec parent** from `Word_Lib +` to `KernelTypes +`
4. **Change ASpec parent** from `Word_Lib + sessions ExecSpec` to
   `KernelTypes + sessions {HOL-Library, Lib}`
5. **Update DSpec** similarly (it has the `DSpec.Syscall_D` →
   `ExecSpec.Event_H` edge)
6. **Update import strings** across the repo:
   - `imports "ExecSpec.MachineTypes"` → `imports "KernelTypes.MachineTypes"`
   - Same for the other 4 direct targets
   - 9 affected import lines (per [theory-baseline-2026-05-14.md](../reports/theory-baseline-2026-05-14.md))
7. **Verify generation pipeline** (`make` in spec/design/) still works:
   - The 12 .thy files were auto-generated from haskell. Their output
     directory may need to change (or the namespace stays
     "ExecSpec.<file>" while we update consumers — needs investigation
     of `spec/design/skel/` Makefile rules).

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Generation pipeline still outputs files under `ExecSpec` namespace | High | Investigate first: can the skel/ pipeline produce a different namespace? If not, may need a thin re-export layer in KernelTypes wrapping ExecSpec-namespaced theories. |
| Hidden ML-level dependencies not in import graph | Medium | Validate by full canonical rebuild before+after; compare lemma_inventory diff = 0 |
| `arm_vspace_region_use` and other types currently in `ARM_H` global namespace; renaming may break downstream | Medium | Keep the `global_naming ARM_H` declaration intact in the moved file; only the session ownership changes |
| ExecSpec's other 80+ theories transitively need the 12 — they should still build fine via `+ KernelTypes`, but worth verifying | Low | The 12 are foundational and at the bottom of ExecSpec's internal DAG; moving them up doesn't change their position in the global DAG. |

## 9. Validation plan

For empirical confirmation (cheap to expensive):

1. **Parse check** (~seconds):
   `isabelle build -n -d . KernelTypes ExecSpec ASpec DSpec` to verify
   ROOT structure parses.

2. **Single-session build** (~10 min):
   `isabelle build -b KernelTypes` then `isabelle build -b ExecSpec`,
   confirms heap-merge chain works.

3. **ASpec + downstream sessions** (~30 min):
   `isabelle build -b ASpec AInvs Refine` to confirm spec content is
   unchanged.

4. **Lemma inventory check** (seconds):
   `python3 tools/lemma_inventory/diff.py` ensures no lemma added/removed.

5. **Canonical rebuild** (~5h):
   Full build, compare new `heaps/build_log.txt` against
   [heaps/build_log.txt](../heaps/build_log.txt).
   Target: total wall ≤ current 16,942s; ASpec build wall down.

6. **2D classification re-run** (seconds):
   `python3 tools/critical_path/theory_axis_2d.py` and diff against
   [reports/theory-baseline-2026-05-14.md](../reports/theory-baseline-2026-05-14.md).
   Expected: `spec × needs-H-NOT-C` drops from 49 → ~5–10.

## 10. What this does NOT solve

This is **target (a) only**. It does NOT address:
- The 45 cross-cut proof theories (needs-H-AND-C) — those still pay CSTR
  because they intrinsically need both sides
- The 9 misalignment candidates in CRefine/InfoFlowC
- The lemma-level optimization opportunities (Phase C)

But it's the cleanest single-axis architectural fix available, with
the largest change-locality dividend (haskell-pillar isolation).

## 11. Open questions for project owners

1. Is the `spec/design/skel/` generation pipeline tooling owned by the
   seL4 project or by Isabelle? Can the output namespace be changed?
2. Are there downstream tools (e.g., outside our verification tree) that
   reference `ExecSpec.MachineTypes` by exact name? Renaming would break them.
3. Is the `(* shared by spec and abstract *)` comment in
   `Arch_Structs_B.thy` a tracked design invariant, suggesting the
   shared-types refactor has been considered before? Check git log /
   l4v contributor docs for prior discussion.
