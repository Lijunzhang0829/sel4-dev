# record — `checked_insert_tcb_invs_no_tcb_cap_valid`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchTcb_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=93808 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L135 之后 |
| 锚定的原 lemma | `checked_insert_tcb_invs` (L95) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma checked_insert_tcb_invs[wp]: (* arch specific *)
  "\<lbrace>invs and cte_wp_at (\<lambda>c. c = cap.NullCap) (target, ref)
        and K (is_cnode_or_valid_arch new_cap) and valid_cap new_cap
        and tcb_cap_valid new_cap (target, ref)
        and K (is_pt_cap new_cap \<or> is_pd_cap new_cap
                         \<longrightarrow> cap_asid new_cap \<noteq> None)
        and (cte_wp_at (\<lambda>c. obj_refs c = obj_refs new_cap
                              \<longrightarrow> table_cap_ref c = table_cap_ref new_cap \<and>
                                 pt_pd_asid c = pt_pd_asid new_cap) src_slot)\<rbrace>
     check_cap_at new_cap src_slot
      (check_cap_at (cap.ThreadCap target) slot
       (cap_insert new_cap src_slot (target, ref))) \<lbrace>\<lambda>rv. invs\<rbrace>"
  apply (simp add: check_cap_at_def)
  apply (rule hoare_pre)
   apply (wp get_cap_wp)
  apply (clarsimp simp: cte_wp_at_caps_of_state)
  apply (frule caps_of_state_valid_cap[where p=src_slot], fastforce)
  apply (frule caps_of_state_valid_cap[where p=slot], fastforce)
  apply (rule conjI, simp only: ex_cte_cap_wp_to_def)
   apply (rule_tac x=slot in exI)
   apply (clarsimp simp: cte_wp_at_caps_of_state same_object_as_cte_refs)
   apply (clarsimp simp: same_object_as_def2 cap_master_cap_simps
                  dest!: cap_master_cap_eqDs)
   apply (clarsimp simp: valid_cap_def[where c="cap.ThreadCap x" for x])
   apply (erule cte_wp_atE[OF caps_of_state_cteD])
    apply (fastforce simp: obj_at_def is_obj_defs)
   apply clarsimp
  apply (rule conjI)
   apply clarsimp
   apply (subgoal_tac "\<not> is_zombie new_cap")
    apply (simp add: same_object_zombies same_object_obj_refs)
    apply (erule(2) zombies_final_helperE)
      apply fastforce
     apply (fastforce simp add: cte_wp_at_caps_of_state)
    apply assumption
   apply (clarsimp simp: is_cnode_or_valid_arch_def is_cap_simps
                         is_valid_vtable_root_def)
  apply (rule conjI)
   apply (erule(1) checked_insert_is_derived, simp+)
  apply (auto simp: is_cnode_or_valid_arch_def is_cap_simps)
  done
```

## 2. 新增 lemma

```isabelle
lemma checked_insert_tcb_invs_no_tcb_cap_valid[wp]:  (* arch specific *)
  "\<lbrace>invs and cte_wp_at (\<lambda>c. c = cap.NullCap) (target, ref)
        and K (is_cnode_or_valid_arch new_cap) and valid_cap new_cap
        and K (is_pt_cap new_cap \<or> is_pd_cap new_cap
                         \<longrightarrow> cap_asid new_cap \<noteq> None)
        and (cte_wp_at (\<lambda>c. obj_refs c = obj_refs new_cap
                              \<longrightarrow> table_cap_ref c = table_cap_ref new_cap \<and>
                                 pt_pd_asid c = pt_pd_asid new_cap) src_slot)\<rbrace>
     check_cap_at new_cap src_slot
      (check_cap_at (cap.ThreadCap target) slot
       (cap_insert new_cap src_slot (target, ref))) \<lbrace>\<lambda>rv. invs\<rbrace>"
  apply (simp add: check_cap_at_def)
  apply (rule hoare_pre)
   apply (wp get_cap_wp)
  apply (clarsimp simp: cte_wp_at_caps_of_state)
  apply (frule caps_of_state_valid_cap[where p=src_slot], fastforce)
  apply (frule caps_of_state_valid_cap[where p=slot], fastforce)
  apply (rule conjI, simp only: ex_cte_cap_wp_to_def)
   apply (rule_tac x=slot in exI)
   apply (clarsimp simp: cte_wp_at_caps_of_state same_object_as_cte_refs)
   apply (clarsimp simp: same_object_as_def2 cap_master_cap_simps
                  dest!: cap_master_cap_eqDs)
   apply (clarsimp simp: valid_cap_def[where c="cap.ThreadCap x" for x])
   apply (erule cte_wp_atE[OF caps_of_state_cteD])
    apply (fastforce simp: obj_at_def is_obj_defs)
   apply clarsimp
  apply (rule conjI)
   apply clarsimp
   apply (subgoal_tac "\<not> is_zombie new_cap")
    apply (simp add: same_object_zombies same_object_obj_refs)
    apply (erule(2) zombies_final_helperE)
      apply fastforce
     apply (fastforce simp add: cte_wp_at_caps_of_state)
    apply assumption
   apply (clarsimp simp: is_cnode_or_valid_arch_def is_cap_simps
                         is_valid_vtable_root_def)
  apply (rule conjI)
   apply (erule(1) checked_insert_is_derived, simp+)
  apply (auto simp: is_cnode_or_valid_arch_def is_cap_simps)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `tcb_cap_valid new_cap (target, ref)` head `tcb_cap_valid` never appears in the proof body (prefix match incl. _def/_E forms); 3 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `check_cap_at` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`tcb_cap_valid new_cap (target, ref)`

**agent 论证**：The proof body of checked_insert_tcb_invs never mentions tcb_cap_valid — it does not appear in any simp rule name, frule, drule, or clarsimp hint across lines 107-135. The proof body is copied verbatim with the conjunct removed from the precondition.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(invs and cte_wp_at (\<lambda>c. c = cap.NullCap) (target, ref)
        and K (is_cnode_or_valid_arch new_cap) and valid_cap new_cap
        and tcb_cap_valid new_cap (target, ref)
        and K (is_pt_cap new_cap \<or> is_pd_cap new_cap
                         \<longrightarrow> cap_asid new_cap \<noteq> None)
        and (cte_wp_at (\<lambda>c. obj_refs c = obj_refs new_cap
                              \<longrightarrow> table_cap_ref c = table_cap_ref new_cap \<and>
                                 pt_pd_asid c = pt_pd_asid new_cap) src_slot)) ==> (invs and cte_wp_at (\<lambda>c. c = cap.NullCap) (target, ref)
        and K (is_cnode_or_valid_arch new_cap) and valid_cap new_cap
        and K (is_pt_cap new_cap \<or> is_pd_cap new_cap
                         \<longrightarrow> cap_asid new_cap \<noteq> None)
        and (cte_wp_at (\<lambda>c. obj_refs c = obj_refs new_cap
                              \<longrightarrow> table_cap_ref c = table_cap_ref new_cap \<and>
                                 pt_pd_asid c = pt_pd_asid new_cap) src_slot)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `tcb_cap_valid new_cap (target, ref)`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future callers of checked_insert_tcb_invs that do not establish tcb_cap_valid in context；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                          ex_cte_cap_wp_to (appropriate_cte_cap new_cap)
***                           (target, ref) s \<and>
***                          (\<forall>r\<in>obj_refs new_cap.
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchTcb_AI:checked_insert_tcb_invs_no_tcb_cap_valid" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
