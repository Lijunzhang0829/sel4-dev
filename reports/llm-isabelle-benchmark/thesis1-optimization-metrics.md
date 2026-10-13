# Thesis-1 evaluation: how to verify the *effect* of an LLM optimization, per module

Branch: `bridge-consumer-trace`. Date: 2026-06-16.

Under Thesis 1 ("use LLMs to optimize seL4"), "does it work?" has **no single
metric** — it splits by module. Three cross-cutting laws, then per-module.

## Three cross-cutting laws

1. **Correctness gate is primary, binary, and scope-varying.** Nothing counts until
   the proof chain is green. The "test" here is full formal correctness
   preservation — itself the hard part. Gate scope differs per module.
2. **Improvement metric is module-specific, and only C has a true scalar.** C =
   runtime/size; Spec = logical strength (not a scalar); Proof = robustness (not
   wall); Haskell = no intrinsic metric.
3. **Net value = benefit − re-establishment cost**, under **noise discipline**
   (same-fingerprint A/B, median ≥3, report the noise floor). Do not report noise
   as improvement (cf. the ±0.7% wall trap).

## Per module

| Module | Optimization | Improvement metric | True scalar? | Correctness gate (scope) |
|---|---|---|---|---|
| **C** | perf / size | cycles, bytes (sel4bench-style) | **yes** | CRefine + **translation validation, at the *verified* -O level** |
| **Spec** | stronger guarantees | strictly-stronger (binary, checkable) + **downstream-consumption count** + gap-closure | no (logical strength) | AInvs + all consumers green |
| **Proof** | robustness / brevity | **perturbation-survival rate** + step count (wall measured but ≈0) | partial | same statement + session green |
| **Haskell** | algorithm / modeling | **none intrinsic** — measure downstream (C cycles if enabler) or gap-closure (binary) | no | **Refine + CRefine (double bridge)** |

### C — the only module with a real number
Measure cycles/bytes with microkernel-benchmark discipline (fixed workload, cache
controlled, median, **compiler config = the verified config**). Methodological
trap: a -O3 speedup is meaningless if the kernel is only verified/TV-passing at
-O1/-O2 — the perf number would be on an unverified binary.

### Spec — strength, not a scalar
1. strictly-stronger: `⊢(new⟹old)` ∧ `¬⊢(old⟹new)` (both kernel-checkable);
2. **consumption**: how many downstream obligations now rely on / are simplified by
   it — the *real* effect; an unconsumed strengthening is speculative-additive;
3. gap-closure: a previously assumed/axiomatized property now proved (binary).
Do not invent a fake "how much stronger" scalar — strength is a partial order.

### Proof — robustness, not wall
The meaningful effect is **perturbation-survival rate**: subject the proof to the
failure-taxonomy operators (rename / insert-case / swap-sub-op / alter-precond /
alter-relation) and measure the fraction the new proof absorbs vs the old. Build
wall is measured but expected ≈0 (established no-leverage). This metric doubles as
the Thesis-2 repair benchmark's instrument.

### Haskell — no standalone metric
Design spec is not deployed; its perf is meaningless. Effect is visible only
downstream (C runtime if it enables a C change) or as binary modeling-gap closure,
and must always be netted against the double-bridge (Refine+CRefine) re-establishment
cost.
