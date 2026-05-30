# `reports/experiments/` — per-PR audit bundles

Persistent in-repo audit trail for every accepted strengthening patch.
Required by parent SKILL rule 5
([`.claude/skills/isabelle_prover/SKILL.md`](../../.claude/skills/isabelle_prover/SKILL.md)).

This directory is the **durable evidence**. The PR description gives
the human-readable story; this directory gives the machine-replayable
proof. Both are required.

## Layout

```
reports/experiments/
├── README.md                            ← this file
├── _template/                           ← skeleton, COPY when starting a new experiment
│   ├── patch.diff
│   ├── derivability.thy
│   ├── command.sh
│   └── measurement.json
└── <NNNN>-<short-name>/                 ← one directory per accepted strengthening
    ├── patch.diff                       ← re-applyable source change
    ├── derivability.thy                 ← A'⟹A witness lemma (skipped only for Pattern B)
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

## How to create

```bash
# 1. Allocate the next id
NNNN=$(printf "%04d" $(( $(ls reports/experiments/ \
  | grep -E '^[0-9]+-' | cut -d- -f1 | sort -n | tail -1 | sed 's/^0*//' || echo 0) + 1 )))
SHORT=spec-${NNNN}-your-short-name
mkdir -p reports/experiments/$SHORT

# 2. Copy skeleton
cp reports/experiments/_template/* reports/experiments/$SHORT/

# 3. Fill in the 4 files. command.sh must be re-runnable on a clean checkout.

# 4. Commit on a topic branch. Open the PR using the appropriate template
#    at .github/PULL_REQUEST_TEMPLATE/<type>-strengthen.md.
```

## Why four files instead of one PR description

The four files and the PR description serve different roles:

| Role | Location | Lifetime |
|---|---|---|
| Re-runnable measurement | `command.sh` + `measurement.json` | Persistent in repo; CI / future audit can re-run |
| Re-applyable source change | `patch.diff` | Persistent in repo; can be replayed onto a fresh checkout |
| A'⟹A consumability proof | `derivability.thy` | Persistent in repo; the cryptographic-equivalent guarantee that nothing broke |
| Human-readable "why" | PR description | Lives in PR; GitHub UI keeps it after merge but not in working tree |

PR description is the discovery-time review aid; experiments dir is the
post-merge audit trail. We deliberately do NOT duplicate them. Verdict
and rationale live in PR description only.

## Re-runnable contract

`command.sh` is the canonical re-runner. CI (or any auditor) can run
`bash reports/experiments/<NNNN>-<name>/command.sh` and reproduce the
verification + measurement from scratch. The script must:

- Apply `patch.diff` to a clean checkout of `verification/l4v` at the
  ref recorded in `measurement.json#baseline_ref`.
- Run `check-theory.sh --patch <patch>` (Step 3 of the spec sub-skill).
- Run the derivability check (Step 4.5) — verify `derivability.thy`.
- Print baseline wall, trial wall, delta to stdout.
- Exit 0 iff all gates pass.

`measurement.json` schema (minimal — extend per type as needed):

```json
{
  "session": "AInvs",
  "pattern": "C",
  "baseline_wall_ms": 41055,
  "trial_wall_ms": 38245,
  "delta_pct": -6.8,
  "baseline_ref": "reports/golden-baseline/walls.json#AInvs",
  "session_rebuild_done": false,
  "consumers_lines": 21,
  "consumers_files": 6,
  "impact_verdict": "premise-weaken",
  "derivability_verdict": "ok"
}
```

## Lifecycle

- **Open** — experiment dir created, patch verified, PR in flight.
- **Merged** — PR landed on `main`. Dir is permanent record.
- **Reverted** — if a later finding reverts the strengthening, add
  `reverted.md` to the dir explaining why. Don't delete the dir;
  the audit trail must survive reverts.
