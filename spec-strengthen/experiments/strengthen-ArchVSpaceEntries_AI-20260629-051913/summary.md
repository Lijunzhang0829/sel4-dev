# strengthen summary — proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy (AInvs)

- baseline wall: **55083 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **3/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-shift_0x3C_set_no_len_ | `shift_0x3C_set_no_len` | P | named | trial_passed | additive | -0.12 |
| 01-P-invoke_cnode_valid_pdpt_objs_no_invs_ | `invoke_cnode_valid_pdpt_objs_no_invs` | P | named | trial_passed | additive | -0.65 |
| 02-P-invoke_cnode_valid_pdpt_objs_min_ | `invoke_cnode_valid_pdpt_objs_min` | P | named | trial_passed | additive | 0.31 |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
