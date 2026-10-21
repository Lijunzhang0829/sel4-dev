# strengthen summary — proof/invariant-abstract/ARM/ArchDetype_AI.thy (AInvs)

- baseline wall: **39565 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-tcb_arch_detype__ | `tcb_arch_detype'` | P | named | IMPACT-FAILED | noop | 4.81 |
| 01-P-valid_vspace_obj__ | `valid_vspace_obj'` | P | named | TRIAL-FAILED |  |  |
| 02-P-delete_objects_invs__ | `delete_objects_invs'` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
