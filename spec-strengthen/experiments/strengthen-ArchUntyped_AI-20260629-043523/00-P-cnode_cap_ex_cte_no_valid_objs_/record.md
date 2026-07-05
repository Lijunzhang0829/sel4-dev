# record — `cnode_cap_ex_cte_no_valid_objs`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchUntyped_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=44813 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L35 之后 |
| 锚定的原 lemma | `cnode_cap_ex_cte` (L24) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cnode_cap_ex_cte[Untyped_AI_assms]:
  "\<lbrakk> is_cnode_cap cap; cte_wp_at (\<lambda>c. \<exists>m. cap = mask_cap m c) p s;
     (s::'state_ext::state_ext state) \<turnstile> cap; valid_objs s; pspace_aligned s \<rbrakk> \<Longrightarrow>
    ex_cte_cap_wp_to is_cnode_cap (obj_ref_of cap, nat_to_cref (bits_of cap) x) s"
  apply (simp only: ex_cte_cap_wp_to_def)
  apply (rule exI, erule cte_wp_at_weakenE)
  apply (clarsimp simp: is_cap_simps bits_of_def)
  apply (case_tac c, simp_all add: mask_cap_def cap_rights_update_def split:bool.splits)
  apply (clarsimp simp: nat_to_cref_def word_bits_def)
  apply (erule(2) valid_CNodeCapE)
  apply (simp add: word_bits_def cte_level_bits_def)
  done
```

## 2. 新增 lemma

```isabelle
lemma cnode_cap_ex_cte_no_valid_objs[Untyped_AI_assms]:
  "\<lbrakk> is_cnode_cap cap; cte_wp_at (\<lambda>c. \<exists>m. cap = mask_cap m c) p s;
     (s::'state_ext::state_ext state) \<turnstile> cap; pspace_aligned s \<rbrakk> \<Longrightarrow>
    ex_cte_cap_wp_to is_cnode_cap (obj_ref_of cap, nat_to_cref (bits_of cap) x) s"
  apply (simp only: ex_cte_cap_wp_to_def)
  apply (rule exI, erule cte_wp_at_weakenE)
  apply (clarsimp simp: is_cap_simps bits_of_def)
  apply (case_tac c, simp_all add: mask_cap_def cap_rights_update_def split:bool.splits)
  apply (clarsimp simp: nat_to_cref_def word_bits_def)
  apply (erule(2) valid_CNodeCapE)
  apply (simp add: word_bits_def cte_level_bits_def)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `valid_objs s` head `valid_objs` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`valid_objs s`

**agent 论证**：The conjunct `valid_objs s` never appears textually in the proof body of cnode_cap_ex_cte. The proof uses `erule(2) valid_CNodeCapE` whose two consumed hypotheses are most likely `is_cnode_cap cap` and `s \<turnstile> cap`; `valid_objs` is a decode/lookup-style implication lemma making this a prime P-slot candidate. Trial decides whether valid_CNodeCapE implicitly requires valid_objs.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(is_cnode_cap cap \<and> cte_wp_at (\<lambda>c. \<exists>m. cap = mask_cap m c) p s \<and> s \<turnstile> cap \<and> pspace_aligned s) ==> (is_cnode_cap cap \<and> cte_wp_at (\<lambda>c. \<exists>m. cap = mask_cap m c) p s \<and> s \<turnstile> cap \<and> valid_objs s \<and> pspace_aligned s)

**delivery**：named，目标：Future consumers of cnode_cap_ex_cte that do not have valid_objs in scope；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***         cap = CNodeCap r bits g\<rbrakk>
***        \<Longrightarrow> 32 - (32 - bits) = bits
*** At command "apply" (line 46 of "/tmp/tmp.JxqfWuCrOP/Tmp_98a9b2517034dd10.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchUntyped_AI:cnode_cap_ex_cte_no_valid_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
