# strengthen summary — proof/invariant-abstract/ARM/ArchRetype_AI.thy (AInvs)

- baseline wall: **78972 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-store_pde_global_objs_no_global_refs_ | `store_pde_global_objs_no_global_refs` | P | named | TRIAL-FAILED |  |  |
| 01-P-store_pde_global_objs_no_arch_state_ | `store_pde_global_objs_no_arch_state` | P | named | TRIAL-FAILED |  |  |
| 02-P-store_pde_valid_kernel_mappings_map_global_no_arch_state_ | `store_pde_valid_kernel_mappings_map_global_no_arch_state` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
