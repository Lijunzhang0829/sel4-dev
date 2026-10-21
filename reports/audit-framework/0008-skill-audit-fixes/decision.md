# spec-0008 — slim-SKILL audit follow-ups (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

Post-merge audit of the slim SKILL ([[0006]] + [[0007]]) caught two
residual inconsistencies. This dir bundles the cleanup.

## Issues found

### 1. `hoare_post_imp_R` is not a real l4v rule

`isabelle_prover_spec/SKILL.md` line 30-31 listed
`hoare_post_imp_R` among the witness-tactic rules. Cross-check
against `verification/l4v/lib/Monads/`:

| Name | Status |
|---|---|
| `hoare_pre` | exists |
| `hoare_weaken_pre` | exists |
| `hoare_strengthen_post` | exists |
| `hoare_strengthen_postE_R` | exists |
| `hoare_strengthen_postE_E` | exists |
| `hoare_post_imp` | exists |
| `hoare_post_impE` | exists |
| **`hoare_post_imp_R`** | **does not exist** |

An agent following the SKILL would have written a witness using a
non-existent rule and got a typecheck error on the first
`check-theory.sh --patch` run.

### 2. `derivability.thy` was a stale parallel artefact

The slim SKILL ([[0006]]) folded the old standalone "Step 4.5 —
derivability check" into Step 2's witness lemma, which now lives
**inline in `patch.diff`** verified by the same `check-theory.sh`
pass as the strengthened lemma. But:

- `SKILL.md` Step 6 still said "and `derivability.thy` if the
  change isn't purely additive" — contradicts Step 2.
- `reports/experiments/_template/derivability.thy` survived as a
  vestigial template file.
- `reports/experiments/README.md` documented a 4-file layout +
  Step 4.5 contract that no longer exists.
- `reports/experiments/_template/measurement.json` had stale
  fields (`pattern`, `derivability_verdict`) that `spec_impact.py
  --measurement-out` does not emit.
- `tools/spec_strengthen/spec_impact.py` printed "Step 4.5
  incomplete" when the witness was absent — but Step 4.5 is gone.

The artefact wasn't load-bearing — but every reference to it was a
trap door back into the abandoned Tier-1/Pattern-A workflow.

## What changed

### `.claude/skills/isabelle_prover_spec/SKILL.md`

- Line 30-31 witness-rule list: `hoare_post_imp_R` →
  `hoare_post_impE` (the real validE form).
- Step 6: dropped the `derivability.thy` parenthetical; added the
  positive statement "The witness lemma is part of `patch.diff`
  (Step 2) — no separate file."

### `tools/spec_strengthen/spec_impact.py`

- `DERIVABILITY_RULES` tuple: dropped `hoare_post_imp_R`; added
  `hoare_strengthen_postE_E` and `hoare_post_impE` (the real
  validE-side rules that complete the witness-rule coverage).
- Witness-absent message: "Step 4.5 incomplete" → "witness lemma
  missing from patch" (Step 4.5 no longer exists in the slim SKILL).

### `reports/experiments/README.md`

- Layout block now 3 files (patch.diff, command.sh,
  measurement.json) instead of 4.
- "Why four files" header / table → "Why three files" with
  `_old`-witness-inline-in-patch note.
- Re-runnable contract: dropped "Run the derivability check (Step
  4.5)"; added that the single `check-theory.sh --patch` pass
  verifies both the strengthened lemma and the inline `_old`
  witness.
- `measurement.json` schema example: dropped stale `pattern` /
  `derivability_verdict` fields; added the live fields
  `wall_gate_pass`, `strength_score`, `witness_present`,
  `witness_advisory_pass`, `gate_pass`, `has_weakening`.

### `reports/experiments/_template/`

- `derivability.thy` — removed (vestigial; witness is inline in
  `patch.diff`).
- `measurement.json` — updated to match the schema
  `spec_impact.py --measurement-out` emits.
- `patch.diff` — header comment now says "the inline `<name>_old`
  witness lemma (spec sub-skill Step 2)" instead of "the _old aux
  lemma from Step 4.5".

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- Pure documentation + template + tool-string cleanup.
- Smoke tests below replace measurement.json.

## Smoke tests

All ran 2026-06-02 on a clean spec-strengthen working tree after
the edits above:

1. **Witness-rule existence cross-check** (the issue that
   triggered this audit):
   ```
   for n in hoare_pre hoare_weaken_pre hoare_strengthen_post \
            hoare_strengthen_postE_R hoare_strengthen_postE_E \
            hoare_post_imp hoare_post_impE; do
     grep -rln "\b${n}\b" verification/l4v/lib/Monads/ | head -1
   done
   ```
   All 7 names resolve to a definition file under
   `verification/l4v/lib/Monads/`. `hoare_post_imp_R` does not
   (negative-test confirms the bug was real). ✓

2. **`spec_impact.py` parses + `DERIVABILITY_RULES` correct**
   ```
   python3 -c "
   import importlib.util, sys
   spec = importlib.util.spec_from_file_location('m', 'tools/spec_strengthen/spec_impact.py')
   m = importlib.util.module_from_spec(spec); sys.modules['m']=m
   spec.loader.exec_module(m)
   assert 'hoare_post_imp_R' not in m.DERIVABILITY_RULES
   assert 'hoare_post_impE' in m.DERIVABILITY_RULES
   assert 'hoare_strengthen_postE_E' in m.DERIVABILITY_RULES
   "
   ```
   ✓ passes.

3. **`spec_candidates.py --target ainvs --limit 2`** —
   still scans 4740 Hoare-triple lemmas under `invariant-abstract`
   and emits the expected ranked table header. ✓

4. **No live file still references the removed artefacts**:
   ```
   grep -rn "derivability\.thy\|Step 4\.5\|hoare_post_imp_R" \
     .claude/skills/isabelle_prover_spec/ \
     tools/spec_strengthen/ \
     reports/experiments/README.md \
     reports/experiments/_template/
   ```
   ✓ empty result. Historical references inside
   `reports/experiments/0001/`/`0003/`/`0004/`/`0006/` patch.diff
   files are immutable PR records and intentionally left alone.

5. **`reports/experiments/_template/measurement.json`** parses as
   valid JSON with keys matching what `spec_impact.py
   --measurement-out` writes. ✓

## Notes / follow-ups

- The patch is intentionally additive on rule coverage:
  `hoare_strengthen_postE_E` was added even though it wasn't
  strictly required to fix `hoare_post_imp_R`, because a Hoare-rule
  list shouldn't silently omit the validE-E variant when the
  validE-R variant is listed. Symmetric coverage is the principled
  shape.
- Historical experiment dirs (`0001`/`0003`/`0004`/`0006`) keep
  their pre-slim-SKILL references to `derivability.thy` and
  "Step 4.5" — those are PR records of past state, not live
  contracts. Per `README.md` Lifecycle note, audit dirs are
  permanent records and survive even reverts.
