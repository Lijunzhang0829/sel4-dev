# record — `some_get_page_info_umapsD_no_valid_objs`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchAInvsPre.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=33439 ms · trial=35102 ms · Δ 4.97% |
| 锚点 | 插入于 L164 之后 |
| 锚定的原 lemma | `some_get_page_info_umapsD` (L112) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma some_get_page_info_umapsD:
  "\<lbrakk>get_page_info (\<lambda>obj. get_arch_obj (kheap s obj)) pd_ref p = Some (b, a, attr, r);
    (\<exists>\<rhd> pd_ref) s; p \<notin> kernel_mappings; valid_vspace_objs s; pspace_aligned s;
    valid_asid_table (arm_asid_table (arch_state s)) s; valid_objs s\<rbrakk>
   \<Longrightarrow> (\<exists>sz. pageBitsForSize sz = a \<and> is_aligned b a \<and>
             data_at sz (ptrFromPAddr b) s)"
  apply (clarsimp simp: get_page_info_def get_pd_entry_def get_arch_obj_def
                        kernel_mappings_slots_eq
                 split: option.splits Structures_A.kernel_object.splits
                        arch_kernel_obj.splits)
  apply (frule (1) valid_vspace_objsD[rotated 2])
   apply (simp add: obj_at_def)
  apply (simp add: valid_vspace_obj_def)
  apply (drule bspec, simp)
  apply (simp split: pde.splits)
    apply (rename_tac rs pd pt_ref rights w)
    apply (subgoal_tac
        "((rs, pd_ref) \<rhd>1
          (VSRef (ucast (ucast (p >> 20))) (Some APageDirectory) # rs,
           ptrFromPAddr pt_ref)) s")
     prefer 2
     apply (rule vs_lookup1I[rotated 2], simp)
      apply (simp add: obj_at_def)
     apply (simp add: vs_refs_def pde_ref_def image_def graph_of_def)
     apply (rule exI, rule conjI, simp+)
    apply (frule (1) vs_lookup_step)
    apply (drule (2) stronger_vspace_objsD[where ref="x # xs" for x xs])
    apply clarsimp
    apply (case_tac ao, simp_all add: a_type_simps obj_at_def )[1]
     apply (simp add: get_pt_info_def get_pt_entry_def)
     apply (drule_tac x="(ucast ((p >> 12) && mask 8))" in spec)
     apply (clarsimp simp: obj_at_def split: pte.splits,intro exI conjI,simp_all)[1]
      apply (frule obj_bits_data_at)
      apply (clarsimp simp: pspace_aligned_def data_at_def)
      apply (drule_tac x = "(ptrFromPAddr b)" in  bspec )
       apply (fastforce simp: obj_at_def)
      apply (clarsimp dest!: is_aligned_ptrFromPAddrD)
     apply (frule (1) data_at_aligned)
     apply (intro exI conjI, simp_all add: pageBits_def is_aligned_ptrFromPAddrD)[1]
    apply (simp add: get_pt_info_def get_pt_entry_def)
   apply (frule obj_bits_data_at)
   apply (intro exI conjI, simp_all add: pageBits_def)[1]
   apply (clarsimp simp: pspace_aligned_def data_at_def)
   apply (drule_tac x = "(ptrFromPAddr b)" in  bspec)
    apply (fastforce simp: obj_at_def)
   apply (clarsimp dest!: is_aligned_ptrFromPAddrD)
  apply (frule obj_bits_data_at)
  apply (intro exI conjI, simp_all add: pageBits_def)[1]
  apply (clarsimp simp: pspace_aligned_def data_at_def)
  apply (drule_tac x = "(ptrFromPAddr b)" in  bspec)
   apply (fastforce simp: obj_at_def)
  apply (clarsimp dest!: is_aligned_ptrFromPAddrD)
  done
