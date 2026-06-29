# strengthen summary — proof/invariant-abstract/ARM/ArchAcc_AI.thy (AInvs)

- baseline wall: **34953 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-lookup_pt_slot_pte_no_pspace_aligned_ | `lookup_pt_slot_pte_no_pspace_aligned` | P | named | TRIAL-FAILED |  |  |
| 01-P-lookup_pt_slot_pte_no_pd_at_ | `lookup_pt_slot_pte_no_pd_at` | P | named | TRIAL-FAILED |  |  |
| 02-P-lookup_pt_slot_ptes_aligned_valid_no_ekm_ | `lookup_pt_slot_ptes_aligned_valid_no_ekm` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
