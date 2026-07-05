# strengthen summary — proof/invariant-abstract/Detype_AI.thy (AInvs)

- baseline wall: **41356 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-descendants_range_imply_no_descendants_no_untyped_ | `descendants_range_imply_no_descendants_no_untyped` | P | named | TRIAL-FAILED |  |  |
| 01-P-descendants_range_imply_no_descendants_no_valid_objs_ | `descendants_range_imply_no_descendants_no_valid_objs` | P | named | TRIAL-FAILED |  |  |
| 02-P-descendants_range_imply_no_descendants_no_valid_mdb_ | `descendants_range_imply_no_descendants_no_valid_mdb` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
