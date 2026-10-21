# strengthen summary — proof/invariant-abstract/ARM/ArchCSpace_AI.thy (AInvs)

- baseline wall: **42536 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-ex_nonz_tcb_cte_caps_no_tcb_ | `ex_nonz_tcb_cte_caps_no_tcb` | P | named | TRIAL-FAILED |  |  |
| 01-P-cap_insert_simple_invs_no_tcb_cap_valid_ | `cap_insert_simple_invs_no_tcb_cap_valid` | P | named | TRIAL-FAILED |  |  |
| 02-P-cap_insert_simple_arch_caps_no_ap_no_diff_ref_ | `cap_insert_simple_arch_caps_no_ap_no_diff_ref` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
