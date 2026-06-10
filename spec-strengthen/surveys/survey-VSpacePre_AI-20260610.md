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

## Out of scope / manual only

**Pattern D** has no detector. It is an execute-only path with evidence tag `none`.
```
  spec_strengthen_run.sh execute --pattern D \
    --patch <patch> --theory <thy> --expid <expid> [--key <key>] [-y]
```

## Informational — Tier 1 candidates rejected by mechanical preflight

These remain visible for traceability, but they are not execute candidates.

| Key | evidence | (op, field) | reason |
|---|---|---|---|
| `G:VSpacePre_AI:set_mrs:machine_state` | `mechanical` | set_mrs / machine_state | do_machine_op reachable via set_mrs→store_word_offs — writes machine_state.memory, so frame is semantic FP |
| `G:VSpacePre_AI:set_mrs:cdt` | `mechanical` | set_mrs / cdt | 1 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:cur_thread` | `mechanical` | set_mrs / cur_thread | 2 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:idle_thread` | `mechanical` | set_mrs / idle_thread | 3 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:scheduler_action` | `mechanical` | set_mrs / scheduler_action | 1 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:ready_queues` | `mechanical` | set_mrs / ready_queues | 1 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:cur_domain` | `mechanical` | set_mrs / cur_domain | 5 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:domain_index` | `mechanical` | set_mrs / domain_index | set_mrs_domain_index already exists in 2 site(s) |
| `G:VSpacePre_AI:set_mrs:domain_time` | `mechanical` | set_mrs / domain_time | set_mrs_domain_time already exists in 2 site(s) |
| `G:VSpacePre_AI:set_mrs:arch_state` | `mechanical` | set_mrs / arch_state | set_mrs_arch_state already exists in 2 site(s) |
| `G:VSpacePre_AI:set_mrs:interrupt_irq_node` | `mechanical` | set_mrs / interrupt_irq_node | 3 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:interrupt_states` | `mechanical` | set_mrs / interrupt_states | 17 crunch derivation(s) reach set_mrs |
| `G:VSpacePre_AI:set_mrs:is_original_cap` | `mechanical` | set_mrs / is_original_cap | 1 crunch derivation(s) reach set_mrs |

