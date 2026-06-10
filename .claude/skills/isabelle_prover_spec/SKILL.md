---
name: isabelle-prover-spec
description: "Strengthen seL4 Abstract Spec postconditions and invariant-abstract guarantees. Use when adding stronger statements to spec/abstract/ or proof/invariant-abstract/."
---

# Spec — strengthen the specification

A Hoare-triple lemma `⟨P⟩ f ⟨Q⟩` is **strengthened** if you replace it
with `⟨P'⟩ f ⟨Q'⟩` such that

```
P ⟹ P'    (every old precondition still satisfies the new)
Q' ⟹ Q    (the new postcondition still yields the old)
```

This is the only thing that distinguishes a real strengthening from
an arbitrary statement change. If you cannot mechanically prove the
**old form from the new form**, your change is **not a strengthening**;
it is an alternative statement and must be rejected.

The mechanical proof is a **witness lemma**: in the same patch as the
strengthened lemma, include

```isabelle
lemma <name>_old: "<verbatim old statement>"
  by (rule <hoare-monotonicity>[OF <name>]) <discharge>
```

where `<hoare-monotonicity>` is **one of the applicable Hoare
monotonicity rules** for the lemma's triple shape (`valid` /
`validE` / `validE_R` / `validE_E`) — e.g. `hoare_pre`,
`hoare_weaken_pre`, `hoare_strengthen_post`, `hoare_strengthen_postE_R`.
Do not pick by name-matching alone; the full table of which rule
applies to which triple shape (and the common
`valid`-vs-`validE_R` confusion that produces invented names like
`hoare_post_imp_R`) lives in
[`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md).
The witness stays in source as a permanent soundness record — do
not delete it later.

**Three patch shapes, three different soundness obligations** —
do not conflate them:

1. **Modify an existing lemma** (same name; statement gets
   stronger — drop a premise, tighten a postcondition, swap `≤` →
   `=`). Soundness is proved by the `<name>_old` witness lemma
   above. Without the witness the change is **rejected**.
2. **Add a new companion lemma** (purely additive — a new
   functional postcondition, a new frame lemma, a missing
   `_invs`). No old form to derive from; **no witness needed**.
3. **Delete or disable an existing lemma** (e.g. a weak alias
   that's no longer carrying its weight). This is **not a
   strengthening** — adding a renamed copy `<name>_old` does not
   redirect callers that still reference the old name. Either:
   (a) `grep -rn '\b<name>\b' verification/l4v` returns nothing
   (no consumers — safe to delete outright), or (b) keep a
   `lemmas <name> = <new-or-replacement>` compatibility alias
   under the old name so callers still resolve. The deletion is
   then a refactor, recorded with the smoke-test evidence above
   in the audit dir, but it carries no `_old` witness.

Picking the wrong shape is the most common silent break. If in
doubt, default to shape 1 (modify, keep witness) and follow up
with a separate cleanup PR.

## Workflow

1. **Find a candidate.** Run the candidates tool against the target
   session and pick the top entry not already in past strengthen logs.
   For unfamiliar candidate shapes, consult
   [`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md).