```

## 2. 新增 lemma

```isabelle
lemma some_get_page_info_umapsD_no_valid_objs:
  "\<lbrakk>get_page_info (\<lambda>obj. get_arch_obj (kheap s obj)) pd_ref p = Some (b, a, attr, r);
    (\<exists>\<rhd> pd_ref) s; p \<notin> kernel_mappings; valid_vspace_objs s; pspace_aligned s;
    valid_asid_table (arm_asid_table (arch_state s)) s\<rbrakk>
   \<Longrightarrow> (\<exists>sz. pageBitsForSize sz = a \<and> is_aligned b a \<and>
              data_at sz (ptrFromPAddr b) s)"
  apply (clarsimp simp: get_page_info_def get_pd_entry_def get_arch_obj_def
                        kernel_mappings_slots_eq
                 split: option.splits Structures_A.kernel_object.splits
                        arch_kernel_obj.splits)
  apply (frule (1) valid_vspace_objsD[rotated 2])
   apply (simp add: obj_at_def)
  apply (simp add: valid_vspace_obj_def)
  apply (drule bspec, simp)
  apply (simp split: pde.splits)
    apply (rename_tac rs pd pt_ref rights w)
    apply (subgoal_tac
        "((rs, pd_ref) \<rhd>1
          (VSRef (ucast (ucast (p >> 20))) (Some APageDirectory) # rs,
           ptrFromPAddr pt_ref)) s")
     prefer 2
     apply (rule vs_lookup1I[rotated 2], simp)
      apply (simp add: obj_at_def)
     apply (simp add: vs_refs_def pde_ref_def image_def graph_of_def)
     apply (rule exI, rule conjI, simp+)
    apply (frule (1) vs_lookup_step)
    apply (drule (2) stronger_vspace_objsD[where ref="x # xs" for x xs])
    apply clarsimp
    apply (case_tac ao, simp_all add: a_type_simps obj_at_def )[1]
     apply (simp add: get_pt_info_def get_pt_entry_def)
     apply (drule_tac x="(ucast ((p >> 12) && mask 8))" in spec)
     apply (clarsimp simp: obj_at_def split: pte.splits,intro exI conjI,simp_all)[1]
      apply (frule obj_bits_data_at)
      apply (clarsimp simp: pspace_aligned_def data_at_def)
      apply (drule_tac x = "(ptrFromPAddr b)" in  bspec )
       apply (fastforce simp: obj_at_def)
      apply (clarsimp dest!: is_aligned_ptrFromPAddrD)
     apply (frule (1) data_at_aligned)
     apply (intro exI conjI, simp_all add: pageBits_def is_aligned_ptrFromPAddrD)[1]
    apply (simp add: get_pt_info_def get_pt_entry_def)
   apply (frule obj_bits_data_at)
   apply (intro exI conjI, simp_all add: pageBits_def)[1]
   apply (clarsimp simp: pspace_aligned_def data_at_def)
   apply (drule_tac x = "(ptrFromPAddr b)" in  bspec)
    apply (fastforce simp: obj_at_def)
   apply (clarsimp dest!: is_aligned_ptrFromPAddrD)
  apply (frule obj_bits_data_at)
  apply (intro exI conjI, simp_all add: pageBits_def)[1]
  apply (clarsimp simp: pspace_aligned_def data_at_def)
  apply (drule_tac x = "(ptrFromPAddr b)" in  bspec)
   apply (fastforce simp: obj_at_def)
  apply (clarsimp dest!: is_aligned_ptrFromPAddrD)
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `valid_asid_table (arm_asid_table (arch_state s)) s` head `valid_asid_table` never appears in the proof body (prefix match incl. _def/_E forms); 3 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`valid_asid_table (arm_asid_table (arch_state s)) s`

**agent 论证**：The premise `valid_objs s` does not appear anywhere in the proof body of some_get_page_info_umapsD (lines 118-164). The proof drives the result through valid_vspace_objsD and stronger_vspace_objsD (which require valid_vspace_objs and reachability), obj_bits_data_at (which only needs data_at), and pspace_aligned_def (which needs pspace_aligned). None of these visibly require valid_objs as a side condition.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(get_page_info ... = Some ... \<and> (\<exists>\<rhd> pd_ref) s \<and> p \<notin> kernel_mappings \<and> valid_vspace_objs s \<and> pspace_aligned s \<and> valid_asid_table ... s) ==> (get_page_info ... = Some ... \<and> (\<exists>\<rhd> pd_ref) s \<and> p \<notin> kernel_mappings \<and> valid_vspace_objs s \<and> pspace_aligned s \<and> valid_asid_table ... s \<and> valid_objs s)

**delivery**：named，目标：Future callers of some_get_page_info_umapsD can switch to the weaker precondition; ptable_rights_imp_frame is a candidate consumer.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (35102ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchAInvsPre:some_get_page_info_umapsD_no_valid_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
