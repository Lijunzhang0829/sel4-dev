# spec-0010 — five-point SKILL review fixes (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

User 2026-06-02 reviewed the slim SKILL and approved it as the
main version, with **5 specific revisions** required. Points 3
and 4 were verification asks — no SKILL change needed; the tool
CLIs already matched. Points 1, 2, and 5 required SKILL edits;
adopting point 1 also exposed a documentation gap in the
playbook that this PR fills.

## Five points — responses

### 1. Witness rule names — soften the hard list (FIXED)

**User's concern:** SKILL listed 6 Hoare monotonicity rules as
the witness tactic. A reader who picks by name-pattern alone can
easily mis-attribute a `validE_R`-shaped rule to a plain `valid`
triple (the 0008 audit found exactly this — the invented
`hoare_post_imp_R`). Hard list invites copy-paste.

**Fix:** SKILL now says

> `<hoare-monotonicity>` is **one of the applicable Hoare
> monotonicity rules** for the lemma's triple shape (`valid` /
> `validE` / `validE_R` / `validE_E`) — e.g. `hoare_pre`,
> `hoare_weaken_pre`, `hoare_strengthen_post`,
> `hoare_strengthen_postE_R`. Do not pick by name-matching alone;
> the full table of which rule applies to which triple shape (and
> the common `valid`-vs-`validE_R` confusion that produces
> invented names like `hoare_post_imp_R`) lives in
> `references/spec-strengthen-playbook.md`.

The full canonical table is now in the playbook (see point 1b).

### 1b. Playbook now carries the witness-rule-by-triple-shape table (NEW SECTION)

The SKILL hint above is only useful if the table actually exists.
Added a new section `## Witness rules by Hoare triple shape` to
`references/spec-strengthen-playbook.md`, placed between the
Taxonomy and Pattern catalog sections. It tabulates:

- 4 triple shapes (`valid`, `validE`, `validE_R`, `validE_E`)
- Strengthening direction (pre weakened vs post strengthened)
- The correct l4v rule name for that combination
- The expected `simp` discharge

Plus an explicit `Names that do NOT exist in l4v` list with the
canonical wrong picks (`hoare_post_imp_R`, `hoare_post_impR`,
`hoare_pre_R`) so future agents pattern-matching by suffix get
caught before `check-theory.sh` does.

Smoke-verified against `verification/l4v/lib/Monads/`:
- All 7 cited names: ✓ exist
- All 3 don't-invent names: ✓ confirmed absent

### 2. Modify-vs-delete were conflated (FIXED)

**User's concern:** SKILL said "if the patch replaces, weakens,
or deletes an existing lemma, the change needs an `_old`
witness." Adding `foo_weak_old` doesn't help consumers that
reference the deleted `foo_weak` by name — the soundness witness
proves the **statement** is still derivable; it doesn't redirect
**name resolution**. So "delete + `_old`" is not a sound shape.

**Fix:** witness contract section now distinguishes **three patch
shapes**, each with its own soundness obligation:

| Shape | Action | Soundness obligation |
|---|---|---|
| 1 | Modify an existing lemma (same name, stronger statement) | `<name>_old` witness in same patch |
| 2 | Add a new companion lemma (purely additive) | none |
| 3 | Delete or disable an existing lemma | EITHER `grep` proves no consumer OR a `lemmas <name> = ...` compatibility alias in the same patch — **NOT** an `_old` witness |

Step 2 of the Workflow now also enumerates these 3 shapes
explicitly. Shape 3 is called out as "a refactor, not a
strengthening", with a default recommendation "default to shape 1
(modify, keep witness) and follow up with a separate cleanup PR"
when in doubt.

### 3. `spec_impact.py --measurement-out` claimed by SKILL — verify (CONFIRMED)

**Verification command:**
```
$ python3 tools/spec_strengthen/spec_impact.py --help
usage: spec_impact.py [-h] [--baseline-wall BASELINE_WALL]
                      [--append-to APPEND_TO] [--json]
                      [--measurement-out MEASUREMENT_OUT]
                      ...
```

✓ `--measurement-out` exists. ✓ `--append-to` still exists for
the strengthen-log workflow. ✓ `--json` still exists for the
verbose report. No SKILL change required.

### 4. `spec_candidates.py` named correctly — verify (CONFIRMED)

