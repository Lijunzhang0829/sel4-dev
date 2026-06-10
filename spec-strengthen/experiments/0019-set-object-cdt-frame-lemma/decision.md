# spec-0019 — `set_object_cdt[wp]` Pattern G frame lemma (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-05 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive — Pattern G frame preservation) |
| **Impact verdict** | `additive` (measurement.json) |
| **Acceptance** | PASS (all 4 gates per measurement.json) |
| **File** | `proof/invariant-abstract/KHeap_AI.thy` (new file this batch) |
| **Base** | l4v submodule baseline `00d9073f70d0` (independent of 0014-0017 chain on CSpace_AI.thy) |

## What changed

Inserted right after `set_object_machine_state[wp]`:

```isabelle
lemma set_object_cdt[wp]:
  "\<lbrace>\<lambda>s. P (cdt s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cdt s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

Same Pattern G shape as 0015-0017, but on a much more fundamental
operation: `set_object` is the primitive heap-write that almost
every other `set_*` operation in the kernel spec calls internally
(set_cap, set_tcb, set_endpoint, set_notification, set_pt,
set_asid_pool, set_cdt all reduce to set_object at some level).

A frame lemma on set_object therefore has potentially much wider
downstream pickup than a frame lemma on any single set_<X>.

## Why the gap exists

`set_object` is defined as

```isabelle
set_object ptr obj ≡ do
  kobj ← get_object ptr;
  assert (a_type kobj = a_type obj);
  s ← get;
  put (s\<lparr>kheap := (kheap s)(ptr \<mapsto> obj)\<rparr>)
od
```

The only field touched is `kheap`. Every other state component
(`cdt`, `interrupt_states`, `cur_thread`, `scheduler_action`, ...)
flows through unchanged. Existing `set_object_*[wp]` lemmas cover
some of these — `set_object_machine_state[wp]` exists explicitly,
others are derived implicitly. A survey of `set_object_*` lemmas
in KHeap_AI.thy shows the following **literal-field frames
missing** at this point in the file:

| Field | Lemma name | Status pre-0019 |
|---|---|---|
| `machine_state` | `set_object_machine_state[wp]` | present (line 1277) |
| `cdt` | `set_object_cdt[wp]` | **missing → added by this PR** |
| `cur_thread` | `set_object_cur_thread[wp]` | missing (future PR candidate) |
| `idle_thread` | `set_object_idle_thread[wp]` | missing |
| `arch_state` | `set_object_arch_state[wp]` | missing |
| `interrupt_irq_node` | `set_object_interrupt_irq_node[wp]` | missing |
| `interrupt_states` | `set_object_interrupt_states[wp]` | **missing → added by [[0020]]** |
| `ready_queues` | `set_object_ready_queues[wp]` | missing |
| `scheduler_action` | `set_object_scheduler_action[wp]` | missing |
| `cur_domain` | `set_object_cur_domain[wp]` | missing |
| `domain_index` | `set_object_domain_index[wp]` | missing |
| `domain_time` | `set_object_domain_time[wp]` | missing |

This PR fills the `cdt` slot — chosen first because `cdt` is
heavily touched by CSpace-side proofs (cap_insert / cap_move
write to it), and many such proofs eventually call set_object
indirectly.

## Patch shape — additive, no witness

`spec_witness_gen.py` confirmed SHAPE 2 (additive) once the
lemma was written in explicit Hoare form `\<lbrace>P\<rbrace> ...
\<lbrace>Q\<rbrace>`. See "Tool false-negative" below for context.

## Acceptance gate trace

| Gate | Result | Source |
|---|---|---|
| 1. `check-theory.sh --patch` returns OK | ✓ OK 25,045 ms | trial run |
| 2. `spec_impact` emits non-weakening + accepting verdict | ✓ `additive` (per `measurement.json`) | `spec_impact.py --measurement-out` |
| 3. Trial wall ≤ baseline × 1.30 | ✓ +2.1% (24,538 → 25,045) | within +30% cap |
| 4. Parent SKILL hard rules inherited | ✓ | no `sorry`/`oops`; check-theory.sh is sole gate; PR-tracked |

## Tool false-negative recorded (workflow note)

The first attempt at this lemma used the **abbreviated
"preserves" form** that the existing `set_object_machine_state[wp]`
uses:

```isabelle
lemma set_object_cdt[wp]:
  "set_object p ko \<lbrace>\<lambda>s. P (cdt s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

