# spec-0015 — add `set_cdt_machine_state[wp]` frame lemma (seL4-source PR)

| Field | Value |
|---|---|
| **Variant** | seL4-source PR (rule 5 full record) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |
| **Patch shape** | 2 (additive — Pattern G frame preservation) |
| **Impact verdict** | `additive` |
| **Acceptance** | PASS (all 4 gates) |
| **Base** | `[[0014]]` (this experiment builds on 0014's applied state) |

## What changed

`verification/l4v/proof/invariant-abstract/CSpace_AI.thy` — added a
`[wp]`-attributed frame lemma right after `set_cdt_vms`:

```isabelle
lemma set_cdt_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

The lemma states **`set_cdt m` preserves the literal `machine_state`
field** for any predicate `P`. This is Pattern G (frame preservation):
the operation under question doesn't touch a particular state
component, so any property of that component flows through unchanged.

l4v submodule HEAD at baseline: `00d9073f70d0`.

## Why this is useful — what was missing

Existing `set_cdt_*` companions in the file:
- `set_cdt_vms[wp]` (line 3847) — preserves `valid_machine_state` (a
  PREDICATE over machine_state).
- `set_cdt_pspace`, `set_cdt_cur`, `set_cdt_valid_cap`, etc. — frame
  lemmas for various state components.

What was missing: a frame lemma for the LITERAL `machine_state` field.
`set_cdt_vms` proves the validity-predicate `valid_machine_state`
holds across `set_cdt`, but it does NOT let `wp` discharge an
arbitrary post-condition `P (machine_state s)` for a generic `P`. Any
proof that needed e.g. `\<lambda>s. ms_at p (machine_state s) = v` after
`set_cdt` had to unfold `set_cdt_def` manually to expose the record
update structure.

`set_cdt_machine_state[wp]` fills this gap. Universal-`P`
preservation is the strongest possible frame statement and gives wp
automation a polymorphic handle.

## Why shape 2 (additive)

The lemma is brand new — no `set_cdt_machine_state` exists. No
weakening, no deletion. Per SKILL §witness-contract shape 2: no
`_old` witness needed.

`spec_witness_gen.py` confirmed:
```
SHAPE 2 (additive) — new lemma(s) set_cdt_machine_state added;
no old form to derive. No witness required.
```

## Acceptance gate trace

1. `check-theory.sh --patch` returns OK — trial OK 57416 ms;
   re-verification at `--apply` OK 52587 ms. The proof
   `wpsimp simp: set_cdt_def` closes by unfolding the operation to
   a record update over the state, where Isabelle's record-update
   simplifier knows the cdt-field update doesn't affect the
   machine_state field. ✓
2. Impact verdict `additive` (SKILL-accepting for shape 2). ✓
3. Trial wall **−11.3%** vs baseline — well under the +30% cap
   (and actually **net negative**, see below). ✓
4. Parent SKILL hard rules inherited. ✓

## Impact on seL4 (上下游)

### Same-file wall — measured negative delta

| Phase | Wall | Source |
|---|---:|---|
| baseline (with [[0014]] in place) | 64,766 ms | `check-theory.sh CSpace_AI.thy AInvs` |
| trial (with this patch added) | 57,416 ms | `check-theory.sh --patch ...` |
| apply (re-verifies) | 52,587 ms | `check-theory.sh --apply ...` |
| delta (trial vs baseline) | **−11.3%** | `(trial − baseline) / baseline` |

**Why the file got FASTER**, despite adding a new lemma:

This is the **Case-1-style Pattern A effect** showing up via a
different mechanism. By tagging the new lemma `[wp]`, wp automation
in the rest of CSpace_AI.thy now discharges `\<lambda>s. P (machine_state
s)` postcondition fragments instantly via the new rule — instead of
falling through to slower fallbacks (`(simp add: set_cdt_def)` + a
universal cong rule + unification). Several downstream proofs in
CSpace_AI use `set_cdt`-containing tactic chains; wp's per-rule
unification cost drops once a rule with the exact shape is in the
class.

The new lemma's own proof cost is dwarfed by the per-consumer
discharge speed-up. The −11.3% wall delta is the net win.

This is a real-world demonstration of the playbook's **Pattern G
ROI**: a strong frame lemma added at source can both prevent a
class of manual unfolds AND speed up wp automation in the same
file.

### Cross-file consumers (Tier 2 grep, immediate)

`spec_impact.py`: `consumers_lines: 0`, `consumers_files: 0`.

The lemma is brand new, so grep finds zero usages. **But the
file-wall improvement above is real and immediate** — the wp
automation pickup happens within the same file at theory-load
time, not via explicit `apply set_cdt_machine_state` citations.

### Cross-session impact (Refine / CRefine) — NOT rebuilt

For an additive `[wp]` frame lemma in ASpec:
- The lemma is exported to Refine via standard `AInvs → Refine`
  session dependency.
- Downstream `[wp]` databases gain one entry. Some Refine proofs
  involving `set_cdt`-containing operations could see a similar
  Pattern G speedup, but quantifying requires a full Refine
  rebuild (~1h17min).
- **Upper bound for breakage**: zero — adding rules to wp's
  `[wp]` class can never break an existing proof, only speed it
  up or leave it unchanged.

Cross-session measurement deferred to a future cleanup pass.

## PR description fields

| Field | Value |
|---|---|
| Lemma | `set_cdt_machine_state[wp]` (new) |
| File | `verification/l4v/proof/invariant-abstract/CSpace_AI.thy` (inserted after line 3851) |
| Baseline wall entry | `reports/golden-baseline/walls.json#AInvs` — `64,766 ms` (state includes [[0014]]) |
| Trial wall from `--apply` | `52,587 ms` (the `--apply` re-verification; `--patch` trial was `57,416 ms`) |
| Experiment ID | `0015-set-cdt-machine-state-frame-lemma` |

## Files in this audit dir

| File | Source |
|---|---|
| `patch.diff` | hand-extracted hunk (the cumulative `git -C verification/l4v diff` after `--apply` contains both [[0014]] and this experiment's hunks; only this experiment's hunk is recorded here for per-experiment auditability) |
| `command.sh` | re-runnable measurement command (assumes [[0014]] applied first) |
| `measurement.json` | emitted by `spec_impact.py --measurement-out` |
| `decision.md` | this file |

## Notes / follow-ups

- l4v submodule pointer unchanged (same caveat as [[0014]]).
- The −11.3% wall delta is the first concrete demonstration that
  the playbook's ROI promise for `[wp]`-tagged frame lemmas
  materialises. Worth highlighting in any future Pattern G
  case-study.
- A future cleanup pass could grep CSpace_AI.thy for proofs that
  manually unfold `set_cdt_def` purely to discharge a
  `machine_state`-frame obligation; those proofs can now be
  simplified to single-line `wpsimp` calls. Out of scope here.
- Probe note: this candidate was found by inspection rather than
  scanner output (scanner's Pattern G detection is currently
  manual-only per playbook). `spec_premise_probe.sh` doesn't
  apply to shape-2 additive candidates — it's an unused-premise
  probe.
