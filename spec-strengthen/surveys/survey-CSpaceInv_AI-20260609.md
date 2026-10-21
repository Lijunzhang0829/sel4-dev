# spec-strengthen survey — AInvs / CSpaceInv_AI.thy — 2026-06-09

Source: `verification/l4v/proof/invariant-abstract/CSpaceInv_AI.thy`

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
| `G:CSpaceInv_AI:set_cap:machine_state` | `mechanical` | set_cap / machine_state | set_cap_valid_ioc (L1582) | discovered (this run) |
| `G:CSpaceInv_AI:set_cap:domain_index` | `mechanical` | set_cap / domain_index | set_cap_valid_ioc (L1582) | discovered (this run) |
| `G:CSpaceInv_AI:set_cap:domain_time` | `mechanical` | set_cap / domain_time | set_cap_valid_ioc (L1582) | discovered (this run) |
| `G:CSpaceInv_AI:set_cap:arch_state` | `mechanical` | set_cap / arch_state | set_cap_valid_ioc (L1582) | discovered (this run) |

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
| `G:CSpaceInv_AI:set_cap:cdt` | `mechanical` | set_cap / cdt | 1 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:cur_thread` | `mechanical` | set_cap / cur_thread | 2 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:idle_thread` | `mechanical` | set_cap / idle_thread | 3 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:scheduler_action` | `mechanical` | set_cap / scheduler_action | 1 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:ready_queues` | `mechanical` | set_cap / ready_queues | 1 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:cur_domain` | `mechanical` | set_cap / cur_domain | 5 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:interrupt_irq_node` | `mechanical` | set_cap / interrupt_irq_node | 3 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:interrupt_states` | `mechanical` | set_cap / interrupt_states | 17 crunch derivation(s) reach set_cap |
| `G:CSpaceInv_AI:set_cap:is_original_cap` | `mechanical` | set_cap / is_original_cap | 1 crunch derivation(s) reach set_cap |

