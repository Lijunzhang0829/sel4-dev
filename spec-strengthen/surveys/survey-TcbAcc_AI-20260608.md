# spec-strengthen survey — AInvs / TcbAcc_AI.thy — 2026-06-08

Source: `verification/l4v/proof/invariant-abstract/TcbAcc_AI.thy`

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
| `G:TcbAcc_AI:set_thread_state:machine_state` | `mechanical` | set_thread_state / machine_state | set_thread_state_valid_ioc (L1463) | discovered (this run) |
| `G:TcbAcc_AI:set_thread_state:domain_index` | `mechanical` | set_thread_state / domain_index | set_thread_state_valid_ioc (L1463) | discovered (this run) |
| `G:TcbAcc_AI:set_thread_state:domain_time` | `mechanical` | set_thread_state / domain_time | set_thread_state_valid_ioc (L1463) | discovered (this run) |
| `G:TcbAcc_AI:set_thread_state:arch_state` | `mechanical` | set_thread_state / arch_state | set_thread_state_valid_ioc (L1463) | discovered (this run) |
| `G:TcbAcc_AI:set_bound_notification:machine_state` | `mechanical` | set_bound_notification / machine_state | set_bound_notification_valid_ioc (L1476) | discovered (this run) |
| `G:TcbAcc_AI:set_bound_notification:domain_index` | `mechanical` | set_bound_notification / domain_index | set_bound_notification_valid_ioc (L1476) | discovered (this run) |
| `G:TcbAcc_AI:set_bound_notification:domain_time` | `mechanical` | set_bound_notification / domain_time | set_bound_notification_valid_ioc (L1476) | discovered (this run) |
| `G:TcbAcc_AI:set_bound_notification:arch_state` | `mechanical` | set_bound_notification / arch_state | set_bound_notification_valid_ioc (L1476) | discovered (this run) |
| `G:TcbAcc_AI:set_mrs:machine_state` | `mechanical` | set_mrs / machine_state | set_mrs_pred_tcb_at (L1805) | discovered (this run) |
| `G:TcbAcc_AI:set_mrs:domain_index` | `mechanical` | set_mrs / domain_index | set_mrs_pred_tcb_at (L1805) | discovered (this run) |
| `G:TcbAcc_AI:set_mrs:domain_time` | `mechanical` | set_mrs / domain_time | set_mrs_pred_tcb_at (L1805) | discovered (this run) |
| `G:TcbAcc_AI:set_mrs:arch_state` | `mechanical` | set_mrs / arch_state | set_mrs_pred_tcb_at (L1805) | discovered (this run) |

## Tier 2 — Probe-confirmable (execute upgrades heuristic to ground-truth)

Pattern C candidates. The scanner only supplies a suspicion signal; `execute --candidate <key>` must run the TRIAL-based premise probe before any patch generation.

`suspicion_score` is **not** a success ranking. It is a within-pattern impact score: higher means "bigger payoff if true", not "more likely to survive probe".

(no C candidates for this file in scanner output)

## Tier 3 — Manual review only

Pattern A candidates. The detector only identifies weak/strong pairs plus a redirect-shaped proof hint. Pre/post comparability and consumer safety are still manual judgments, so there is no auto-execute path.

| Key | evidence | weak lemma | suggested strong companion | redirect proof hint | suspicion_score | prior |
|---|---|---|---|---|---:|---|
| `A:TcbAcc_AI:thread_set_valid_objs_triv` | `heuristic+manual` | thread_set_valid_objs_triv | thread_set_tcb_fault_set_invs | manual review | 7 | discovered (this run) |

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
| `G:TcbAcc_AI:set_thread_state:cdt` | `mechanical` | set_thread_state / cdt | 1 crunch derivation(s) reach set_thread_state |
| `G:TcbAcc_AI:set_thread_state:cur_thread` | `mechanical` | set_thread_state / cur_thread | 2 crunch derivation(s) reach set_thread_state |
| `G:TcbAcc_AI:set_thread_state:idle_thread` | `mechanical` | set_thread_state / idle_thread | 3 crunch derivation(s) reach set_thread_state |
| `G:TcbAcc_AI:set_thread_state:scheduler_action` | `mechanical` | set_thread_state / scheduler_action | set_thread_state_scheduler_action already exists in 2 site(s) |
| `G:TcbAcc_AI:set_thread_state:ready_queues` | `mechanical` | set_thread_state / ready_queues | set_thread_state_ready_queues already exists in 2 site(s) |
| `G:TcbAcc_AI:set_thread_state:cur_domain` | `mechanical` | set_thread_state / cur_domain | 5 crunch derivation(s) reach set_thread_state |
| `G:TcbAcc_AI:set_thread_state:interrupt_irq_node` | `mechanical` | set_thread_state / interrupt_irq_node | 3 crunch derivation(s) reach set_thread_state |
| `G:TcbAcc_AI:set_thread_state:interrupt_states` | `mechanical` | set_thread_state / interrupt_states | set_thread_state_interrupt_states already exists in 1 site(s) |
| `G:TcbAcc_AI:set_thread_state:is_original_cap` | `mechanical` | set_thread_state / is_original_cap | 1 crunch derivation(s) reach set_thread_state |
| `G:TcbAcc_AI:set_bound_notification:cdt` | `mechanical` | set_bound_notification / cdt | 1 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:cur_thread` | `mechanical` | set_bound_notification / cur_thread | 2 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:idle_thread` | `mechanical` | set_bound_notification / idle_thread | 3 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:scheduler_action` | `mechanical` | set_bound_notification / scheduler_action | 1 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:ready_queues` | `mechanical` | set_bound_notification / ready_queues | 1 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:cur_domain` | `mechanical` | set_bound_notification / cur_domain | 5 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:interrupt_irq_node` | `mechanical` | set_bound_notification / interrupt_irq_node | 3 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_bound_notification:interrupt_states` | `mechanical` | set_bound_notification / interrupt_states | set_bound_notification_interrupt_states already exists in 1 site(s) |
| `G:TcbAcc_AI:set_bound_notification:is_original_cap` | `mechanical` | set_bound_notification / is_original_cap | 1 crunch derivation(s) reach set_bound_notification |
| `G:TcbAcc_AI:set_mrs:cdt` | `mechanical` | set_mrs / cdt | 1 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:cur_thread` | `mechanical` | set_mrs / cur_thread | 2 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:idle_thread` | `mechanical` | set_mrs / idle_thread | 3 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:scheduler_action` | `mechanical` | set_mrs / scheduler_action | 1 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:ready_queues` | `mechanical` | set_mrs / ready_queues | 1 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:cur_domain` | `mechanical` | set_mrs / cur_domain | 5 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:interrupt_irq_node` | `mechanical` | set_mrs / interrupt_irq_node | 3 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:interrupt_states` | `mechanical` | set_mrs / interrupt_states | 17 crunch derivation(s) reach set_mrs |
| `G:TcbAcc_AI:set_mrs:is_original_cap` | `mechanical` | set_mrs / is_original_cap | 1 crunch derivation(s) reach set_mrs |