This is the idiomatic l4v style for frame-preservation lemmas.
It is semantically identical to the explicit form (`f \<lbrace>P\<rbrace>`
desugars to `\<lbrace>P\<rbrace> f \<lbrace>\<lambda>_. P\<rbrace>`), and
`check-theory.sh --patch` accepted it (OK 24,434 ms).

**However**:
- `spec_witness_gen.py` returned "Could not classify patch (no
  shape detected)".
- `spec_impact.py` returned `Gate: FAIL` in stdout markdown (the
  Lemma deltas table was empty because the parser couldn't see
  the new lemma).
- `measurement.json` still got `impact_verdict: additive` /
  `gate_pass: true` via a fallback code path, creating an
  **internal inconsistency between stdout and JSON output**.

Root cause: `spec_strengthen_scan.py`'s `parse_thy_lemmas()` /
`HOARE_TRIPLE_RE` regex matches the explicit `\<lbrace>P\<rbrace>
body \<lbrace>Q\<rbrace>` form but not the abbreviated
`body \<lbrace>P\<rbrace>` form. This is exactly the regex-parser
fragility flagged in the toolchain critique earlier; it manifests
on real l4v idiom.

**Resolution for this PR**: I reverted the abbreviated-form apply
via `git -C verification/l4v checkout`, rewrote the patch in
explicit form, and re-measured + re-applied. The explicit form
has 0 functional difference but lets the tools see the lemma.

**Followup added to the cross-experiment summary**: the parser
needs to handle the abbreviated `f \<lbrace>...\<rbrace>` form
(both for `spec_strengthen_scan.py` library code and the stdout
markdown table in `spec_impact.py`, which has a divergence from
its own JSON output).

## Impact on seL4 (上下游)

### Same-file wall

| Phase | Wall | Notes |
|---|---:|---|
| baseline | 24,538 ms | KHeap_AI clean baseline (no prior strengthen) |
| trial (with patch) | 25,045 ms | +2.1% |
| apply (re-verifies) | 24,568 ms | within noise of baseline |

Roughly flat. KHeap_AI itself doesn't heavily reference `(cdt s)`
in postconditions, so the in-file wp-class pickup is small. The
big win (if any) is downstream.

### Cross-file consumers (Tier-2 grep)

`spec_impact.py`: `consumers_lines: 0`, `consumers_files: 0`. Brand
new lemma — no immediate textual citations. Expected pickup:
- CSpace-side proofs in CSpace_AI.thy that wrap `set_object`-using
  operations and need to commute past `cdt`.
- IPC proofs in Ipc_AI.thy that touch cap_insert / cap_move
  internally.
- Refine-tier counterparts (separate session, not built).

### Cross-session — NOT rebuilt

Adding a `[wp]` rule cannot break a downstream proof. Refine /
CRefine rebuild deferred (same reasoning as 0015-0017).

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_object_cdt[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/KHeap_AI.thy` (line 1281 post-apply) |
| Baseline wall | `24,538 ms` |
| Trial wall from `--apply` | `24,568 ms` (`--patch` trial: `25,045 ms`) |
| Experiment ID | `0019-set-object-cdt-frame-lemma` |

## Notes / follow-ups

- l4v submodule pointer unchanged.
- Parser fragility on abbreviated `f \<lbrace>P\<rbrace>` form is
  the most impactful tool issue this PR exposed. Worth fixing in
  `spec_strengthen_scan.py` HOARE_TRIPLE_RE before the next batch
  to avoid the explicit-vs-abbreviated wording churn. Adding a
  second branch to the regex (matching `body \<lbrace>P\<rbrace>$`
  after `lemma <name>[<attrs>]:` and treating it as
  `pre = post = P`) should suffice — small change, big workflow
  benefit.
- Of the 10 still-missing `set_object_<field>[wp]` lemmas, this
  batch fills `cdt` (0019) and `interrupt_states` (0020). The
  remaining 8 are natural candidates for a follow-up batch,
  particularly `cur_thread` and `scheduler_action` for
  scheduler-side proofs.
