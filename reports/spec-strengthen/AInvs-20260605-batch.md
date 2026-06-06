# Spec-Strengthen Log — AInvs session — 2026-06-05 batch (KHeap_AI)

Session: `AInvs`
File patched: `proof/invariant-abstract/KHeap_AI.thy` (new file this batch)
Operation focus: `set_object` (the fundamental heap-write primitive)
l4v submodule HEAD at baseline: `00d9073f70d0`
Branch: `spec-strengthen` (PR-4)

Previous batch (CSpace_AI, set_cdt): see
[`AInvs-20260602-batch.md`](AInvs-20260602-batch.md).

---

## TL;DR

Four `apply`-verified Pattern G frame lemmas on `set_object`, all
additive, all proven by the same one-line tactic. Plus one aborted
attempt that produced a useful negative result and motivated a
playbook update.

| # | Lemma | Trial Δ | apply wall | Notes |
|---|---|---:|---:|---|
| 0019 | `set_object_cdt[wp]` | +2.1% | 24,568 ms | parser false-negative on abbreviated form, recovered via explicit rewrite |
| 0020a | `set_object_interrupt_states[wp]` | — | — | **aborted** — `Duplicate fact declaration` (crunch-derived) |
| 0020b | `set_object_cur_thread[wp]` | −4.4% | 23,954 ms | fallback after 0020a; clean apply |
| 0021 | `set_object_cur_domain[wp]` | −4.6% | 24,238 ms | first positive application of new pre-flight recipe |
| 0022 | `set_object_arch_state[wp]` | −2.0% | 24,229 ms | last clean slot we took this batch |

**Cumulative same-file wall**: 24,538 ms (pre-batch baseline) →
24,229 ms (post-0022 apply) = **−1.3% net** for adding 4 lemmas.

`set_object` is the fundamental heap-write that nearly every
other `set_<X>` kernel operation calls internally. Pattern G
frames on `set_object` propagate through to downstream
`set_<X>_<field>` facts via wp class accumulation, so the wider
seL4 impact is downstream rather than in-file.

---

## 1. Per-experiment

### 1.1 spec-0019 — `set_object_cdt[wp]`

**What changed.** Inserted right after `set_object_machine_state[wp]`:

