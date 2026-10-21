# record — `cap_insert_ap_invs_no_tcb_valid`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchArch_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=64932 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L620 之后 |
| 锚定的原 lemma | `cap_insert_ap_invs` (L585) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cap_insert_ap_invs:
  "\<lbrace>invs and valid_cap cap and tcb_cap_valid cap dest and
    ex_cte_cap_wp_to (appropriate_cte_cap cap) dest and
    cte_wp_at (\<lambda>c. c = cap.NullCap) dest and
    no_cap_to_obj_with_diff_ref cap {dest} and
    (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s) and
    K (cap = cap.ArchObjectCap (arch_cap.ASIDPoolCap ap asid)) and
   (\<lambda>s. \<forall>irq \<in> cap_irqs cap. irq_issued irq s) and
   ko_at (ArchObj (arch_kernel_obj.ASIDPool Map.empty)) ap and
   (\<lambda>s. ap \<notin> ran (asid_table s) \<and>
        asid_table s (asid_high_bits_of asid) = None)\<rbrace>
  cap_insert cap src dest
  \<lbrace>\<lambda>rv s. invs (s\<lparr>arch_state := arch_state s
                       \<lparr>arm_asid_table := ((arm_asid_table \<circ> arch_state) s)(asid_high_bits_of asid \<mapsto> ap)\<rparr>\<rparr>)\<rbrace>"
  apply (simp add: invs_def valid_state_def valid_pspace_def)
  apply (strengthen valid_arch_state_strg
                    valid_asid_map_asid_upd_strg valid_vspace_objs_asid_upd_strg )
  apply (simp cong: conj_cong)
  apply (rule hoare_pre)
   apply (wp cap_insert_simple_mdb cap_insert_iflive
             cap_insert_zombies cap_insert_ifunsafe cap_insert_vspace_objs
             cap_insert_valid_global_refs cap_insert_idle
             valid_irq_node_typ cap_insert_simple_arch_caps_ap)
  apply (clarsimp simp: is_simple_cap_def cte_wp_at_caps_of_state is_cap_simps)
  apply (frule safe_parent_cap_is_device)
  apply (drule safe_parent_cap_range)
  apply (simp add: cap_range_def)
  apply (rule conjI)
   prefer 2
   apply (clarsimp simp: obj_at_def a_type_def)
  apply (clarsimp simp: cte_wp_at_caps_of_state)
  apply (drule_tac p="(a,b)" in caps_of_state_valid_cap, fastforce)
  apply (auto simp: obj_at_def is_tcb_def is_cap_table_def
                    valid_cap_def [where c="cap.Zombie a b x" for a b x]
              dest: obj_ref_is_tcb obj_ref_is_cap_table split: option.splits)
  done
```

## 2. 新增 lemma

```isabelle
lemma cap_insert_ap_invs_no_tcb_valid:
  "\<lbrace>invs and valid_cap cap and
    ex_cte_cap_wp_to (appropriate_cte_cap cap) dest and
    cte_wp_at (\<lambda>c. c = cap.NullCap) dest and
    no_cap_to_obj_with_diff_ref cap {dest} and
    (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s) and
    K (cap = cap.ArchObjectCap (arch_cap.ASIDPoolCap ap asid)) and
   (\<lambda>s. \<forall>irq \<in> cap_irqs cap. irq_issued irq s) and
   ko_at (ArchObj (arch_kernel_obj.ASIDPool Map.empty)) ap and
   (\<lambda>s. ap \<notin> ran (asid_table s) \<and>
        asid_table s (asid_high_bits_of asid) = None)\<rbrace>
  cap_insert cap src dest
  \<lbrace>\<lambda>rv s. invs (s\<lparr>arch_state := arch_state s
                       \<lparr>arm_asid_table := ((arm_asid_table \<circ> arch_state) s)(asid_high_bits_of asid \<mapsto> ap)\<rparr>\<rparr>)\<rbrace>"
  apply (simp add: invs_def valid_state_def valid_pspace_def)
  apply (strengthen valid_arch_state_strg
                    valid_asid_map_asid_upd_strg valid_vspace_objs_asid_upd_strg )
  apply (simp cong: conj_cong)
  apply (rule hoare_pre)
   apply (wp cap_insert_simple_mdb cap_insert_iflive
             cap_insert_zombies cap_insert_ifunsafe cap_insert_vspace_objs
             cap_insert_valid_global_refs cap_insert_idle
             valid_irq_node_typ cap_insert_simple_arch_caps_ap)
  apply (clarsimp simp: is_simple_cap_def cte_wp_at_caps_of_state is_cap_simps)
  apply (frule safe_parent_cap_is_device)
  apply (drule safe_parent_cap_range)
  apply (simp add: cap_range_def)
  apply (rule conjI)
   prefer 2
   apply (clarsimp simp: obj_at_def a_type_def)
  apply (clarsimp simp: cte_wp_at_caps_of_state)
  apply (drule_tac p="(a,b)" in caps_of_state_valid_cap, fastforce)
  apply (auto simp: obj_at_def is_tcb_def is_cap_table_def
                    valid_cap_def [where c="cap.Zombie a b x" for a b x]
                dest: obj_ref_is_tcb obj_ref_is_cap_table split: option.splits)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `tcb_cap_valid cap dest` head `tcb_cap_valid` never appears in the proof body (prefix match incl. _def/_E forms); 4 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `cap_insert` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`tcb_cap_valid cap dest`

