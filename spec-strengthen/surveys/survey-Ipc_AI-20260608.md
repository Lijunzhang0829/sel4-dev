# spec-strengthen survey — AInvs / Ipc_AI.thy — 2026-06-08

Source: `verification/l4v/proof/invariant-abstract/Ipc_AI.thy`

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
| `G:Ipc_AI:set_extra_badge:machine_state` | `mechanical` | set_extra_badge / machine_state | set_extra_badge_vms (L1125) | discovered (this run) |
| `G:Ipc_AI:set_extra_badge:domain_index` | `mechanical` | set_extra_badge / domain_index | set_extra_badge_vms (L1125) | discovered (this run) |
| `G:Ipc_AI:set_extra_badge:domain_time` | `mechanical` | set_extra_badge / domain_time | set_extra_badge_vms (L1125) | discovered (this run) |
| `G:Ipc_AI:set_extra_badge:arch_state` | `mechanical` | set_extra_badge / arch_state | set_extra_badge_vms (L1125) | discovered (this run) |
| `G:Ipc_AI:set_mrs:machine_state` | `mechanical` | set_mrs / machine_state | set_mrs_vms (L1873) | discovered (this run) |
| `G:Ipc_AI:set_mrs:domain_index` | `mechanical` | set_mrs / domain_index | set_mrs_vms (L1873) | discovered (this run) |
| `G:Ipc_AI:set_mrs:domain_time` | `mechanical` | set_mrs / domain_time | set_mrs_vms (L1873) | discovered (this run) |
| `G:Ipc_AI:set_mrs:arch_state` | `mechanical` | set_mrs / arch_state | set_mrs_vms (L1873) | discovered (this run) |
| `G:Ipc_AI:set_message_info:machine_state` | `mechanical` | set_message_info / machine_state | set_message_info_global_refs (L2792) | discovered (this run) |
| `G:Ipc_AI:set_message_info:domain_index` | `mechanical` | set_message_info / domain_index | set_message_info_global_refs (L2792) | discovered (this run) |
| `G:Ipc_AI:set_message_info:domain_time` | `mechanical` | set_message_info / domain_time | set_message_info_global_refs (L2792) | discovered (this run) |
| `G:Ipc_AI:set_message_info:arch_state` | `mechanical` | set_message_info / arch_state | set_message_info_global_refs (L2792) | discovered (this run) |

## Tier 2 — Probe-confirmable (execute upgrades heuristic to ground-truth)

Pattern C candidates. The scanner only supplies a suspicion signal; `execute --candidate <key>` must run the TRIAL-based premise probe before any patch generation.

`suspicion_score` is **not** a success ranking. It is a within-pattern impact score: higher means "bigger payoff if true", not "more likely to survive probe".

| Key | evidence | lemma / premise | Consumers | suspicion_score | prior |
|---|---|---|---:|---:|---|
| `C:Ipc_AI:lsfco_cte_at:valid_objs` | `heuristic` | lsfco_cte_at / valid_objs | 88 | 440 | discovered (this run) |
| `C:Ipc_AI:get_rs_real_cte_at:valid_objs` | `heuristic` | get_rs_real_cte_at / valid_objs | 20 | 100 | discovered (this run) |

## Tier 3 — Manual review only

Pattern A candidates. The detector only identifies weak/strong pairs plus a redirect-shaped proof hint. Pre/post comparability and consumer safety are still manual judgments, so there is no auto-execute path.

| Key | evidence | weak lemma | suggested strong companion | redirect proof hint | suspicion_score | prior |
|---|---|---|---|---|---:|---|
| `A:Ipc_AI:lsfco_cte_at` | `heuristic+manual` | lsfco_cte_at | lookup_cnode_slot_real_cte | likely redirect | 88 | discovered (this run) |

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
| `G:Ipc_AI:set_extra_badge:cdt` | `mechanical` | set_extra_badge / cdt | 1 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:cur_thread` | `mechanical` | set_extra_badge / cur_thread | 2 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:idle_thread` | `mechanical` | set_extra_badge / idle_thread | 3 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:scheduler_action` | `mechanical` | set_extra_badge / scheduler_action | 1 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:ready_queues` | `mechanical` | set_extra_badge / ready_queues | 1 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:cur_domain` | `mechanical` | set_extra_badge / cur_domain | 5 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:interrupt_irq_node` | `mechanical` | set_extra_badge / interrupt_irq_node | 3 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:interrupt_states` | `mechanical` | set_extra_badge / interrupt_states | 17 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_extra_badge:is_original_cap` | `mechanical` | set_extra_badge / is_original_cap | 1 crunch derivation(s) reach set_extra_badge |
| `G:Ipc_AI:set_mrs:cdt` | `mechanical` | set_mrs / cdt | 1 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:cur_thread` | `mechanical` | set_mrs / cur_thread | 2 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:idle_thread` | `mechanical` | set_mrs / idle_thread | 3 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:scheduler_action` | `mechanical` | set_mrs / scheduler_action | 1 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:ready_queues` | `mechanical` | set_mrs / ready_queues | 1 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:cur_domain` | `mechanical` | set_mrs / cur_domain | 5 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:interrupt_irq_node` | `mechanical` | set_mrs / interrupt_irq_node | 3 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:interrupt_states` | `mechanical` | set_mrs / interrupt_states | 17 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_mrs:is_original_cap` | `mechanical` | set_mrs / is_original_cap | 1 crunch derivation(s) reach set_mrs |
| `G:Ipc_AI:set_message_info:cdt` | `mechanical` | set_message_info / cdt | 1 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:cur_thread` | `mechanical` | set_message_info / cur_thread | 2 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:idle_thread` | `mechanical` | set_message_info / idle_thread | 3 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:scheduler_action` | `mechanical` | set_message_info / scheduler_action | 1 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:ready_queues` | `mechanical` | set_message_info / ready_queues | 1 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:cur_domain` | `mechanical` | set_message_info / cur_domain | 5 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:interrupt_irq_node` | `mechanical` | set_message_info / interrupt_irq_node | 3 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:interrupt_states` | `mechanical` | set_message_info / interrupt_states | 17 crunch derivation(s) reach set_message_info |
| `G:Ipc_AI:set_message_info:is_original_cap` | `mechanical` | set_message_info / is_original_cap | 1 crunch derivation(s) reach set_message_info |

