# spec-0001 — experiments audit scaffold (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified record — no measurement, no derivability) |
| **Commit** | `60461f6` on `experiments-infra` |
| **Date** | 2026-05-30 05:47:17 -0400 |
| **Branch lineage** | `experiments-infra` (branched from `main@df95551`) |
| **Verdict** | applied |

## Scope

Bootstraps parent SKILL.md rule 5 by introducing:

- `reports/experiments/_template/` — 4-file skeleton (patch.diff,
  derivability.thy, command.sh, measurement.json) for **seL4-source**
  PRs.
- `reports/experiments/README.md` — directory contract, layout,
  lifecycle (open / merged / reverted).
- `.github/PULL_REQUEST_TEMPLATE/spec-strengthen.md` — PR template
  covering the spec sub-skill's acceptance gate checklist.
- `.gitignore` allowlist `!reports/experiments/**` (was previously
  globbed by the `reports/*` ignore rule).

## Why no `command.sh` / `measurement.json` here

This commit is the **bootstrap exception** to rule 5: it introduces
the very template the record format references. Trying to follow the
template here would be circular (the template documents this commit
documenting itself).

All subsequent commits on this branch — starting with `b010c93` (see
`spec-0002-tools-critical-path/`) — comply with the simplified
meta-PR format (patch.diff + decision.md).

## Smoke test

The scaffold itself is verified by being usable: the immediately
following commit `b010c93` referenced
`reports/experiments/_template/` and shipped its own
`spec-0002-tools-critical-path/` record using the simplified meta-PR
variant. If the scaffold had been broken (missing files, wrong paths,
bad gitignore allowlist), `b010c93`'s record could not have been
created.

## Related

- Parent SKILL rule 5 (Meta-PR variant clause) — added in the same
  audit pass that created this record. See parent SKILL.md
  `.claude/skills/isabelle_prover/SKILL.md`.
- This record was **backfilled retroactively** on 2026-05-31 after
  noting in conversation that the original commit violated rule 5.
  Backfilled records carry the same audit weight as records written
  at the time; the file dates reflect the audit pass, not the
  underlying commit.