**agent 论证**：The string `tcb_cap_valid` never appears in the proof body of cap_insert_ap_invs. None of the wp rules applied (cap_insert_simple_mdb, cap_insert_iflive, cap_insert_zombies, cap_insert_ifunsafe, cap_insert_vspace_objs, cap_insert_valid_global_refs, cap_insert_idle, valid_irq_node_typ, cap_insert_simple_arch_caps_ap) take tcb_cap_valid as a required precondition for an ASID pool cap. The postcondition is invs of an arch-state update, which does not unfold to tcb_cap_valid. The final `auto` discharge with obj_at_def/is_tcb_def addresses zombie/tcb object shape goals that arise from valid_cap, not from tcb_cap_valid directly.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(invs and valid_cap cap and tcb_cap_valid cap dest and
    ex_cte_cap_wp_to (appropriate_cte_cap cap) dest and
    cte_wp_at (\<lambda>c. c = cap.NullCap) dest and
    no_cap_to_obj_with_diff_ref cap {dest} and
    (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s) and
    K (cap = cap.ArchObjectCap (arch_cap.ASIDPoolCap ap asid)) and
   (\<lambda>s. \<forall>irq \<in> cap_irqs cap. irq_issued irq s) and
   ko_at (ArchObj (arch_kernel_obj.ASIDPool Map.empty)) ap and
   (\<lambda>s. ap \<notin> ran (asid_table s) \<and>
        asid_table s (asid_high_bits_of asid) = None)) ==> (invs and valid_cap cap and
    ex_cte_cap_wp_to (appropriate_cte_cap cap) dest and
    cte_wp_at (\<lambda>c. c = cap.NullCap) dest and
    no_cap_to_obj_with_diff_ref cap {dest} and
    (\<lambda>s. cte_wp_at (safe_parent_for (cdt s) src cap) src s) and
    K (cap = cap.ArchObjectCap (arch_cap.ASIDPoolCap ap asid)) and
   (\<lambda>s. \<forall>irq \<in> cap_irqs cap. irq_issued irq s) and
   ko_at (ArchObj (arch_kernel_obj.ASIDPool Map.empty)) ap and
   (\<lambda>s. ap \<notin> ran (asid_table s) \<and>
        asid_table s (asid_high_bits_of asid) = None)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `tcb_cap_valid cap dest`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future callers of cap_insert_ap_invs where tcb_cap_valid for an ASID-pool cap is not available or inconvenient to establish；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***          (appropriate_cte_cap (ArchObjectCap (ASIDPoolCap ap asid))) dest s;
***         caps_of_state s dest = Some NullCap;
***         no_cap_to_obj_with_diff_ref (ArchObjectCap (ASIDPoolCap ap asid))
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchArch_AI:cap_insert_ap_invs_no_tcb_valid" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