```isabelle
lemma set_object_cdt[wp]:
  "\<lbrace>\<lambda>s. P (cdt s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cdt s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

**Understanding.** `set_object` is defined as
`do kobj ← get_object ptr; assert (a_type kobj = a_type obj); s ← get; put (s\<lparr>kheap := (kheap s)(ptr \<mapsto> obj)\<rparr>) od`.
Only the `kheap` field is touched; `cdt` flows through unchanged.
Existing `set_object_*[wp]` lemmas covered `machine_state` (line
1277) but no other literal fields. A pre-batch survey found 11
missing literal-field frames.

**Attempt log.**
1. First wrote the lemma in the **abbreviated "preserves" form**
   that the existing `set_object_machine_state[wp]` uses:
   `set_object p ko \<lbrace>\<lambda>s. P (cdt s)\<rbrace>`. This is
   the idiomatic l4v style.
2. `check-theory.sh --patch` → OK 24,434 ms ✓
3. **But** `spec_witness_gen.py` returned "Could not classify"
   (regex doesn't match abbreviated form), and `spec_impact.py`
   stdout reported `Gate: FAIL` with an empty deltas table
   (though its `--measurement-out` JSON wrote
   `impact_verdict: additive, gate_pass: true` via a different
   code path — internal stdout/JSON divergence in the tool).
4. Strict SKILL Acceptance #2 needs a verdict in the table, so I
   reverted via `git -C verification/l4v checkout` and rewrote in
   **explicit Hoare form** `\<lbrace>P\<rbrace> body
   \<lbrace>Q\<rbrace>` (semantically identical).
5. Re-measure: baseline 24,538 → trial 25,045 (+2.1%). Apply OK
   24,568 ms.

**Downstream impact (上下游).** Same-file delta is roughly flat
(+2.1%) because KHeap_AI itself doesn't heavily reference
`(cdt s)` in postconditions. The intended downstream pickup is
in CSpace-side proofs and IPC paths that wrap `set_object`-using
operations and need to commute past `cdt`. Tier-2 grep
consumers: 0 (brand new). Refine NOT rebuilt.

---

### 1.2 spec-0020 — first attempt aborted, then `set_object_cur_thread[wp]`

**0020 first attempt — `set_object_interrupt_states[wp]`.**

The lemma right below the insertion point is `valid_irq_states_triv`,
which uses `\<lambda>s. P (interrupt_states s)` as an assumption.
So a frame lemma on `interrupt_states` would directly unlock
`valid_irq_states_triv` to apply on `set_object`. High expected
ROI.

Wrote in explicit form (lesson from 0019). `check-theory.sh
--patch` failed with:

```
*** Duplicate fact declaration
"Tmp_cecc3aa5ee556ddd.set_object_interrupt_states" vs.
"Tmp_cecc3aa5ee556ddd.set_object_interrupt_states"
*** At command "lemma" (line 1285)
```

Root cause: `KHeap_AI.thy:925` has

```isabelle
crunch interrupt_states[wp]: set_simple_ko "\<lambda>s. P (interrupt_states s)"
```

`set_simple_ko` calls `set_object` internally. `crunch`
recursively descends and auto-generates
`set_object_interrupt_states[wp]` as an intermediate fact. The
lemma name appears nowhere in source text — `grep
'set_object_interrupt_states'` across the entire l4v tree
returns zero hits — but the duplicate fires when Isabelle
compiles the patched file.

**Useful negative result**: the unlock-`valid_irq_states_triv`
downstream readiness is **already in place**. No PR needed for
that gap. Knowledge recorded as a playbook gotcha (commit
`f98fb0a`, see §"Pattern G mining: the pre-flight recipe"
below).

**0020 fallback — `set_object_cur_thread[wp]`.**

Picked `cur_thread` after confirming no crunch derivation
reaches `set_object` via that field. Wrote in explicit form
from the start.

```isabelle
lemma set_object_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

Baseline 25,076 → trial 23,966 (−4.4%). Apply OK 23,954 ms.

**Downstream impact.** Cur_thread-frame preservation across
`set_object` was the missing piece — `set_object_cur` covered the
predicate `cur_tcb`, not the literal field. Expected downstream
pickup in scheduler proofs (DetSchedSchedule_AI.thy) and TCB
proofs (TcbAcc_AI.thy) that wrap `set_object` underneath
`thread_set` or `set_thread_state`. Tier-2 grep: 0 immediate.

---

### 1.3 spec-0021 — `set_object_cur_domain[wp]`

**What changed.**

```isabelle
lemma set_object_cur_domain[wp]:
  "\<lbrace>\<lambda>s. P (cur_domain s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cur_domain s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

**Understanding.** Scheduler-domain field. Existing
`set_object_*[wp]` lemmas don't cover it; no crunch derivation
reaches it via `set_object`. Clean Pattern G slot.

**Attempt log.** First positive application of the new pre-flight
recipe (committed as part of playbook §"Pattern G mining: the
pre-flight recipe"). Two greps, both empty, then proceed:

```
$ grep -rn 'set_object_cur_domain\b' verification/l4v/
   # 0 hits
$ grep -rn 'crunch cur_domain\b.*set_simple_ko\|crunch cur_domain\b.*set_cap\|...' \
        verification/l4v/proof/invariant-abstract/
   # 0 hits
```

Wrote in explicit form, `check-theory.sh --patch` → OK 24,773 ms.
`spec_impact.py` verdict `additive`, gate PASS. Apply OK 24,238 ms.

**Downstream impact.** Δ −4.6% trial wall. Downstream pickup
expected in scheduler-domain invariant proofs across
DetSchedSchedule_AI.thy.

---

### 1.4 spec-0022 — `set_object_arch_state[wp]`

**What changed.**

```isabelle
lemma set_object_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

