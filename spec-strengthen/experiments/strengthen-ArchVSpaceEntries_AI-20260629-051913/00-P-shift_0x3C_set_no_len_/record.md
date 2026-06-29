# record — `shift_0x3C_set_no_len`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=55083 ms · trial=55016 ms · Δ -0.12% |
| 锚点 | 插入于 L126 之后 |
| 锚定的原 lemma | `shift_0x3C_set` (L98) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma shift_0x3C_set:
  "\<lbrakk> is_aligned p 6; 8 \<le> bits; bits < 32; len_of TYPE('a) = bits - 2 \<rbrakk> \<Longrightarrow>
   (\<lambda>x. ucast (x + p && mask bits >> 2) :: ('a :: len) word) ` set [0 :: word32 , 4 .e. 0x3C]
        = {x. x && ~~ mask 4 = ucast (p && mask bits >> 2)}"
  apply (clarsimp simp: upto_enum_step_def word_shift_by_2 image_image)
  apply (subst image_cong[where N="{x. x < 2 ^ 4}"])
    apply (safe, simp_all)[1]
     apply (drule plus_one_helper2, simp_all)[1]
    apply (drule word_le_minus_one_leq, simp_all)[1]
   apply (rule_tac f="\<lambda>x. ucast (x && mask bits >> 2)" in arg_cong)
   apply (rule trans[OF add.commute is_aligned_add_or], assumption)
   apply (rule shiftl_less_t2n, simp_all)[1]
  apply safe
   apply (frule upper_bits_unset_is_l2p_32[THEN iffD2, rotated])
    apply (simp add: word_bits_conv)
   apply (rule word_eqI)
   apply (simp add: word_ops_nth_size word_size nth_ucast nth_shiftr
                    nth_shiftl neg_mask_test_bit
                    word_bits_conv)
   apply (safe, simp_all add: is_aligned_nth)[1]
  apply (rule_tac x="ucast x && mask 4" in image_eqI)
   apply (rule word_eqI[rule_format])
   apply (drule_tac x=n in word_eqD)
   apply (simp add: word_ops_nth_size word_size nth_ucast nth_shiftr
                    nth_shiftl)
   apply (safe, simp_all)
  apply (rule order_less_le_trans, rule and_mask_less_size)
   apply (simp_all add: word_size)
  done
```

## 2. 新增 lemma

```isabelle
lemma shift_0x3C_set_no_len:
  "\<lbrakk> is_aligned p 6; 8 \<le> bits; bits < 32 \<rbrakk> \<Longrightarrow>
   (\<lambda>x. ucast (x + p && mask bits >> 2) :: ('a :: len) word) ` set [0 :: word32 , 4 .e. 0x3C]
        = {x. x && ~~ mask 4 = ucast (p && mask bits >> 2)}"
  apply (clarsimp simp: upto_enum_step_def word_shift_by_2 image_image)
  apply (subst image_cong[where N="{x. x < 2 ^ 4}"])
    apply (safe, simp_all)[1]
     apply (drule plus_one_helper2, simp_all)[1]
    apply (drule word_le_minus_one_leq, simp_all)[1]
   apply (rule_tac f="\<lambda>x. ucast (x && mask bits >> 2)" in arg_cong)
   apply (rule trans[OF add.commute is_aligned_add_or], assumption)
   apply (rule shiftl_less_t2n, simp_all)[1]
  apply safe
   apply (frule upper_bits_unset_is_l2p_32[THEN iffD2, rotated])
    apply (simp add: word_bits_conv)
   apply (rule word_eqI)
   apply (simp add: word_ops_nth_size word_size nth_ucast nth_shiftr
                    nth_shiftl neg_mask_test_bit
                    word_bits_conv)
   apply (safe, simp_all add: is_aligned_nth)[1]
  apply (rule_tac x="ucast x && mask 4" in image_eqI)
   apply (rule word_eqI[rule_format])
   apply (drule_tac x=n in word_eqD)
   apply (simp add: word_ops_nth_size word_size nth_ucast nth_shiftr
                    nth_shiftl)
   apply (safe, simp_all)
  apply (rule order_less_le_trans, rule and_mask_less_size)
   apply (simp_all add: word_size)
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `len_of TYPE('a) = bits - 2` head `len_of` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`len_of TYPE('a) = bits - 2`

**agent 论证**：The proof body of shift_0x3C_set never mentions len_of or any tactic that explicitly consumes the 'len_of TYPE('a) = bits - 2' hypothesis. The two other conjuncts (is_aligned p 6 and 8 <= bits) are textually consumed via is_aligned_add_or and shiftl_less_t2n respectively. The high-priority differential signal (0023 shape) strongly suggests this conjunct is dropped implicitly. The proof is verbatim from the original.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(is_aligned p 6 \<and> 8 \<le> bits \<and> bits < 32) \<Longrightarrow> (is_aligned p 6 \<and> 8 \<le> bits \<and> bits < 32 \<and> len_of TYPE('a) = bits - 2)

**delivery**：named，目标：Any consumer of shift_0x3C_set that can drop the len_of constraint; likely mapM_x_store_invalid_pte_valid_pdpt or mapM_x_store_pde_valid_pdpt_objs call sites；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (55016ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchVSpaceEntries_AI:shift_0x3C_set_no_len" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
