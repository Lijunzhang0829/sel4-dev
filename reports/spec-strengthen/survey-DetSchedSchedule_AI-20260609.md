# spec-strengthen survey — AInvs / DetSchedSchedule_AI.thy — 2026-06-09

Source: `verification/l4v/proof/invariant-abstract/DetSchedSchedule_AI.thy`

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
| `G:DetSchedSchedule_AI:set_scheduler_action:machine_state` | `mechanical` | set_scheduler_action / machine_state | set_scheduler_action_switch_not_cur_thread (L3428) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_scheduler_action:domain_index` | `mechanical` | set_scheduler_action / domain_index | set_scheduler_action_switch_not_cur_thread (L3428) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_scheduler_action:domain_time` | `mechanical` | set_scheduler_action / domain_time | set_scheduler_action_switch_not_cur_thread (L3428) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_scheduler_action:arch_state` | `mechanical` | set_scheduler_action / arch_state | set_scheduler_action_switch_not_cur_thread (L3428) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_simple_ko:machine_state` | `mechanical` | set_simple_ko / machine_state | set_simple_ko_valid_sched_action (L2152) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_simple_ko:domain_index` | `mechanical` | set_simple_ko / domain_index | set_simple_ko_valid_sched_action (L2152) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_simple_ko:domain_time` | `mechanical` | set_simple_ko / domain_time | set_simple_ko_valid_sched_action (L2152) | discovered (this run) |
| `G:DetSchedSchedule_AI:set_simple_ko:arch_state` | `mechanical` | set_simple_ko / arch_state | set_simple_ko_valid_sched_action (L2152) | discovered (this run) |

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
| `G:DetSchedSchedule_AI:set_scheduler_action:cdt` | `mechanical` | set_scheduler_action / cdt | 1 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:cur_thread` | `mechanical` | set_scheduler_action / cur_thread | 2 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:idle_thread` | `mechanical` | set_scheduler_action / idle_thread | 3 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:scheduler_action` | `mechanical` | set_scheduler_action / scheduler_action | 1 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:ready_queues` | `mechanical` | set_scheduler_action / ready_queues | 1 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:cur_domain` | `mechanical` | set_scheduler_action / cur_domain | 5 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:interrupt_irq_node` | `mechanical` | set_scheduler_action / interrupt_irq_node | 3 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:interrupt_states` | `mechanical` | set_scheduler_action / interrupt_states | 17 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_scheduler_action:is_original_cap` | `mechanical` | set_scheduler_action / is_original_cap | 1 crunch derivation(s) reach set_scheduler_action |
| `G:DetSchedSchedule_AI:set_thread_state:machine_state` | `mechanical` | set_thread_state / machine_state | set_thread_state_machine_state already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_thread_state:cdt` | `mechanical` | set_thread_state / cdt | 1 crunch derivation(s) reach set_thread_state |
| `G:DetSchedSchedule_AI:set_thread_state:cur_thread` | `mechanical` | set_thread_state / cur_thread | 2 crunch derivation(s) reach set_thread_state |
| `G:DetSchedSchedule_AI:set_thread_state:idle_thread` | `mechanical` | set_thread_state / idle_thread | 3 crunch derivation(s) reach set_thread_state |
| `G:DetSchedSchedule_AI:set_thread_state:scheduler_action` | `mechanical` | set_thread_state / scheduler_action | set_thread_state_scheduler_action already exists in 2 site(s) |
| `G:DetSchedSchedule_AI:set_thread_state:cur_domain` | `mechanical` | set_thread_state / cur_domain | 5 crunch derivation(s) reach set_thread_state |
| `G:DetSchedSchedule_AI:set_thread_state:domain_index` | `mechanical` | set_thread_state / domain_index | do_extended_op reachable via set_thread_state — exst replacement, no stock lift for domain_index |
| `G:DetSchedSchedule_AI:set_thread_state:domain_time` | `mechanical` | set_thread_state / domain_time | do_extended_op reachable via set_thread_state — exst replacement, no stock lift for domain_time |
| `G:DetSchedSchedule_AI:set_thread_state:arch_state` | `mechanical` | set_thread_state / arch_state | set_thread_state_arch_state already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_thread_state:interrupt_irq_node` | `mechanical` | set_thread_state / interrupt_irq_node | 3 crunch derivation(s) reach set_thread_state |
| `G:DetSchedSchedule_AI:set_thread_state:interrupt_states` | `mechanical` | set_thread_state / interrupt_states | set_thread_state_interrupt_states already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_thread_state:is_original_cap` | `mechanical` | set_thread_state / is_original_cap | 1 crunch derivation(s) reach set_thread_state |
| `G:DetSchedSchedule_AI:set_bound_notification:machine_state` | `mechanical` | set_bound_notification / machine_state | set_bound_notification_machine_state already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_bound_notification:cdt` | `mechanical` | set_bound_notification / cdt | 1 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:cur_thread` | `mechanical` | set_bound_notification / cur_thread | 2 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:idle_thread` | `mechanical` | set_bound_notification / idle_thread | 3 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:scheduler_action` | `mechanical` | set_bound_notification / scheduler_action | 1 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:ready_queues` | `mechanical` | set_bound_notification / ready_queues | 1 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:cur_domain` | `mechanical` | set_bound_notification / cur_domain | 5 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:domain_index` | `mechanical` | set_bound_notification / domain_index | set_bound_notification_domain_index already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_bound_notification:domain_time` | `mechanical` | set_bound_notification / domain_time | set_bound_notification_domain_time already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_bound_notification:arch_state` | `mechanical` | set_bound_notification / arch_state | set_bound_notification_arch_state already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_bound_notification:interrupt_irq_node` | `mechanical` | set_bound_notification / interrupt_irq_node | 3 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_bound_notification:interrupt_states` | `mechanical` | set_bound_notification / interrupt_states | set_bound_notification_interrupt_states already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_bound_notification:is_original_cap` | `mechanical` | set_bound_notification / is_original_cap | 1 crunch derivation(s) reach set_bound_notification |
| `G:DetSchedSchedule_AI:set_simple_ko:cdt` | `mechanical` | set_simple_ko / cdt | 1 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:cur_thread` | `mechanical` | set_simple_ko / cur_thread | 2 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:idle_thread` | `mechanical` | set_simple_ko / idle_thread | 3 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:scheduler_action` | `mechanical` | set_simple_ko / scheduler_action | 1 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:ready_queues` | `mechanical` | set_simple_ko / ready_queues | 1 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:cur_domain` | `mechanical` | set_simple_ko / cur_domain | 5 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:interrupt_irq_node` | `mechanical` | set_simple_ko / interrupt_irq_node | 3 crunch derivation(s) reach set_simple_ko |
| `G:DetSchedSchedule_AI:set_simple_ko:interrupt_states` | `mechanical` | set_simple_ko / interrupt_states | set_simple_ko_interrupt_states already exists in 1 site(s) |
| `G:DetSchedSchedule_AI:set_simple_ko:is_original_cap` | `mechanical` | set_simple_ko / is_original_cap | 1 crunch derivation(s) reach set_simple_ko |