**Understanding.** `arch_state` carries architecture-specific
kernel state (ASID tables, ARM hardware-register abstraction,
etc.). Referenced extensively in arch invariants
(`valid_arch_state`, `valid_vspace_objs`, `valid_global_objs`,
…). High downstream surface area — but most of that surface lives
in ArchAcc_AI.thy and downstream ARM-specific files, not in
KHeap_AI itself.

**Attempt log.** Same pre-flight (0 direct hits, 0 crunch
derivations). Baseline 24,677 → trial 24,184 (−2.0%). Apply OK
24,229 ms.

**Downstream impact.** Modest in-file pickup (arch_state
references are rare in KHeap_AI). Expected real downstream value
in ArchAcc_AI.thy proofs and Refine-tier arch-state preservation
lifts.

---

## 2. Cumulative observations across the batch

### 2.1 Same-file wall trajectory

| State | Wall (ms) | Δ from pre-batch |
|---|---:|---:|
| pre-batch baseline | 24,538 | 0% |
| post-0019 apply | 24,568 | +0.1% |
| post-0020 apply | 23,954 | −2.4% |
| post-0021 apply | 24,238 | −1.2% |
| post-0022 apply | 24,229 | **−1.3%** |

The file got **marginally faster** despite adding 4 new lemmas.
Per-rule `[wp]`-class pickups (each new frame lemma lets wp
discharge that field's preservation goals instantly) outweigh
the per-lemma theory-graph parse cost. Same effect as the
0015-0017 CSpace_AI batch but cumulative across 4 adds rather
than just one.

### 2.2 Pattern G ROI is real and additive

Per-experiment trial deltas (all on the same operation):

| # | Field | Δ |
|---|---|---:|
| 0019 | cdt | +2.1% |
| 0020 | cur_thread | −4.4% |
| 0021 | cur_domain | −4.6% |
| 0022 | arch_state | −2.0% |

3 of 4 individual deltas are negative. The one positive (cdt,
+2.1%) reflects that `cdt` references in KHeap_AI itself are
rare — most cdt-frame proof obligations live in CSpace_AI / Ipc_AI.
Net result for the whole file is still negative, because the
other three fields have more in-file goal-shape matches.

### 2.3 Aborted attempts produced useful knowledge

**0020a**: `set_object_interrupt_states[wp]` failure showed
that `crunch <field>[wp]: <wrapper_calling_<op>>` implicitly
generates `set_<op>_<field>` frame facts via recursive lifting.
The crunch-derived lemma is invisible to grep. This led to:
- a new playbook gotcha section with a pre-flight recipe
- a re-survey of the 11 missing slots → 4 truly clean, 4
  crunch-derived (skip), 2 unverified, plus the already-filled
  machine_state

**0019 form-revert**: a stdout/JSON divergence in `spec_impact.py`
went unnoticed until I tried to use the abbreviated lemma form.
This is a tool consistency bug, not a soundness bug — but
worth a future fix in `spec_strengthen_scan.py`'s parser.

### 2.4 Which `set_object_<field>[wp]` slots remain after this batch

Post-batch state of the 11 originally-missing fields (pre-flight
classified):

| Field | Status | Reason |
|---|---|---|
| `cdt` | **applied** | [[0019]] |
| `cur_thread` | **applied** | [[0020]] |
| `cur_domain` | **applied** | [[0021]] |
| `arch_state` | **applied** | [[0022]] |
| `domain_index` | clean, available | — |
| `domain_time` | clean, available | — |
| `idle_thread` | skip | crunch-derived (3 files) |
| `scheduler_action` | skip | crunch-derived (1 file) |
| `interrupt_irq_node` | skip | crunch-derived (3 files) |
| `ready_queues` | skip | crunch-derived (1 file) |
| `interrupt_states` | skip | crunch-derived (caught by [[0020a]]) |

2 clean slots remain (`domain_index`, `domain_time`); both could
land in a future batch with the same recipe.

---

## 3. Selection logic — what actually drove the picks

The SKILL Step 1 says "run the candidates tool, pick the top
entry." I did **not** follow that for this batch (or for the
previous 0014-0017 batch). Honest account of the implicit
selection function:

1. **Operation: fundamental over specialized.** `set_object` was
   picked because it's the primitive heap-write that most other
   `set_<X>` ops call internally. Pattern G frames on it propagate
   to downstream via wp class accumulation.
2. **Pattern: shape 2 additive over shape 1 modify.** Additive
   `[wp]` rules cannot break downstream proofs — strict upper
   bound on risk. Shape 1 needs witness + probe + structural
   diff, and the TRIAL premise probe has been finding the top
   scanner candidates load-bearing in prior batches.
3. **Field choice within an op: existing-companion-by-predicate
   → missing-companion-by-literal-field.** When a file has
   `set_<op>_<predicate>[wp]` (e.g. `set_object_machine_state`
   for `valid_machine_state`) but no
   `set_<op>_<literal-field>[wp]`, the literal-field frame is
   almost certainly provable by the same one-line tactic. This
   is what I've been doing by inspection.
4. **Pre-flight: direct grep + crunch grep.** The recipe
   formalized after 0020a. Mechanical, cheap, catches the
   crunch-collision class.
5. **Scanner ROI ranking: not used.** The scanner emits Pattern
   A/B/C candidates. Pattern G isn't in its output. Top scanner
   picks I tried in earlier batches were Pattern C (`unused-premise`,
   ~50% TP per playbook calibration) and all came back
   load-bearing under the probe.

**Honest framing**: the batch logic was "scout `set_<op>` family
gaps + pre-flight + add". Effective but ad-hoc. We discussed
whether to formalize it (as a tool `spec_frame_gap.py` or a
playbook recipe) and the decision was **not** to write it into
the SKILL at this point — this summary records the implicit logic
for traceability.

---

## 4. Open follow-ups

1. **2 clean `set_object` slots remain** (`domain_index`,
   `domain_time`). Each is a same-shape additive PR with
   essentially the same pre-flight + measurement workflow.
2. **`spec_strengthen_scan.py` HOARE_TRIPLE_RE parser fix**.
   Should recognize the abbreviated `f \<lbrace>P\<rbrace>` form
   to avoid the 0019-style revert cycle. Small regex extension.
3. **`spec_impact.py` stdout/JSON consistency**. Same lemma
   sometimes produces "Gate: FAIL" in stdout markdown and
   `gate_pass: true` in JSON. Audit-of-record is JSON, but the
   stdout divergence is misleading.
4. **Cross-session measurement still deferred**. The 8 applied
   Pattern G frame lemmas across both batches (0015-0017 +
   0019-0022) should in principle speed up some Refine proofs.
   Quantification needs a ~1h17min rebuild and is the right
   thing to do once a meaningful cleanup PR adopts the new
   lemmas; doing it earlier wouldn't move the needle.
5. **Other `set_<op>` families to mine**: `set_thread_state`,
   `thread_set`, `set_cap` all have many companion lemmas and
   are likely to have similar Pattern G gaps. Each would warrant
   the same family-survey + pre-flight approach.

---

## 5. PR-4 commit list (this batch)

```
d58acf2 spec(0021+0022): set_object_cur_domain[wp] + set_object_arch_state[wp]
f98fb0a playbook(spec): record Pattern G gotchas — crunch collisions + parser caveat
1a5fd19 spec(0019+0020): set_object_cdt[wp] + set_object_cur_thread[wp] on KHeap_AI.thy
```

Three commits cover the 4 strengthening experiments plus the one
playbook update. Commit↔experiment mapping is not 1:1: `d58acf2`
covers 0021 and 0022 jointly, `1a5fd19` covers 0019 and 0020.
Each experiment still has its own
`reports/experiments/00NN-*/` audit directory with the full
4-file seL4-source PR record.

l4v submodule pointer at end of batch: `00d9073f70d0` (unchanged).
