# strengthen summary — proof/invariant-abstract/KHeap_AI.thy (AInvs)

- baseline wall: **43071 ms**
- candidates proposed: **4**
- accepted (trial+impact+delivery pass): **0/4**  *(dry-run)*

| dir | lemma | slot | delivery | verdict | impact | Δ% |
|---|---|---|---|---|---|---|
| 00-F-set_simple_ko_machine_state_ | `set_simple_ko_machine_state` | F | wp | IMPACT-FAILED | noop | -1.4 |
| 01-F-set_simple_ko_cur_thread_ | `set_simple_ko_cur_thread` | F | wp | IMPACT-FAILED | noop | -0.4 |
| 02-F-set_simple_ko_domain_index_ | `set_simple_ko_domain_index` | F | wp | TRIAL-FAILED |  |  |
| 03-F-set_simple_ko_domain_time_ | `set_simple_ko_domain_time` | F | wp | IMPACT-FAILED | noop | 0.88 |

Verdict legend: `applied` = committed to source · `trial_passed` = dry-run accept · `trial_failed` = patch did not build · `impact_failed` = verdict/wall gate · `rejected_delivery` = failed the §2.5 delivery contract before any build.

Each `NN-<slot>-<lemma>/` holds: proposal.json (agent output), delivery_gate.json, range-patch.patch.txt, trial.log, measurement.json, patch.diff, decision.md, command.sh.
