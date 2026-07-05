# strengthen summary — proof/invariant-abstract/ARM/ArchVSpace_AI.thy (AInvs)

- baseline wall: **227453 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-pd_at_asid_unique_no_vspace_objs_ | `pd_at_asid_unique_no_vspace_objs` | P | named | IMPACT-FAILED | noop | 0.98 |
| 01-P-pd_at_asid_unique_no_global_objs_ | `pd_at_asid_unique_no_global_objs` | P | named | IMPACT-FAILED | noop | 22.09 |
| 02-P-lookup_pt_slot_is_aligned_no_global_objs_ | `lookup_pt_slot_is_aligned_no_global_objs` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
