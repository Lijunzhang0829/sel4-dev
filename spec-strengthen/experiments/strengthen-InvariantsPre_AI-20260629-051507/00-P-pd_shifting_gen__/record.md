# record — `pd_shifting_gen'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/InvariantsPre_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=62028 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L305 之后 |
| 锚定的原 lemma | `pd_shifting_gen` (L295) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma pd_shifting_gen:
  "\<lbrakk>b \<le> a; size pd - c \<le> a - b; is_aligned pd c \<rbrakk> \<Longrightarrow> pd + (vptr >> a << b) && ~~ mask c = pd"
  apply (subgoal_tac "(vptr >> a << b) && ~~ mask c = 0")
   apply (subst word_plus_and_or_coroll)
    apply (erule aligned_mask_disjoint)
    apply (simp add: and_mask_0_iff_le_mask[symmetric])
   apply (simp add: bit.conj_disj_distrib2)
  apply (simp add: shiftr_shiftl1 neg_mask_twice word_bw_assocs)
  apply (rule shiftr_not_mask_0)
  apply (fastforce simp: max_def word_size)
  done
```

## 2. 新增 lemma

```isabelle
lemma pd_shifting_gen':
  "\<lbrakk>b \<le> a; is_aligned pd c \<rbrakk> \<Longrightarrow> pd + (vptr >> a << b) && ~~ mask c = pd"
  apply (subgoal_tac "(vptr >> a << b) && ~~ mask c = 0")
   apply (subst word_plus_and_or_coroll)
    apply (erule aligned_mask_disjoint)
    apply (simp add: and_mask_0_iff_le_mask[symmetric])
   apply (simp add: bit.conj_disj_distrib2)
  apply (simp add: shiftr_shiftl1 neg_mask_twice word_bw_assocs)
  apply (rule shiftr_not_mask_0)
  apply (fastforce simp: max_def word_size)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> [implication lemma — assumption-weakening] conjunct `size pd - c \<le> a - b` head `size` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`size pd - c \<le> a - b`

**agent 论证**：The scanner flagged `size pd - c <= a - b` as never appearing in the proof body. Branch 2 of the proof uses `fastforce simp: max_def word_size` to discharge the residual arithmetic; with `b <= a` still present, `word_size` unfolding may make the size-difference bound redundant. This is genuinely borderline — the trial build is the ground truth.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(b <= a & is_aligned pd c) ==> (b <= a & size pd - c <= a - b & is_aligned pd c)

**delivery**：named，目标：Future callers of pd_shifting_gen that already have b <= a and is_aligned pd c but not the size bound；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***  1. \<lbrakk>b \<le> a; is_aligned pd c\<rbrakk>
***     \<Longrightarrow> LENGTH('a) \<le> a - b + max b c
*** At command "apply" (line 316 of "/tmp/tmp.BX3WLZEtCa/Tmp_595dc45afe4f2158.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:InvariantsPre_AI:pd_shifting_gen'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
