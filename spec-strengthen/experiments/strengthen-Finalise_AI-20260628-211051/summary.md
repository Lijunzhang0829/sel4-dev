# strengthen summary — proof/invariant-abstract/Finalise_AI.thy (AInvs)

- baseline wall: **41432 ms**
- candidates proposed: **2**
- accepted (trial+impact+delivery pass): **0/2**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-Q-cap_delete_one_deletes_reply_strong_ | `cap_delete_one_deletes_reply_strong` | Q | named | TRIAL-FAILED |  |  |
| 01-Q-cancel_ipc_caps_of_state_inv_ | `cancel_ipc_caps_of_state_inv` | Q | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
