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

where `<hoare-monotonicity>` is one of `hoare_pre`, `hoare_weaken_pre`,
`hoare_strengthen_post`, `hoare_strengthen_postE_R`, `hoare_post_imp`,
or `hoare_post_impE`. The witness stays in source as a permanent
soundness record — do not delete it later.

**Only purely additive companion lemmas skip the witness.** If the
patch replaces, weakens, or deletes an existing lemma, the change
needs an `_old` witness — without one it is **rejected**. The
witness is what distinguishes a sound strengthening from a silent
break, including the cleanup-style "delete the weak alias" move.

## Workflow

1. **Find a candidate.** Run the candidates tool against the target
   session and pick the top entry not already in past strengthen logs.
   For unfamiliar candidate shapes, consult
   [`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md).

2. **Write the patch.** Two shapes:
   - **Modify an existing lemma** — drop a premise, tighten an
     existing postcondition (e.g. `≤` → `=`), or strengthen a
     postcondition conjunct. In the same patch append the witness
     lemma `<name>_old` per the contract above.
   - **Add a new companion lemma** — purely additive: a new
     functional postcondition, a new frame lemma, or a missing
     compound `_invs`. No witness required.

   Place patches under `logs/spec-strengthen-<file>-<YYYYMMDD>.patch`.

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

6. **Record.** Create
   `reports/experiments/<NNNN>-<short-name>/` with `patch.diff` +
   `command.sh` + `measurement.json`. The witness lemma is part of
   `patch.diff` (Step 2) — no separate file. Open a PR from a
   `spec-strengthen` topic branch targeting `main`. For meta-PRs
   (skill/tools/infra), the simplified two-file variant in parent
   SKILL rule 5 applies.

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
| `tools/spec_strengthen/spec_candidates.py` | Step 1 entry. Flat ranked list of candidates with natural-language suggested moves. |
| `tools/spec_strengthen/spec_impact.py` | Step 4 entry. Verdict + wall gate + witness presence. With `--measurement-out FILE` emits a simplified JSON suitable for the rule-5 audit bundle. |

Tool semantics are described in their own `--help`. The skill does
not enumerate detector internals — those live in the playbook.

## References (read on demand)

| When | File |
|---|---|
| Candidate shapes, scanner reliability, ROI weighting, worked case studies (success + failure) | [`references/spec-strengthen-playbook.md`](references/spec-strengthen-playbook.md) |
| Which sessions rebuild for which change | [`references/spec-downstream-map.md`](references/spec-downstream-map.md) |
| Refinement-level strengthening (`corres` / `ccorres`) | [`references/refinement-proofs.md`](../isabelle_prover/references/refinement-proofs.md) |
| Past strengthening sessions (precedent + failure modes) | [`reports/spec-strengthen/AInvs-*.md`](../../../reports/spec-strengthen/) |
