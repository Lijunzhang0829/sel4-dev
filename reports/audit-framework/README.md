# `reports/audit-framework/` — audit-dir format reference + historical scaffold

Two roles, both stable:

1. **Format reference** — `README.md` (this file) + `_template/` define
   the canonical layout of a per-experiment audit dir (`patch.diff`,
   `command.sh`, `measurement.json`, `decision.md`). Referenced by
   parent SKILL.md rule 5.

2. **Historical scaffold** — `0001-0010` are the early framework
   experiments that established the audit-dir convention itself
   (`experiments-scaffold`, `tools-critical-path`, `skill-redesign`,
   `skill-and-tools-slim-refactor`, `references-relocation`,
   `skill-audit-fixes`, `purge-stale-claude-duplicate`,
   `skill-review-5-points`). These are frozen historical records.

**Active experiments now live under topic-specific directories**, not here:

- spec-strengthening: `spec-strengthen/experiments/<NNNN>-*/`
- proof staticization: `lemma-staticize/runs/bench-<timestamp>/`

This directory is the **durable evidence FORMAT** — the PR description
gives the human-readable story; the audit dir under the appropriate
topic dir gives the machine-replayable proof. Both are required.

## Layout

```
reports/audit-framework/
├── README.md                            ← this file
├── _template/                           ← skeleton, COPY when starting a new experiment
│   ├── patch.diff
│   ├── command.sh
│   └── measurement.json
└── <NNNN>-<short-name>/                 ← one directory per accepted strengthening
    ├── patch.diff                       ← re-applyable source change (includes the `_old` witness lemma inline)
    ├── command.sh                       ← re-runnable measurement command
    └── measurement.json                 ← raw numbers tied to golden-baseline
```

`<NNNN>` is a zero-padded sequential id (`0001`, `0002`, …).
Short-name is lowercase-with-hyphens, ≤ 30 chars, describing the lemma
or batch. Example: `spec-0042-lookup-slot-real-cte-at`.

## When to create

**Mandatory** for every patch that reaches `check-theory.sh --apply` and
is intended to be merged via PR. See the per-type sub-skill (e.g.
[`isabelle_prover_spec`](../../.claude/skills/isabelle_prover_spec/SKILL.md))
for the exact construction step.

## Two variants — seL4-source PR vs Meta PR

Rule 5 (parent SKILL.md) distinguishes two kinds of PR. Pick the one
that matches your change:

### Variant A — seL4-source PR (full record)

Triggered when the PR modifies `.thy` / `.hs` / `.c` / `.h` under
`verification/l4v/`. Full 3-file record required:

```
<NNNN>-<short-name>/
├── patch.diff       ← includes the `_old` witness lemma (per sub-skill Step 2)
├── command.sh
└── measurement.json
```

### Variant B — Meta PR (simplified record)

Triggered when the PR only modifies skill documents, scaffolding,
PR templates, tooling under `tools/` or `.claude/skills/`. No
Hoare-triple to measure, so:

```
<NNNN>-<short-name>/
├── patch.diff       ← what changed (verbatim diff)
└── decision.md      ← rationale, scope, smoke-test description
```

A meta-PR's `decision.md` must cite an **explicit smoke test** that
proves the meta change doesn't break the existing workflow (e.g.
"check-theory.sh on Finalise_AI.thy baseline returns OK in 20s after
the bug fix").

The single commit that first introduces this very `_template/`
directory is the **only** bootstrap exception (it would be
documenting itself); see rule 5 in parent SKILL.md.

## How to create

```bash
# 1. Allocate the next id
NNNN=$(printf "%04d" $(( $(ls reports/audit-framework/ \
  | grep -E '^[0-9]+-' | cut -d- -f1 | sort -n | tail -1 | sed 's/^0*//' || echo 0) + 1 )))
SHORT=spec-${NNNN}-your-short-name
mkdir -p reports/audit-framework/$SHORT

# 2. Copy skeleton
cp reports/audit-framework/_template/* reports/audit-framework/$SHORT/

# 3. Fill in the 4 files. command.sh must be re-runnable on a clean checkout.

# 4. Commit on a topic branch. Open the PR using the appropriate template
#    at .github/PULL_REQUEST_TEMPLATE/<type>-strengthen.md.
```

## Why three files instead of one PR description

The three files and the PR description serve different roles:

| Role | Location | Lifetime |
|---|---|---|
| Re-runnable measurement | `command.sh` + `measurement.json` | Persistent in repo; CI / future audit can re-run |
| Re-applyable source change | `patch.diff` (includes `_old` witness inline) | Persistent in repo; replayable onto fresh checkout. Witness is mechanically checked by the same `check-theory.sh` pass that verifies the strengthened lemma |
| Human-readable "why" | PR description | Lives in PR; GitHub UI keeps it after merge but not in working tree |

PR description is the discovery-time review aid; experiments dir is the
post-merge audit trail. We deliberately do NOT duplicate them. Verdict
and rationale live in PR description only.

## Re-runnable contract

`command.sh` is the canonical re-runner. CI (or any auditor) can run
`bash reports/audit-framework/<NNNN>-<name>/command.sh` and reproduce the
verification + measurement from scratch. The script must:

- Apply `patch.diff` to a clean checkout of `verification/l4v` at the
  ref recorded in `measurement.json#baseline_ref`.
- Run `check-theory.sh --patch <patch>` (Step 3 of the spec sub-skill).
  This single pass verifies both the strengthened lemma and the inline
  `_old` witness — no separate derivability step.
- Print baseline wall, trial wall, delta to stdout.
- Exit 0 iff all gates pass.

`measurement.json` schema (produced by `spec_impact.py
--measurement-out`; extend per type as needed):

```json
{
  "session": "AInvs",
  "baseline_wall_ms": 41055,
  "trial_wall_ms": 38245,
  "delta_pct": -6.8,
  "wall_gate_pass": true,
  "baseline_ref": "reports/golden-baseline/walls.json#AInvs",
  "session_rebuild_done": false,
  "consumers_lines": 21,
  "consumers_files": 6,
  "impact_verdict": "premise-weaken",
  "strength_score": 2.0,
  "witness_present": true,
  "witness_advisory_pass": true,
  "gate_pass": true,
  "has_weakening": false
}
```

## Lifecycle

- **Open** — experiment dir created, patch verified, PR in flight.
- **Merged** — PR landed on `main`. Dir is permanent record.
- **Reverted** — if a later finding reverts the strengthening, add
  `reverted.md` to the dir explaining why. Don't delete the dir;
  the audit trail must survive reverts.
