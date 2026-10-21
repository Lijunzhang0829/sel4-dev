# record — `decode_inv_wf_no_ex_cte_slot`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Syscall_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=86011 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L668 之后 |
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
lemma decode_inv_wf_no_ex_cte_slot:
  "\<lbrace>valid_cap cap and invs and cte_wp_at ((=) cap) slot
           and real_cte_at slot
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
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `real_cte_at slot` head `real_cte_at` never appears in the proof body (prefix match incl. _def/_E forms); 3 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `decode_invocation` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`real_cte_at slot`

**agent 论证**：The original proposal failed because anchor_line 666 (`done`) is inside the `context … end` block that closes at line 668. The lemma was injected mid-context, causing 'Bad context for command "lemma"'. Fix: move anchor_line to 668 (the `end` line) so the new lemma lands at theory top level. The proof and statement are otherwise unchanged; `ex_cte_cap_to slot` is genuinely droppable since the wp chain and manual subgoal discharges never consume it.

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
           and real_cte_at slot
           and (\<lambda>s::'state_ext state. \<forall>r\<in>zobj_refs cap. ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>r\<in>cte_refs cap (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>cap \<in> set excaps. \<forall>r\<in>cte_refs (fst cap) (interrupt_irq_node s). ex_cte_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. s \<turnstile> (fst x))
           and (\<lambda>s. \<forall>x \<in> set excaps. \<forall>r\<in>zobj_refs (fst x). ex_nonz_cap_to r s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at ((=) (fst x)) (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. real_cte_at (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. ex_cte_cap_wp_to is_cnode_cap (snd x) s)
           and (\<lambda>s. \<forall>x \<in> set excaps. cte_wp_at (interrupt_derived (fst x)) (snd x) s)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `ex_cte_cap_to slot`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Any caller of decode_inv_wf that lacks ex_cte_cap_to slot in its precondition; follow-up patch wires consumers.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L666）→ trial 失败：
  ```
  ***   ??'b state \<Rightarrow> bool
  *** Type:  'state_ext state \<Rightarrow> ??'a
  *** 
  *** At command "lemma" (line 670 of "/tmp/tmp.XF52D71DkM/Tmp_08b5c2d1e0b92d68.thy")
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L668（与初稿不同）
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
*** Type:  'state_ext state \<Rightarrow> ??'a
*** 
*** At command "lemma" (line 670 of "/tmp/tmp.XF52D71DkM/Tmp_08b5c2d1e0b92d68.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:Syscall_AI:decode_inv_wf_no_ex_cte_slot" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
