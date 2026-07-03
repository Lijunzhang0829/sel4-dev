# strengthen summary — proof/invariant-abstract/Syscall_AI.thy (AInvs)

- baseline wall: **86011 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **1/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-decode_inv_wf_no_real_cte_ | `decode_inv_wf_no_real_cte` | P | named | trial_passed | additive | -8.28 |
| 01-P-decode_inv_wf_no_ex_cte_slot_ | `decode_inv_wf_no_ex_cte_slot` | P | named | TRIAL-FAILED |  |  |
| 02-P-resolve_address_bits_valid_fault2_invs_ | `resolve_address_bits_valid_fault2_invs` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
