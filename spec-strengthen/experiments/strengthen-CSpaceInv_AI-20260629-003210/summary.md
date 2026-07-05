# strengthen summary — proof/invariant-abstract/CSpaceInv_AI.thy (AInvs)

- baseline wall: **44739 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-zombies_tcb_update__ | `zombies_tcb_update'` | P | named | TRIAL-FAILED |  |  |
| 01-P-ifunsafe_tcb_update__ | `ifunsafe_tcb_update'` | P | named | TRIAL-FAILED |  |  |
| 02-P-valid_objs_tcb_update__ | `valid_objs_tcb_update'` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
