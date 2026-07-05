# strengthen summary — proof/invariant-abstract/ARM/ArchArch_AI.thy (AInvs)

- baseline wall: **64932 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-cap_insert_ap_invs_no_tcb_valid_ | `cap_insert_ap_invs_no_tcb_valid` | P | named | TRIAL-FAILED |  |  |
| 01-P-retype_region_no_cap_to_obj_no_mdb_ | `retype_region_no_cap_to_obj_no_mdb` | P | named | TRIAL-FAILED |  |  |
| 02-P-retype_region_no_cap_to_obj_no_pspace_ | `retype_region_no_cap_to_obj_no_pspace` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
