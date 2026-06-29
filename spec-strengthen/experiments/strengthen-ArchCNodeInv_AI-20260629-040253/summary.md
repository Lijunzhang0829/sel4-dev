# strengthen summary — proof/invariant-abstract/ARM/ArchCNodeInv_AI.thy (AInvs)

- baseline wall: **47504 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **2/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-cap_swap_asid_map__ | `cap_swap_asid_map'` | P | named | trial_passed | additive | 0.76 |
| 01-P-vs_cap_ref_master_no_base_ | `vs_cap_ref_master_no_base` | P | named | TRIAL-FAILED |  |  |
| 02-P-post_cap_delete_pre_is_final_cap___ | `post_cap_delete_pre_is_final_cap''` | P | named | trial_passed | additive | 1.08 |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
