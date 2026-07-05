# spec-strengthen survey — AInvs / Untyped_AI.thy — 2026-06-09

Source: `verification/l4v/proof/invariant-abstract/Untyped_AI.thy`

This survey follows the 4-layer model: detector outputs are collected per-pattern, then presented by **verification tier** rather than by a unified candidate ranking.

Evidence tags:
- `mechanical`: preflight already completed; candidate is high-confidence execute material
- `heuristic`: scanner hit only; execute must upgrade it with a probe
- `heuristic+manual`: scanner hit only; human review remains mandatory
- `none`: no detector; execute-only/manual path

## Tier 1 — Mechanically clean (high-confidence apply)

Pattern G candidates. These already passed direct-grep and crunch-derived preflight checks.

| Key | evidence | (op, field) | anchor | prior |
|---|---|---|---|---|
| `G:Untyped_AI:set_cdt:domain_index` | `mechanical` | set_cdt / domain_index | set_cdt_state_hyp_refs_of (L2871) | discovered (this run) |
| `G:Untyped_AI:set_cdt:domain_time` | `mechanical` | set_cdt / domain_time | set_cdt_state_hyp_refs_of (L2871) | discovered (this run) |
| `G:Untyped_AI:set_cdt:arch_state` | `mechanical` | set_cdt / arch_state | set_cdt_state_hyp_refs_of (L2871) | discovered (this run) |

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
| `G:Untyped_AI:set_cdt:machine_state` | `mechanical` | set_cdt / machine_state | set_cdt_machine_state already exists in 1 site(s) |
| `G:Untyped_AI:set_cdt:cdt` | `mechanical` | set_cdt / cdt | 1 crunch derivation(s) reach set_cdt |
| `G:Untyped_AI:set_cdt:cur_thread` | `mechanical` | set_cdt / cur_thread | set_cdt_cur_thread already exists in 1 site(s) |
| `G:Untyped_AI:set_cdt:idle_thread` | `mechanical` | set_cdt / idle_thread | set_cdt_idle_thread already exists in 1 site(s) |
| `G:Untyped_AI:set_cdt:scheduler_action` | `mechanical` | set_cdt / scheduler_action | 1 crunch derivation(s) reach set_cdt |
| `G:Untyped_AI:set_cdt:ready_queues` | `mechanical` | set_cdt / ready_queues | 1 crunch derivation(s) reach set_cdt |
| `G:Untyped_AI:set_cdt:cur_domain` | `mechanical` | set_cdt / cur_domain | 5 crunch derivation(s) reach set_cdt |
| `G:Untyped_AI:set_cdt:interrupt_irq_node` | `mechanical` | set_cdt / interrupt_irq_node | 3 crunch derivation(s) reach set_cdt |
| `G:Untyped_AI:set_cdt:interrupt_states` | `mechanical` | set_cdt / interrupt_states | 17 crunch derivation(s) reach set_cdt |
| `G:Untyped_AI:set_cdt:is_original_cap` | `mechanical` | set_cdt / is_original_cap | 1 crunch derivation(s) reach set_cdt |

