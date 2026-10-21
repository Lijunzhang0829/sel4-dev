# strengthen summary — proof/invariant-abstract/ARM/ArchCSpaceInvPre_AI.thy (AInvs)

- baseline wall: **30696 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-unique_table_refsD_no_left_lookup_ | `unique_table_refsD_no_left_lookup` | P | named | TRIAL-FAILED |  |  |
| 01-P-valid_table_capsD_no_asid_hyp_ | `valid_table_capsD_no_asid_hyp` | P | named | TRIAL-FAILED |  |  |
| 02-P-cap_refs_in_kernel_windowD_no_lookup_ | `cap_refs_in_kernel_windowD_no_lookup` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
