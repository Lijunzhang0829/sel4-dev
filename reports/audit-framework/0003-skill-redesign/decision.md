# spec-0003 — isabelle_prover_spec skill redesign (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — skill documents only, no source change) |
| **Commits** | `04bdc8e` + `d120ced` on `spec-strengthen` |
| **Dates** | 2026-05-30 (04bdc8e) → 2026-05-31 (d120ced) |
| **Verdict** | applied |

## Scope

Two commits, treated as one logical unit because `d120ced`
supersedes / completes `04bdc8e`. The patch.diff in this directory
is the **cumulative** diff `git diff main..d120ced` restricted to
the two skill-related files.

### `04bdc8e` — first pass

Added the A' ⟹ A derivability gate to the spec sub-skill and a
curated case-studies section to `references/spec-strengthen-patterns.md`.
This was the first attempt at the redesign.

### `d120ced` — final form (replaces 04bdc8e where they overlap)

Full Taxonomy-first redesign of `isabelle_prover_spec/SKILL.md` per
user critique (received 2026-05-31). Major structural change:

- **§1 — Definition** of stronger spec: `P_old ⟹ P_new ∧ Q_new ⟹ Q_old`.
  Notation unified to `_old / _new` (no primes) per user Problem 1.
- **§2 — Taxonomy** in 3 tiers, replacing the prior flat Pattern A–E
  list as first-class concept:
    - Tier 1 Logical Strengthening — derivability gate mandatory
    - Tier 2 Automation Strengthening — derivability skipped
      (verified by Exp 2: building-block lemmas are NOT derivable
      from compound `_invs`)
    - Tier 3 Packaging — Pattern A demoted here, "not a true
      strengthening" warning prominent
- **§3 — Pattern Index** with **NEW Pattern G (Frame Preservation)**
  added as first-class Tier 1 entry (verified by Exp 4a:
  `set_cdt_machine_state[wp]` is a genuine gap, verified OK in
  74,463 ms). Pattern F (composite state-delta) **rejected**
  empirically (Exp 3: l4v idiom always decomposes into B + G).
- **§4.5 — Derivability check** tactic table now distinguishes
  triple shapes: `hoare_weaken_pre` for plain valid, `hoare_pre`
  for validE (Exp 1 finding — previous draft conflated them).
- **§5 Step 2.5 — REPL Preflight** section: current grep heuristic;
  future Isa-REPL integration scoped (Exp 4b confirmed feasibility
  but `_compile(snippet)` errors on complex sessions, needs
  `_step()`-based integration).
- **§6 — Acceptance Gates** explicitly excludes Semantic Strength
  Score from gating, keeps it ranking-only per user Problem 3.
- **§9 — Experiment Calibration** table documents the 4 experiments
  that empirically validated each design decision.

### references/spec-strengthen-patterns.md additions

- Pattern G section with verified example `set_cdt_machine_state[wp]`
  in Untyped_AI.thy + tactic template + scanner-blind-spot note.

## Smoke test (in lieu of measurement.json)

The redesign itself is documentation. The smoke test that the
redesign-guided workflow is executable: the Pattern G example cited
in §9 — `set_cdt_machine_state[wp]` — was verified by
`check-theory.sh` returning `OK (74,463ms)` on `Untyped_AI.thy`
during Exp 4a (2026-05-31). That run is the existence proof that the
new Pattern G entry corresponds to a real, mechanizable opportunity
in the codebase.

## Why this is meta-PR variant

- Only `.md` files under `.claude/skills/` were modified.
- No `.thy` / `.hs` / `.c` / `.h` under `verification/l4v/` touched
  in these two commits.
- No Hoare-triple state to measure.
- The empirical numbers cited (74,463 ms; +9% derivability overhead)
  are documented in §9 as references back to the experiments that
  produced them; not measurements OF this PR.

## Related

- Parent SKILL rule 5 Meta-PR variant clause — landed on
  `experiments-infra` (commit `c562d06`); will appear on `main` once
  PR-1 merges.
- This record **backfilled retroactively** 2026-05-31 after audit
  noted rule 5 violation. The patch.diff is `git diff main..d120ced`
  scoped to the two changed skill files.
- A separate `.gitignore` change in the same audit commit adds
  `!reports/experiments/**` allowlist on `spec-strengthen` (the
  branch inherited the older `.gitignore` that lacks the rule).
