# record — `lookup_pt_slot_is_aligned_no_global_objs`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchVSpace_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=227453 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L1349 之后 |
| 锚定的原 lemma | `lookup_pt_slot_is_aligned` (L1315) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma lookup_pt_slot_is_aligned:
  "\<lbrace>(\<exists>\<rhd> pd) and K (vmsz_aligned vptr sz) and K (is_aligned pd pd_bits)
    and valid_arch_state and valid_vspace_objs and equal_kernel_mappings
    and pspace_aligned and valid_global_objs\<rbrace>
     lookup_pt_slot pd vptr
   \<lbrace>\<lambda>rv s. is_aligned rv (pg_entry_align sz)\<rbrace>,-"
  apply (simp add: lookup_pt_slot_def)
  apply (wp get_pde_wp | wpc)+
  apply (clarsimp simp: lookup_pd_slot_pd)
  apply (frule(2) valid_vspace_objsD[rotated])
  apply simp
  apply (rule is_aligned_add)
   apply (case_tac "ucast (lookup_pd_slot pd vptr && mask pd_bits >> 2) \<in> kernel_mapping_slots")
    apply (frule kernel_mapping_slots_empty_pdeI)
     apply (simp add: obj_at_def)+
    apply clarsimp
    apply (erule_tac x="ptrFromPAddr x" in allE)
    apply (simp add: pde_ref_def second_level_tables_def)
    apply (erule is_aligned_weaken[OF is_aligned_global_pt])
      apply ((simp add: invs_psp_aligned invs_vspace_objs invs_arch_state
                        pg_entry_align_def pt_bits_def pageBits_def
                 split: vmpage_size.split)+)[3]
   apply (drule_tac x="ucast (lookup_pd_slot pd vptr && mask pd_bits >> 2)" in bspec, simp)
   apply (clarsimp simp: obj_at_def a_type_def)
   apply (simp split: Structures_A.kernel_object.split_asm if_split_asm
                     arch_kernel_obj.split_asm)
   apply (erule is_aligned_weaken[OF pspace_alignedD], simp)
   apply (simp add: obj_bits_def pg_entry_align_def  split: vmpage_size.splits)
  apply (rule is_aligned_shiftl)
  apply (rule is_aligned_andI1)
  apply (rule is_aligned_shiftr)
  apply (case_tac sz)
     apply (clarsimp simp: vmsz_aligned_def pg_entry_align_def
                    elim!: is_aligned_weaken  split: vmpage_size.splits)+
  done
```

## 2. 新增 lemma

```isabelle
lemma lookup_pt_slot_is_aligned_no_global_objs:
  "\<lbrace>(\<exists>\<rhd> pd) and K (vmsz_aligned vptr sz) and K (is_aligned pd pd_bits)
    and valid_arch_state and valid_vspace_objs and equal_kernel_mappings
    and pspace_aligned\<rbrace>
     lookup_pt_slot pd vptr
   \<lbrace>\<lambda>rv s. is_aligned rv (pg_entry_align sz)\<rbrace>,-"
  apply (simp add: lookup_pt_slot_def)
  apply (wp get_pde_wp | wpc)+
  apply (clarsimp simp: lookup_pd_slot_pd)
  apply (frule(2) valid_vspace_objsD[rotated])
  apply simp
  apply (rule is_aligned_add)
   apply (case_tac "ucast (lookup_pd_slot pd vptr && mask pd_bits >> 2) \<in> kernel_mapping_slots")
    apply (frule kernel_mapping_slots_empty_pdeI)
      apply (simp add: obj_at_def)+
    apply clarsimp
    apply (erule_tac x="ptrFromPAddr x" in allE)
    apply (simp add: pde_ref_def second_level_tables_def)
    apply (erule is_aligned_weaken[OF is_aligned_global_pt])
      apply ((simp add: invs_psp_aligned invs_vspace_objs invs_arch_state
                        pg_entry_align_def pt_bits_def pageBits_def
               split: vmpage_size.split)+)[3]
   apply (drule_tac x="ucast (lookup_pd_slot pd vptr && mask pd_bits >> 2)" in bspec, simp)
   apply (clarsimp simp: obj_at_def a_type_def)
   apply (simp split: Structures_A.kernel_object.split_asm if_split_asm
                     arch_kernel_obj.split_asm)
   apply (erule is_aligned_weaken[OF pspace_alignedD], simp)
   apply (simp add: obj_bits_def pg_entry_align_def  split: vmpage_size.splits)
  apply (rule is_aligned_shiftl)
  apply (rule is_aligned_andI1)
  apply (rule is_aligned_shiftr)
  apply (case_tac sz)
     apply (clarsimp simp: vmsz_aligned_def pg_entry_align_def
                    elim!: is_aligned_weaken  split: vmpage_size.splits)+
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `valid_arch_state` head `valid_arch_state` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `lookup_pt_slot` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_arch_state`

**agent 论证**：The scanner flagged valid_global_objs as never appearing in the proof body of lookup_pt_slot_is_aligned, a read-only decode operation. The proof proceeds through valid_vspace_objsD, pspace_alignedD, and is_aligned_global_pt; is_aligned_global_pt likely requires valid_arch_state but not valid_global_objs directly.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：((\<exists>\<rhd> pd) and K (vmsz_aligned vptr sz) and K (is_aligned pd pd_bits)
    and valid_arch_state and valid_vspace_objs and equal_kernel_mappings
    and pspace_aligned and valid_global_objs) ==> ((\<exists>\<rhd> pd) and K (vmsz_aligned vptr sz) and K (is_aligned pd pd_bits)
    and valid_arch_state and valid_vspace_objs and equal_kernel_mappings
    and pspace_aligned) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `valid_global_objs`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Callers establishing lookup_pt_slot alignment without valid_global_objs available (e.g. after an unmap operation) can use this lighter precondition.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (230965ms)
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchVSpace_AI:lookup_pt_slot_is_aligned_no_global_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
