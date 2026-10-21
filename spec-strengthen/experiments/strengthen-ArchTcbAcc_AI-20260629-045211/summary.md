# strengthen summary — proof/invariant-abstract/ARM/ArchTcbAcc_AI.thy (AInvs)

- baseline wall: **43079 ms**
- candidates proposed: **3**
- accepted (trial+impact+delivery pass): **0/3**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-P-get_cap_valid_ipc_no_valid_objs_ | `get_cap_valid_ipc_no_valid_objs` | P | named | TRIAL-FAILED |  |  |
| 01-P-cap_master_cap_tcb_cap_valid_arch_no_is_arch_cap_ | `cap_master_cap_tcb_cap_valid_arch_no_is_arch_cap` | P | named | TRIAL-FAILED |  |  |
| 02-P-cap_master_cap_tcb_cap_valid_arch_no_vtable_hyp_ | `cap_master_cap_tcb_cap_valid_arch_no_vtable_hyp` | P | named | TRIAL-FAILED |  |  |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
