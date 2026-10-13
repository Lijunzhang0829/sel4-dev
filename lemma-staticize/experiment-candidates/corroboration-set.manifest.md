# Corroboration set — history-confirmed statically-rewritable lemmas

- 16 cases; all REACHED a valid static proof in prior runs (verdict REACHED-B / PATH-FOUND / ACCEPT / INCONCLUSIVE-* = built but no wall speedup)
- purpose: DISCRIMINATE the 3 methods (DFS / GenStat-stateful / GenStat-blind) on cases that are actually solvable — unlike the all-0 DB-hottest set
- fast/feasible subset (Access + DSpecProofs, small files): 12/16

| ★fast | session | lemma | thy:line | tactic | file_L |
|:--:|---|---|---|---|--:|
| ★ | Access | `auth_graph_map_memI` | Access_AC.thy:83 | fastforce | 1601 |
| ★ | Access | `is_transferable_Endpoint` | Access_AC.thy:115 | blast | 1601 |
| ★ | Access | `is_transferable_IRQ` | Access_AC.thy:109 | blast | 1601 |
| ★ | Access | `is_transferable_Ntfn` | Access_AC.thy:113 | blast | 1601 |
| ★ | Access | `is_transferable_Untyped` | Access_AC.thy:107 | blast | 1601 |
| ★ | Access | `is_transferable_Zombie` | Access_AC.thy:111 | blast | 1601 |
| ★ | Access | `pas_refined_sita_mem` | Access_AC.thy:283 | auto | 1601 |
| ★ | Access | `reply_masters_mdbD1` | CNode_AC.thy:473 | fastforce | 1757 |
| ★ | Access | `tcb_domain_map_wellformed_mono` | Access_AC.thy:231 | auto | 1601 |
| ★ | DSpecProofs | `sep_heap_domD` | RWHelper_DP.thy:171 | fastforce | 502 |
| ★ | DSpecProofs | `sep_heap_domD'` | RWHelper_DP.thy:176 | fastforce | 502 |
| ★ | DSpecProofs | `sep_irq_node_domD'` | RWHelper_DP.thy:181 | fastforce | 502 |
|  | AInvs | `strengthen_imp_ex2` | Untyped_AI.thy:322 | auto | 3947 |
|  | InfoFlow | `ball_subsetE` | Ipc_IF.thy:850 | blast | 2229 |
|  | InfoFlow | `rel_terminate_weaken` | ADT_IF.thy:1693 | force | 3447 |
|  | InfoFlow | `states_equiv_forI` | InfoFlow_IF.thy:84 | auto | 1048 |
