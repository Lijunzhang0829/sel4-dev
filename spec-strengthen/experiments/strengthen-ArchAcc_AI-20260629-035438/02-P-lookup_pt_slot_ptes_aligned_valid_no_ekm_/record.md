# record — `lookup_pt_slot_ptes_aligned_valid_no_ekm`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchAcc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=34953 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L566 之后 |
| 锚定的原 lemma | `lookup_pt_slot_ptes_aligned_valid` (L536) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma lookup_pt_slot_ptes_aligned_valid:
  "\<lbrace>valid_vspace_objs and valid_arch_state
    and equal_kernel_mappings and pspace_aligned
    and valid_global_objs
    and \<exists>\<rhd> pd and page_directory_at pd
    and K (is_aligned vptr 16)\<rbrace>
  lookup_pt_slot pd vptr
  \<lbrace>\<lambda>r s. is_aligned r 6 \<and> (\<forall>x\<in>set [0 , 4 .e. 0x3C]. pte_at (x + r) s)\<rbrace>, -"
  apply (simp add: lookup_pt_slot_def)
  apply (wp get_pde_wp|wpc)+
  apply (clarsimp simp: lookup_pd_slot_def Let_def)
  apply (simp add: pd_shifting_at)
  apply (frule (2) valid_vspace_objsD)
  apply (clarsimp simp: )
  subgoal for s _ _ x
    apply (prop_tac "page_table_at (ptrFromPAddr x) s")
    subgoal
      apply (bspec "(ucast (pd + (vptr >> 20 << 2) && mask pd_bits >> 2))";clarsimp)
      apply (frule kernel_mapping_slots_empty_pdeI)
      apply ((simp add: obj_at_def pte_at_def;fail)+)[4]
      by (clarsimp simp: pde_ref_def valid_global_pts_def valid_arch_state_def second_level_tables_def)
```

## 2. 新增 lemma

```isabelle
lemma lookup_pt_slot_ptes_aligned_valid_no_ekm:
  "\<lbrace>valid_vspace_objs and valid_arch_state
    and pspace_aligned
    and valid_global_objs
    and \<exists>\<rhd> pd and page_directory_at pd
    and K (is_aligned vptr 16)\<rbrace>
  lookup_pt_slot pd vptr
  \<lbrace>\<lambda>r s. is_aligned r 6 \<and> (\<forall>x\<in>set [0 , 4 .e. 0x3C]. pte_at (x + r) s)\<rbrace>, -"
  apply (simp add: lookup_pt_slot_def)
  apply (wp get_pde_wp|wpc)+
  apply (clarsimp simp: lookup_pd_slot_def Let_def)
  apply (simp add: pd_shifting_at)
  apply (frule (2) valid_vspace_objsD)
  apply (clarsimp simp: )
  subgoal for s _ _ x
    apply (prop_tac "page_table_at (ptrFromPAddr x) s")
    subgoal
      apply (bspec "(ucast (pd + (vptr >> 20 << 2) && mask pd_bits >> 2))";clarsimp)
      apply (frule kernel_mapping_slots_empty_pdeI)
      apply ((simp add: obj_at_def pte_at_def;fail)+)[4]
      by (clarsimp simp: pde_ref_def valid_global_pts_def valid_arch_state_def second_level_tables_def)
    apply (rule conjI)
     apply (rule is_aligned_add)
      apply (rule is_aligned_weaken, erule(1) is_aligned_pt)
      apply (simp add: pt_bits_def pageBits_def)
     apply (rule is_aligned_shiftl)
     apply (rule is_aligned_andI1)
     apply (rule is_aligned_shiftr, simp)
    apply clarsimp
    by (erule(1) pte_at_aligned_vptr, simp+)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `equal_kernel_mappings` head `equal_kernel_mappings` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `lookup_pt_slot` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`equal_kernel_mappings`

**agent 论证**：equal_kernel_mappings does not appear textually in the proof body of lookup_pt_slot_ptes_aligned_valid; it is forwarded implicitly to kernel_mapping_slots_empty_pdeI. However kernel_mapping_slots_empty_pdeI at L481 explicitly lists equal_kernel_mappings as a hypothesis, so this drop will likely fail — but the trial cost is one build minute, and lookup_pt_slot is a read op, making it worth the trial.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_vspace_objs and valid_arch_state
    and equal_kernel_mappings and pspace_aligned
    and valid_global_objs
    and \<exists>\<rhd> pd and page_directory_at pd
    and K (is_aligned vptr 16)) ==> (valid_vspace_objs and valid_arch_state
    and pspace_aligned
    and valid_global_objs
    and \<exists>\<rhd> pd and page_directory_at pd
    and K (is_aligned vptr 16)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `equal_kernel_mappings`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future consumers of lookup_pt_slot_ptes_aligned_valid that do not carry equal_kernel_mappings；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***  1. \<lbrakk>valid_vspace_objs s; valid_arch_state s; pspace_aligned s;
***      valid_global_objs s; (ref_ \<rhd> pd) s;
***      typ_at (AArch APageDirectory) pd s; is_aligned vptr 16;
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchAcc_AI:lookup_pt_slot_ptes_aligned_valid_no_ekm" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
