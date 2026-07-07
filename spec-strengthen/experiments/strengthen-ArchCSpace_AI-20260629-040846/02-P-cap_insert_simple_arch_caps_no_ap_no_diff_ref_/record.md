# record — `cap_insert_simple_arch_caps_no_ap_no_diff_ref`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchCSpace_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=42536 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L518 之后 |
| 锚定的原 lemma | `cap_insert_simple_arch_caps_no_ap` (L502) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cap_insert_simple_arch_caps_no_ap:
  "\<lbrace>valid_arch_caps and (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s)
             and no_cap_to_obj_with_diff_ref cap {dest} and K (is_simple_cap cap \<and> \<not>is_ap_cap cap)\<rbrace>
     cap_insert cap src dest
   \<lbrace>\<lambda>rv. valid_arch_caps\<rbrace>"
  apply (simp add: cap_insert_def)
  apply (wp set_cap_valid_arch_caps set_untyped_cap_as_full_valid_arch_caps get_cap_wp
    | simp split del: if_split)+
  apply (wp hoare_vcg_all_lift hoare_vcg_conj_lift hoare_vcg_imp_lift hoare_vcg_ball_lift hoare_vcg_disj_lift
    set_untyped_cap_as_full_cte_wp_at_neg set_untyped_cap_as_full_is_final_cap'_neg
    set_untyped_cap_as_full_empty_table_at hoare_vcg_ex_lift
    set_untyped_cap_as_full_caps_of_state_diff[where dest=dest]
    | wps)+
      apply (wp get_cap_wp)+
  apply (clarsimp simp: cte_wp_at_caps_of_state)
  apply (intro conjI impI allI)
  by (auto simp:is_simple_cap_def[simplified is_simple_cap_arch_def] is_cap_simps)
```

## 2. 新增 lemma

```isabelle
lemma cap_insert_simple_arch_caps_no_ap_no_diff_ref:
  "\<lbrace>valid_arch_caps and (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s)
              and K (is_simple_cap cap \<and> \<not>is_ap_cap cap)\<rbrace>
     cap_insert cap src dest
   \<lbrace>\<lambda>rv. valid_arch_caps\<rbrace>"
  apply (simp add: cap_insert_def)
  apply (wp set_cap_valid_arch_caps set_untyped_cap_as_full_valid_arch_caps get_cap_wp
    | simp split del: if_split)+
  apply (wp hoare_vcg_all_lift hoare_vcg_conj_lift hoare_vcg_imp_lift hoare_vcg_ball_lift hoare_vcg_disj_lift
    set_untyped_cap_as_full_cte_wp_at_neg set_untyped_cap_as_full_is_final_cap'_neg
    set_untyped_cap_as_full_empty_table_at hoare_vcg_ex_lift
    set_untyped_cap_as_full_caps_of_state_diff[where dest=dest]
    | wps)+
      apply (wp get_cap_wp)+
  apply (clarsimp simp: cte_wp_at_caps_of_state)
  apply (intro conjI impI allI)
  by (auto simp:is_simple_cap_def[simplified is_simple_cap_arch_def] is_cap_simps)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `no_cap_to_obj_with_diff_ref cap {dest}` head `no_cap_to_obj_with_diff_ref` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `cap_insert` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`no_cap_to_obj_with_diff_ref cap {dest}`

**agent 论证**：The proof body of cap_insert_simple_arch_caps_no_ap never mentions no_cap_to_obj_with_diff_ref textually. The final auto tactic only discharges is_simple_cap/is_cap_simps goals. For simple non-AP caps the unique_table lemmas used in set_cap_valid_arch_caps are satisfied by is_simple_cap alone (pt/pd caps are excluded). The trial will confirm whether the wp chain for set_cap_valid_arch_caps needs no_cap_to_obj_with_diff_ref or not.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_arch_caps and (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s)
             and no_cap_to_obj_with_diff_ref cap {dest} and K (is_simple_cap cap \<and> \<not>is_ap_cap cap)) ==> (valid_arch_caps and (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s)
              and K (is_simple_cap cap \<and> \<not>is_ap_cap cap)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `no_cap_to_obj_with_diff_ref cap {dest}`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Sites inserting simple non-AP caps where no_cap_to_obj_with_diff_ref is not yet established but valid_arch_caps preservation is still desired.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***         \<forall>p asid.
***            cap \<noteq> ArchObjectCap (PageDirectoryCap p asid)\<rbrakk>
***        \<Longrightarrow> no_cap_to_obj_with_diff_ref cap {dest} s
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchCSpace_AI:cap_insert_simple_arch_caps_no_ap_no_diff_ref" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
