# record — `lookup_pt_slot_pte_no_pd_at`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchAcc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=34953 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L603 之后 |
| 锚定的原 lemma | `lookup_pt_slot_pte` (L580) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma lookup_pt_slot_pte [wp]:
  "\<lbrace>pspace_aligned and valid_vspace_objs and valid_arch_state
   and equal_kernel_mappings and valid_global_objs
   and \<exists>\<rhd> pd and page_directory_at pd\<rbrace>
  lookup_pt_slot pd vptr \<lbrace>pte_at\<rbrace>,-"
  apply (simp add: lookup_pt_slot_def)
  apply (wp get_pde_wp|wpc)+
  apply (clarsimp simp: lookup_pd_slot_def Let_def)
  apply (simp add: pd_shifting_at)
  apply (drule (2) valid_vspace_objsD)
  apply (clarsimp simp: )
  apply (bspec "ucast (pd + (vptr >> 20 << 2) && mask pd_bits >> 2)")
   apply clarsimp
   apply (erule page_table_pte_atI, simp_all)
   apply (simp add: pt_bits_def pageBits_def)
   apply (rule order_le_less_trans, rule word_and_le1, simp)
  apply (frule kernel_mapping_slots_empty_pdeI)
    apply (simp add: obj_at_def)+
  apply (clarsimp simp: pde_ref_def)
  apply (rule page_table_pte_atI, simp_all)
   apply (simp add: valid_arch_state_def valid_global_pts_def second_level_tables_def)
  apply (simp add: pt_bits_def pageBits_def)
  apply (rule order_le_less_trans, rule word_and_le1, simp)
  done
```

## 2. 新增 lemma

```isabelle
lemma lookup_pt_slot_pte_no_pd_at:
  "\<lbrace>pspace_aligned and valid_vspace_objs and valid_arch_state
   and equal_kernel_mappings and valid_global_objs
   and \<exists>\<rhd> pd\<rbrace>
  lookup_pt_slot pd vptr \<lbrace>pte_at\<rbrace>,-"
  apply (simp add: lookup_pt_slot_def)
  apply (wp get_pde_wp|wpc)+
  apply (clarsimp simp: lookup_pd_slot_def Let_def)
  apply (simp add: pd_shifting_at)
  apply (drule (2) valid_vspace_objsD)
  apply (clarsimp simp: )
  apply (bspec "ucast (pd + (vptr >> 20 << 2) && mask pd_bits >> 2)")
   apply clarsimp
   apply (erule page_table_pte_atI, simp_all)
   apply (simp add: pt_bits_def pageBits_def)
   apply (rule order_le_less_trans, rule word_and_le1, simp)
  apply (frule kernel_mapping_slots_empty_pdeI)
    apply (simp add: obj_at_def)+
  apply (clarsimp simp: pde_ref_def)
  apply (rule page_table_pte_atI, simp_all)
   apply (simp add: valid_arch_state_def valid_global_pts_def second_level_tables_def)
  apply (simp add: pt_bits_def pageBits_def)
  apply (rule order_le_less_trans, rule word_and_le1, simp)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `pspace_aligned` head `pspace_aligned` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `lookup_pt_slot` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`pspace_aligned`

**agent 论证**：page_directory_at pd does not appear textually in the proof body. It is consumed indirectly by pd_shifting_at, but pd_shifting_at may be derivable from pspace_aligned alone together with the ko_at hypothesis produced by get_pde_wp (which establishes that kheap holds a PageDirectory at pd && ~~ mask pd_bits, implying alignment). Lookup ops on read paths are prime candidates. Trial is the judge.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(pspace_aligned and valid_vspace_objs and valid_arch_state
   and equal_kernel_mappings and valid_global_objs
   and \<exists>\<rhd> pd and page_directory_at pd) ==> (pspace_aligned and valid_vspace_objs and valid_arch_state
   and equal_kernel_mappings and valid_global_objs
   and \<exists>\<rhd> pd) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `page_directory_at pd`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future consumers of lookup_pt_slot_pte that carry page_directory_at pd may drop it once this lemma is wired in；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***         equal_kernel_mappings s; valid_global_objs s; (ref \<rhd> pd) s;
***         ako_at (PageDirectory pda)
***          (pd + (vptr >> 20 << 2) && ~~ mask pd_bits) s;
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchAcc_AI:lookup_pt_slot_pte_no_pd_at" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
