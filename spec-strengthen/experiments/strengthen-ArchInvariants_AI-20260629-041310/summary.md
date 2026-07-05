# strengthen summary — proof/invariant-abstract/ARM/ArchInvariants_AI.thy (AInvs)

- baseline wall: **76551 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-wellformed_arch_pspace_no_kheap_ | `wellformed_arch_pspace_no_kheap` | P | named | IMPACT-FAILED | weakening | -0.73 |
| 01-P-valid_arch_tcb_pspaceI_no_kheap_ | `valid_arch_tcb_pspaceI_no_kheap` | P | named | IMPACT-FAILED | noop | -0.51 |
| 02-P-empty_table_is_valid_no_arch_state_ | `empty_table_is_valid_no_arch_state` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
