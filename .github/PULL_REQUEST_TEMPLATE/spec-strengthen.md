<!--
PR TEMPLATE — spec-strengthen
Use this template when the PR comes from the `spec-strengthen` branch
and contains one or more spec / invariant-abstract strengthenings.

For other types use the corresponding template:
  proof-strengthen.md, haskell-mega-merge.md, c-strengthen.md.

Required by parent SKILL rule 5 (.claude/skills/isabelle_prover/SKILL.md).
-->

## Summary

<!-- One sentence: what lemma(s) got strengthened and in what way. -->

## Experiments included in this PR

<!-- List every reports/audit-framework/<NNNN>-<name>/ dir touched.
     One PR may bundle multiple experiments if they're logically coherent
     (e.g. "all cte_at → real_cte_at ports in a single session"). -->

| Experiment | Lemma | Pattern | File:Line | Wall delta | Verdict |
|---|---|---|---|---:|---|
| spec-NNNN-... | `<lemma>` | C/A/B/D/E | `<file>:<line>` | -X.X% | premise-weaken |

## Per-experiment evidence

For each experiment listed above, the `reports/audit-framework/<NNNN>-<name>/`
directory contains:
- `patch.diff` — the source change (re-applyable)
- `derivability.thy` — A'⟹A witness lemma (proof that the OLD form is
  derivable from the NEW form; required for Patterns A/C/D/E)
- `command.sh` — re-runnable measurement command
- `measurement.json` — raw numbers tied to `reports/golden-baseline/walls.json`

CI re-runs `command.sh` for every experiment dir touched in this PR.

## Acceptance gates (all must be ✓)

- [ ] Step 3: `check-theory.sh --patch` returns `OK` on each
  strengthened file.
- [ ] Step 4: impact verdict is one of `premise-weaken`,
  `monotone-strengthen`, `postcond-strengthen`, `additive`. **No
  `weakening` verdict** anywhere.
- [ ] Step 4.5: derivability check passes (`<name>_old` aux lemma
  reaches `OK` in the same `--patch` pass).
- [ ] No new `sorry` / `oops` / `axiomatization` in the diff.
- [ ] Trial wall ≤ baseline × 1.30 (noise tolerance, ±5% normal).
- [ ] Each experiment directory contains the 4 required files
  (`patch.diff`, `derivability.thy`, `command.sh`, `measurement.json`).

## Downstream impact

<!-- Has a session-rebuild been done after `--apply` to measure the
     ACTUAL downstream wall effect on consumer files? Default is no
     (per spec sub-skill Step 5 — batch-rebuild cadence). If yes,
     attach the post-rebuild wall delta from golden-baseline diff. -->

- [ ] Session rebuild done after apply
- Consumer-surface count: <total lines × total files across proof tree>

## Reviewer checklist

- [ ] For Pattern A patches: confirmed both pre and post are comparable
  (not incomparable preconditions across different op shapes — see
  Case 4 in `references/spec-strengthen-patterns.md`).
- [ ] For Pattern C patches: read the proof body and confirmed no
  hidden use of the removed conjunct (no `valid_objsE`, no
  `sym_refs_*`-style consumers).
- [ ] For Pattern E patches: preflight checklist in spec SKILL.md was
  followed; per-component coverage is ≥95% in the spec_coverage_matrix.

## Notes / Lessons (optional)

<!-- If this strengthening teaches something not yet in the case-study
     catalog, propose an addition to
     .claude/skills/isabelle_prover/references/spec-strengthen-patterns.md
     in a follow-up commit. -->
