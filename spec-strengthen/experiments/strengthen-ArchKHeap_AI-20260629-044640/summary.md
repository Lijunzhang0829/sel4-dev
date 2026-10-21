# strengthen summary — proof/invariant-abstract/ARM/ArchKHeap_AI.thy (AInvs)

- baseline wall: **43934 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **1/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-arch_valid_obj_same_type__ | `arch_valid_obj_same_type'` | P | named | trial_passed | additive | -5.53 |
| 01-P-valid_table_caps_ptD__ | `valid_table_caps_ptD'` | P | named | TRIAL-FAILED |  |  |
| 02-P-valid_table_caps_ptD_no_cap_ | `valid_table_caps_ptD_no_cap` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