2. **Write the patch.** Choose the shape (see witness contract
   above for the soundness obligation each one carries):
   - **Modify an existing lemma** (shape 1) — drop a premise,
     tighten an existing postcondition (e.g. `≤` → `=`), or
     strengthen a postcondition conjunct. Append the
     `<name>_old` witness in the same patch.
   - **Add a new companion lemma** (shape 2) — purely additive:
     a new functional postcondition, a new frame lemma, or a
     missing compound `_invs`. No witness.
   - **Delete or disable an existing lemma** (shape 3) — only
     after a `grep` proves no consumer, OR after adding a
     `lemmas <name> = ...` compatibility alias in the same
     patch. No `_old` witness. **Shape 3 is a refactor, NOT a
     spec strengthening** — `spec_impact.py` will emit a
     `removal` verdict, which **fails this sub-skill's
     Acceptance gate #2** (a strengthening verdict is required).
     Routes out:
       (a) **Bundle with a shape-1 strengthening of the same
           lemma family** in one patch — the strengthening
           carries Acceptance; the cleanup rides along, recorded
           as a non-gating component in `decision.md`.
       (b) **Ship the deletion as a standalone refactor PR
           outside this sub-skill** — still a seL4-source PR
           per parent rule 5 (it touches `verification/l4v/`),
           but the acceptance criteria are the refactor's own
           (no behavioral change, no consumer broken); this
           sub-skill's strengthening gate does not apply.
     Do NOT submit a pure shape-3 patch through the
     spec-strengthening workflow expecting it to pass.

   Place patches under `logs/spec-strengthen-<file>-<YYYYMMDD>.patch`.

   For shape 1, run `python3 spec-strengthen/scripts/spec_witness_gen.py
   <patch> <theory.thy>` against the partial patch (modified
   lemma only) to emit the `<name>_old` witness. If the tool
   returns a clean witness (exit 0, no `sorry` TODO), paste it
   verbatim into the patch. If it falls back to a TODO skeleton
   (unrecognized triple shape, mixed direction, or parse error),
   consult [`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md)
   §"Witness rules by Hoare triple shape" and write the witness
   by hand. Hand-written witness is always allowed — the tool is
   a guard against picking the wrong rule by name-pattern, not a
   mandatory step.

   **The patch handed to `check-theory.sh --patch` in Step 3 MUST
   contain both the strengthened lemma AND its `<name>_old`
   witness** (shape 1). Do not verify the partial patch alone —
   a partial patch can pass Step 3 while leaving the soundness
   gate unenforced. The witness must sit immediately after the
   strengthened lemma in the patch hunk so Isabelle parses both
   in the same theory pass.

3. **Verify the patch.** `$ISA_SCRIPTS/check-theory.sh <file>
   <session> --patch <patch>` must reach `OK`. This single run
   verifies both the strengthened lemma and the witness — failure
   on either means the change isn't sound; abandon or rework.

4. **Measure impact.** Run the impact tool with `--baseline-wall` /
   `--trial-wall` from Step 3 and `--append-to` the dated
   strengthen-log. Inspect the verdict and gate result. **Hard
   stops**: any `weakening` verdict; trial wall > baseline × 1.30.

5. **Apply.** `$ISA_SCRIPTS/check-theory.sh <file> <session>
   --apply <patch>`. Source file now contains both the strengthened
   lemma and the witness.

6. **Record.** Create `reports/audit-framework/<NNNN>-<short-name>/`
   per parent SKILL rule 5. For a seL4-source PR (the normal
   case for spec strengthening) all **four** files are required:

   | File | Content |
   |---|---|
   | `patch.diff` | the exact source change, with the `<name>_old` witness inline (shape 1) or no witness (shape 2/3) |
   | `command.sh` | re-runnable measurement command (sub-skill template: `spec_impact.py --measurement-out`) |
   | `measurement.json` | baseline + trial wall + `delta_pct` + `impact_verdict` + `witness_present` (emitted by `spec_impact.py --measurement-out`) |
   | `decision.md` | human-readable summary, the patch shape (1/2/3), and a verdict (`applied` / `rejected` / `inconclusive`) |

   Open a PR from a `spec-strengthen` topic branch targeting
   `main`. The PR description must cite: the lemma name + file
   touched, the matching `reports/golden-baseline/walls.json`
   entry, the trial wall from `check-theory.sh --apply`, and the
   experiment ID (`<NNNN>-<short-name>`).

   For meta-PRs (skill/tools/infra — no `verification/l4v/`
   change), the simplified two-file variant in parent SKILL rule
   5 (`patch.diff` + `decision.md`) applies; `command.sh` and
   `measurement.json` are omitted.

## Acceptance

A patch is applied iff **all** the following hold:

1. `check-theory.sh --patch` returns `OK` on the changed file
   (verifies both the strengthened lemma and the witness in one
   pass — Steps 3 + soundness).
2. The impact tool emits no `weakening` verdict and at least one of
   `premise-weaken` / `monotone-strengthen` / `postcond-strengthen` /
   `additive`. (`rewrite` / `noop` / `removal` alone is insufficient.)
3. Trial wall ≤ baseline × 1.30 (regression cap).
4. The five parent-SKILL hard rules are inherited (no
   `sorry` / `oops` / `axiomatization`, `check-theory.sh` as the
   only verification gate, PR-tracked mainline, heap volatility
   care, two JSONL logs per run).

Spec strengthening trades build time for verification strength — a
slightly slower file with a stronger spec is the intended direction.
A measurable wall regression up to +30% is acceptable; beyond that,
re-examine the change.

## Targets

| Path | Session |
|---|---|
| `spec/abstract/**` | `ASpec` |
| `proof/invariant-abstract/**` | `AInvs` |
| `proof/refine/**` (`_R` lemmas; statement-not-tactic changes only) | `Refine` |

## Anti-pattern

Changing the **definition** of a kernel operation in
`spec/abstract/**` is not spec strengthening — it modifies the
kernel's observable behavior. That belongs in the
`isabelle_prover_haskell` or `isabelle_prover_c` flow. This sub-skill
only changes **statements about** existing definitions. The single
exception is introducing a *derived* `definition` that names an
expression already used unfolded throughout the file — a renaming,
not a behavior change.

## Tools (one-line each)

| Tool | Role |
|---|---|
| `$ISA_SCRIPTS/check-theory.sh` | Only verification gate. Runs the patched file through Isabelle; one pass verifies both the strengthened lemma and the witness. |
| `spec-strengthen/scripts/spec_candidates.py` | Step 1 entry. Flat ranked list of candidates with natural-language suggested moves. |
| `spec-strengthen/scripts/spec_witness_gen.py` | Step 2 helper — emits the `<name>_old` witness for shape-1 patches by looking up the correct Hoare monotonicity rule from triple shape + direction. Use it (see Step 2). |
| `spec-strengthen/scripts/spec_impact.py` | Step 4 entry. Verdict + wall gate + witness presence. With `--measurement-out FILE` emits a simplified JSON suitable for the rule-5 audit bundle. |

**Optional diagnostic** (not on the main path — use when bare
`check-theory.sh --patch` failure doesn't tell you which premise
broke the proof, OR for batch screening when daemon-mode is built):

| Tool | When to use |
|---|---|
| `spec-strengthen/scripts/spec_premise_probe.sh <thy-rel> <lemma> <premise>` | TRIAL-based probe for an `unused-premise` candidate — synthesizes a copy of the lemma with the premise dropped, runs the original proof in Isa-REPL. Ground-truth verdict `likely-unused` / `load-bearing`. Per-probe wall ≈ 60-200s in single-process mode (same as `check-theory.sh --patch`); the value-add is the **residual subgoal text** printed on `load-bearing`, which `check-theory.sh` failure doesn't expose. Future daemon mode (shared JVM across many probes) is where the real speedup lives. |

Tool semantics are described in their own `--help`. The skill does
not enumerate detector internals — those live in the playbook.

## References (read on demand)

| When | File |
|---|---|
| Candidate shapes, scanner reliability, ROI weighting, worked case studies (success + failure) | [`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md) |
| Which sessions rebuild for which change | [`references/spec-downstream-map.md`](references/spec-downstream-map.md) |
| Refinement-level strengthening (`corres` / `ccorres`) | [`references/refinement-proofs.md`](../isabelle_prover/references/refinement-proofs.md) |
| Past strengthening sessions (precedent + failure modes) | [`spec-strengthen/surveys/AInvs-*.md`](../../../spec-strengthen/surveys/) |
