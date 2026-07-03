# strengthen summary — proof/invariant-abstract/ARM/ArchUntyped_AI.thy (AInvs)

- baseline wall: **44813 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-cnode_cap_ex_cte_no_valid_objs_ | `cnode_cap_ex_cte_no_valid_objs` | P | named | TRIAL-FAILED |  |  |
| 01-P-cnode_cap_ex_cte_no_pspace_aligned_ | `cnode_cap_ex_cte_no_pspace_aligned` | P | named | TRIAL-FAILED |  |  |
| 02-P-valid_arch_state_global_pd__ | `valid_arch_state_global_pd'` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
