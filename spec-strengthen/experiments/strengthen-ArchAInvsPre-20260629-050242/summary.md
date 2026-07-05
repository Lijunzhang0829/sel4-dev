# strengthen summary — proof/invariant-abstract/ARM/ArchAInvsPre.thy (AInvs)

- baseline wall: **33439 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **1/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-some_get_page_info_umapsD_no_asid_table_ | `some_get_page_info_umapsD_no_asid_table` | P | named | TRIAL-FAILED |  |  |
| 01-P-some_get_page_info_umapsD_no_valid_objs_ | `some_get_page_info_umapsD_no_valid_objs` | P | named | trial_passed | additive | 4.97 |
| 02-P-some_get_page_info_umapsD_minimal_ | `some_get_page_info_umapsD_minimal` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
