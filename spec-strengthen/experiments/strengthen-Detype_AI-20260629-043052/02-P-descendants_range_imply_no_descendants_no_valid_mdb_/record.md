# record — `descendants_range_imply_no_descendants_no_valid_mdb`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Detype_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=41356 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L343 之后 |
| 锚定的原 lemma | `descendants_range_imply_no_descendants` (L328) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma descendants_range_imply_no_descendants:
  "\<lbrakk>descendants_range cap p s;descendants_inc (cdt s) (caps_of_state s);
  is_untyped_cap cap; caps_of_state s p = Some cap;valid_objs s;valid_mdb s\<rbrakk>
  \<Longrightarrow> descendants_of p (cdt s)= {}"
  apply (simp add:descendants_range_def is_cap_simps descendants_inc_def del:split_paired_All)
  apply (elim exE)
  apply (rule equals0I)
  apply (drule(1) bspec)
  apply (drule spec)+
  apply (erule(1) impE)
  apply (drule(1) descendants_of_cte_at)
  apply (clarsimp simp:cte_wp_at_caps_of_state simp del:split_paired_All)
  apply (drule(1) physical_valid_cap_not_empty_range[OF caps_of_state_valid_cap,rotated])
   apply simp
  apply auto
  done
```

## 2. 新增 lemma

```isabelle
lemma descendants_range_imply_no_descendants_no_valid_mdb:
    "\<lbrakk>descendants_range cap p s; descendants_inc (cdt s) (caps_of_state s);
    is_untyped_cap cap; caps_of_state s p = Some cap; valid_objs s\<rbrakk>
    \<Longrightarrow> descendants_of p (cdt s) = {}"
    apply (simp add:descendants_range_def is_cap_simps descendants_inc_def del:split_paired_All)
    apply (elim exE)
    apply (rule equals0I)
    apply (drule(1) bspec)
    apply (drule spec)+
    apply (erule(1) impE)
    apply (drule(1) descendants_of_cte_at)
    apply (clarsimp simp:cte_wp_at_caps_of_state simp del:split_paired_All)
    apply (drule(1) physical_valid_cap_not_empty_range[OF caps_of_state_valid_cap,rotated])
     apply simp
    apply auto
    done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `is_untyped_cap cap` head `is_untyped_cap` never appears in the proof body (prefix match incl. _def/_E forms); 3 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`is_untyped_cap cap`

**agent 论证**：The head `valid_mdb` never appears verbatim. It feeds `descendants_of_cte_at` implicitly; if that lemma draws `valid_mdb` from `descendants_inc` or the mdb structure alone, the premise may be droppable. Trial will decide.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(descendants_range cap p s \<and> descendants_inc (cdt s) (caps_of_state s) \<and> is_untyped_cap cap \<and> caps_of_state s p = Some cap \<and> valid_objs s) \<Longrightarrow> (descendants_range cap p s \<and> descendants_inc (cdt s) (caps_of_state s) \<and> is_untyped_cap cap \<and> caps_of_state s p = Some cap \<and> valid_objs s \<and> valid_mdb s)

**delivery**：named，目标：Future callers that have valid_mdb only through descendants_inc context；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***         cap = UntypedCap dev r bits f; y \<in> descendants_of p (cdt s);
***         cte_wp_at (\<lambda>c. cap_range c \<inter> untyped_range cap = {})
***          y s;
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:Detype_AI:descendants_range_imply_no_descendants_no_valid_mdb" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
