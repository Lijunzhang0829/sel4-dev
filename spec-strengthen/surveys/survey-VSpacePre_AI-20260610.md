# spec-strengthen survey — AInvs / VSpacePre_AI.thy — 2026-06-10

Source: `verification/l4v/proof/invariant-abstract/VSpacePre_AI.thy`

This survey follows the 4-layer model: detector outputs are collected per-pattern, then presented by **verification tier** rather than by a unified candidate ranking.

Evidence tags:
- `mechanical`: preflight already completed; candidate is high-confidence execute material
- `heuristic`: scanner hit only; execute must upgrade it with a probe
- `heuristic+manual`: scanner hit only; human review remains mandatory
- `none`: no detector; execute-only/manual path

## Tier 1 — Mechanically clean (high-confidence apply)

Pattern G candidates. These already passed direct-grep and crunch-derived preflight checks.

(none in this file)

## Tier 2 — Probe-confirmable (execute upgrades heuristic to ground-truth)

Pattern C candidates. The scanner only supplies a suspicion signal; `execute --candidate <key>` must run the TRIAL-based premise probe before any patch generation.

`suspicion_score` is **not** a success ranking. It is a within-pattern impact score: higher means "bigger payoff if true", not "more likely to survive probe".

(no C candidates for this file in scanner output)

## Tier 3 — Manual review only

Pattern A candidates. The detector only identifies weak/strong pairs plus a redirect-shaped proof hint. Pre/post comparability and consumer safety are still manual judgments, so there is no auto-execute path.

(no A candidates for this file in scanner output)

## Out of scope / manual only

**Pattern D** has no detector. It is an execute-only path with evidence tag `none`.
```
  spec_strengthen_run.sh execute --pattern D \
    --patch <patch> --theory <thy> --expid <expid> [--key <key>] [-y]
```

