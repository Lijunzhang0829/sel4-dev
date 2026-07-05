# strengthen summary — proof/invariant-abstract/CSpace_AI.thy (AInvs)

- baseline wall: **65050 ms**
- candidates proposed: **4**
- accepted (trial+impact+delivery pass): **4/4**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-F-set_cdt_is_original_cap_ | `set_cdt_is_original_cap` | F | wp | trial_passed | additive | 0.22 |
| 01-F-update_cdt_is_original_cap_ | `update_cdt_is_original_cap` | F | wp | trial_passed | additive | 1.0 |
| 02-F-set_cap_cdt_wp_ | `set_cap_cdt_wp` | F | wp | trial_passed | additive | 1.45 |
| 03-F-set_cap_is_original_cap_wp_ | `set_cap_is_original_cap_wp` | F | wp | trial_passed | additive | 1.62 |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
