# strengthen summary — proof/invariant-abstract/Ipc_AI.thy (AInvs)

- baseline wall: **120624 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-lsfco_real_cte_at_no_valid_objs_ | `lsfco_real_cte_at_no_valid_objs` | P | named | TRIAL-FAILED |  |  |
| 01-P-lsfco_cte_at_no_valid_objs_ | `lsfco_cte_at_no_valid_objs` | P | named | TRIAL-FAILED |  |  |
| 02-P-complete_signal_invs_no_tcb_at_ | `complete_signal_invs_no_tcb_at` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
