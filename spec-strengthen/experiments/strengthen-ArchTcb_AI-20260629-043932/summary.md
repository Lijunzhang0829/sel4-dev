# strengthen summary — proof/invariant-abstract/ARM/ArchTcb_AI.thy (AInvs)

- baseline wall: **93808 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **1/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-same_object_also_valid_no_vptr_ | `same_object_also_valid_no_vptr` | P | named | TRIAL-FAILED |  |  |
| 01-P-same_object_also_valid_no_asid_base_ | `same_object_also_valid_no_asid_base` | P | named | trial_passed | additive | 0.48 |
| 02-P-checked_insert_tcb_invs_no_tcb_cap_valid_ | `checked_insert_tcb_invs_no_tcb_cap_valid` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
