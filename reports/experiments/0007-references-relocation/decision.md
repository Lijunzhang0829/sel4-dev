# spec-0007 — relocate spec-only references under sub-skill (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

User review 2026-06-02 noted that two reference files were pure
spec content but lived under the parent-skill `references/`
directory. With **only** the spec sub-skill referencing them, they
were misplaced; under `isabelle_prover_spec/references/` they live
next to the SKILL.md that owns them.

## What changed

### Renames (via `git mv`)

```
.claude/skills/isabelle_prover/references/spec-strengthen-playbook.md
  → .claude/skills/isabelle_prover_spec/references/spec-strengthen-playbook.md

.claude/skills/isabelle_prover/references/spec-downstream-map.md
  → .claude/skills/isabelle_prover_spec/references/spec-downstream-map.md
```

### SKILL.md path updates (3 places)

`isabelle_prover_spec/SKILL.md` cross-links shortened from
`../isabelle_prover/references/<file>.md` to `references/<file>.md`
(sibling dir under same skill).

| Line | Reference |
|---|---|
| 45 | spec-strengthen-playbook (workflow Step 1 hint) |
| 134 | spec-strengthen-playbook (References table) |
| 135 | spec-downstream-map (References table) |

`refinement-proofs.md` reference (line 136) stays at parent path —
it's a truly cross-type doc, used by spec/proof/haskell/c flows.

### Files NOT moved (stay shared at parent)

`strengthen-guide.md` — cross-type strengthen examples; referenced
by both `isabelle_prover_haskell` and `isabelle_prover_c` SKILLs in
addition to spec. Keeps its parent-skill home.

## Why meta-PR variant

- Only documentation reorganization.
- No `.thy` / `.hs` / `.c` / `.h` under `verification/l4v/` touched.
- No tools modified.
- No verdict to measure; smoke check is "spec_candidates.py still
  runs and SKILL.md links resolve".

## Smoke test (in lieu of measurement.json)

```
$ python3 tools/spec_strengthen/spec_candidates.py --target ainvs --limit 2
scanned 4740 Hoare-triple lemmas under verification/l4v/proof/invariant-abstract
# Spec strengthening — candidates (2026-06-02)
...
```

`../../../..` relative path inside `spec-strengthen-playbook.md`
(pointing at `reports/spec-strengthen/AInvs-*.md`) still resolves
correctly — both old and new locations are 4 dirs deep under the
repo root, so the path math is unchanged.

## Justification for keeping vs. removing

Per user instruction "检查文件内容如果无意义可以清除":

- `spec-strengthen-playbook.md` — 628 lines of curated case
  studies, Taxonomy summary, pattern catalog, scanner reliability,
  ROI rationale. **Load-bearing** for the spec workflow when the
  agent hits an unfamiliar shape. Keep + move.
- `spec-downstream-map.md` — 116 lines of session dependency tree
  + wall-time budgets per session. **Load-bearing** when planning
  multi-file strengthening + estimating downstream rebuild cost.
  Keep + move.

Neither file is dead weight — both moved into the spec sub-skill.
