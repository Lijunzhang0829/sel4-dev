# record — `decode_inv_wf_no_real_cte`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Syscall_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=86011 ms · trial=78889 ms · Δ -8.28% |
| 锚点 | 插入于 L666 之后 |
| 锚定的原 lemma | `decode_inv_wf` (L621) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma decode_inv_wf[wp]:
  "\<lbrace>valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and real_cte_at slot
           and ex_cte_cap_to slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)\<rbrace>
     decode_invocation label args cap_index slot cap excaps
   \<lbrace>valid_invocation\<rbrace>,-"
  apply (simp add: decode_invocation_def cong: cap.case_cong if_cong split del: if_split)
  apply (rule hoare_pre)
   apply (wp Tcb_AI.decode_tcb_inv_wf decode_domain_inv_wf[simplified split_def] | wpc |
          simp add: o_def uncurry_def split_def del: is_cnode_cap.simps cte_refs.simps)+
  apply (strengthen cnode_eq_strg)
  apply (clarsimp simp: valid_cap_def cte_wp_at_eq_simp is_cap_simps
                        cap_rights_update_def ex_cte_cap_wp_to_weakenE[OF _ TrueI]
                        cte_wp_at_caps_of_state
                 split: cap.splits option.splits)
       apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
       apply (drule (1) bspec)+
       apply (subst split_paired_Ex[symmetric], rule exI, simp)
      apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
      apply (rule conjI)
       apply (subst split_paired_Ex[symmetric], rule_tac x=slot in exI, simp)
      apply clarsimp
      apply (drule (1) bspec)+
      apply (subst split_paired_Ex[symmetric], rule exI, simp)
     apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
     apply (drule (1) bspec)+
     apply (clarsimp simp add: ex_cte_cap_wp_to_weakenE[OF _ TrueI])
     apply (rule eq_no_cap_to_obj_with_diff_ref)
      apply (fastforce simp add: cte_wp_at_caps_of_state)
     apply (simp add: invs_valid_arch_caps)
    apply (simp add: invs_valid_objs invs_valid_global_refs)
   apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
   apply (drule (1) bspec)+
   apply (subst split_paired_Ex[symmetric], rule exI, simp)
  apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
  apply (subst split_paired_Ex[symmetric], rule exI, simp)
  done
```

## 2. 新增 lemma

```isabelle
lemma decode_inv_wf_no_real_cte:
  "\<lbrace>valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and ex_cte_cap_to slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)\<rbrace>
     decode_invocation label args cap_index slot cap excaps
   \<lbrace>valid_invocation\<rbrace>,-"
  apply (simp add: decode_invocation_def cong: cap.case_cong if_cong split del: if_split)
  apply (rule hoare_pre)
   apply (wp Tcb_AI.decode_tcb_inv_wf decode_domain_inv_wf[simplified split_def] | wpc |
          simp add: o_def uncurry_def split_def del: is_cnode_cap.simps cte_refs.simps)+
  apply (strengthen cnode_eq_strg)
  apply (clarsimp simp: valid_cap_def cte_wp_at_eq_simp is_cap_simps
                        cap_rights_update_def ex_cte_cap_wp_to_weakenE[OF _ TrueI]
                        cte_wp_at_caps_of_state
                 split: cap.splits option.splits)
       apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
       apply (drule (1) bspec)+
       apply (subst split_paired_Ex[symmetric], rule exI, simp)
      apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
      apply (rule conjI)
       apply (subst split_paired_Ex[symmetric], rule_tac x=slot in exI, simp)
      apply clarsimp
      apply (drule (1) bspec)+
      apply (subst split_paired_Ex[symmetric], rule exI, simp)
     apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
     apply (drule (1) bspec)+
     apply (clarsimp simp add: ex_cte_cap_wp_to_weakenE[OF _ TrueI])
     apply (rule eq_no_cap_to_obj_with_diff_ref)
      apply (fastforce simp add: cte_wp_at_caps_of_state)
     apply (simp add: invs_valid_arch_caps)
    apply (simp add: invs_valid_objs invs_valid_global_refs)
   apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
   apply (drule (1) bspec)+
   apply (subst split_paired_Ex[symmetric], rule exI, simp)
  apply (thin_tac " \<forall>x\<in>set excaps. P x \<and> Q x" for P Q)+
  apply (subst split_paired_Ex[symmetric], rule exI, simp)
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `real_cte_at slot` head `real_cte_at` never appears in the proof body (prefix match incl. _def/_E forms); 3 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `decode_invocation` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`real_cte_at slot`

**agent 论证**：The conjunct `real_cte_at slot` does not appear in any tactic in the proof body (no `real_cte_at`, `real_cte_at_def`, etc.). The op is `decode_invocation`, a read/decode operation whose wp-chain decomposition goes through `decode_tcb_inv_wf` and `decode_domain_inv_wf`; neither visibly consumes a slot-validity predicate about the caller's own slot. The remaining conjuncts (`invs`, `cte_wp_at ((=) cap) slot`, `ex_cte_cap_to slot`) are consumed explicitly in the clarsimp/eq_no_cap_to_obj_with_diff_ref steps.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and real_cte_at slot
           and ex_cte_cap_to slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)) ==> (valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and ex_cte_cap_to slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `real_cte_at slot`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Any caller of decode_inv_wf that does not hold real_cte_at slot in its precondition context (e.g. future handle_invocation refinements); wire in a follow-up patch.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (78889ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:Syscall_AI:decode_inv_wf_no_real_cte" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
