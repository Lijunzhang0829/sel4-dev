# strengthen summary — proof/invariant-abstract/DetSchedAux_AI.thy (AInvs)

- baseline wall: **45511 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **1/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-retype_etcb_at_helper_no_valid_etcbs_ | `retype_etcb_at_helper_no_valid_etcbs` | P | named | trial_passed | additive | -1.56 |
| 01-P-retype_etcb_at_helper_no_inactive_ | `retype_etcb_at_helper_no_inactive` | P | named | TRIAL-FAILED |  |  |
| 02-P-invoke_untyped_valid_sched__ | `invoke_untyped_valid_sched'` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