```
$ ls tools/spec_strengthen/
spec_candidates.py       (Step 1 entry)
spec_impact.py           (Step 4 entry)
spec_strengthen_scan.py  (internal library — not user-facing)
__pycache__/
```

✓ `spec_candidates.py` is the merged successor to the old
`rank_candidates.py` + `spec_strengthen_scan.py` CLI surface, per
[[0006]] decision. ✓ `spec_strengthen_scan.py` lives on as
library code imported by both user-facing tools. No SKILL change
required.

### 5. Parent SKILL conformance — gap found and FIXED

**Parent SKILL rule 5** lists 4 required files for a seL4-source
PR record (`patch.diff` + `command.sh` + `measurement.json` +
`decision.md`) and mandates that the PR description **cite four
specific fields**: the lemma/file changed, the matching baseline
wall entry from `reports/golden-baseline/walls.json`, the trial
wall from `check-theory.sh --apply` output, and the experiment
ID.

**Pre-edit spec SKILL Step 6** listed only 3 files
(`patch.diff` + `command.sh` + `measurement.json` — missing
`decision.md`) and did not enforce the PR-description fields.
This is a real violation of parent rule 5 — a strict reading of
the sub-skill would let a non-compliant PR ship.

**Fix:** Step 6 now contains a full requirements table:

| File | Content |
|---|---|
| `patch.diff` | exact source change; witness inline per shape 1 / no witness for shape 2/3 |
| `command.sh` | re-runnable measurement command (template uses `spec_impact.py --measurement-out`) |
| `measurement.json` | baseline + trial wall + delta_pct + impact_verdict + witness_present |
| `decision.md` | summary + patch shape + verdict (`applied` / `rejected` / `inconclusive`) |

And explicit PR-description requirements: lemma name + file
touched, matching `reports/golden-baseline/walls.json` entry,
trial wall from `--apply`, experiment ID.

Meta-PR exception (no `verification/l4v/` change) is preserved:
2 files (`patch.diff` + `decision.md`), per parent rule 5
Meta-PR variant. This Meta-PR (0010) follows the exception.

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- Pure SKILL + playbook documentation.
- Smoke tests below replace measurement.json.

## Smoke tests

All ran 2026-06-02 on a clean spec-strengthen working tree:

1. **All 7 named witness rules exist in l4v**
   ```
   for n in hoare_pre hoare_weaken_pre hoare_strengthen_post \
            hoare_strengthen_postE_R hoare_strengthen_postE_E \
            hoare_post_imp hoare_post_impE; do
     grep -rq "\b${n}\b" verification/l4v/lib/Monads/ && echo ok
   done
   ```
   ✓ 7/7 ok.

2. **All 3 don't-invent names DO NOT exist** (`hoare_post_imp_R`,
   `hoare_post_impR`, `hoare_pre_R`) — confirmed absent ✓.

3. **SKILL link targets resolve.** Each `[...](relpath)` in
   SKILL.md tested via `[ -e "$relpath" ]`:
   - `references/spec-strengthen-playbook.md` ✓
   - `references/spec-downstream-map.md` ✓
   - `../isabelle_prover/references/refinement-proofs.md` ✓
   - `../../../reports/spec-strengthen/` ✓

4. **spec_candidates.py end-to-end smoke** still scans 4740
   Hoare-triple lemmas under `invariant-abstract` and emits the
   expected ranked table header. ✓

5. **SKILL.md size:** 138 → 184 lines (still well below the
   pre-slim 409). The +46 lines pay for the 3-shape patch contract
   (point 2) + the Step 6 requirements table (point 5).

## Notes / follow-ups

- The old SKILL acceptance line "**Hard stops**: any `weakening`
  verdict; trial wall > baseline × 1.30" is duplicated by
  Acceptance gate #2 + #3 — no action needed; the duplication is
  intentional (one is in-flow workflow guidance, the other is
  post-hoc audit gate).
- The playbook's "Taxonomy" section still describes the legacy
  "Packaging" pseudo-kind (delete weak alias) and says it needs a
  witness "treat as strengthening". This is consistent with shape
  1 of the new SKILL contract (the action is to MODIFY the
  surviving lemma), just phrased differently. Not changing —
  cross-reference works both ways.
