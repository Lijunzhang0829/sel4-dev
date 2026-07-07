# record — `ex_nonz_tcb_cte_caps_no_tcb`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchCSpace_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=42536 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L439 之后 |
| 锚定的原 lemma | `ex_nonz_tcb_cte_caps` (L427) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma ex_nonz_tcb_cte_caps [CSpace_AI_assms]:
  "\<lbrakk>ex_nonz_cap_to t s; tcb_at t s; valid_objs s; ref \<in> dom tcb_cap_cases\<rbrakk>
   \<Longrightarrow> ex_cte_cap_wp_to (appropriate_cte_cap cp) (t, ref) s"
  apply (clarsimp simp: ex_nonz_cap_to_def ex_cte_cap_wp_to_def
                        cte_wp_at_caps_of_state)
  apply (subgoal_tac "s \<turnstile> cap")
   apply (rule_tac x=a in exI, rule_tac x=ba in exI)
   apply (clarsimp simp: valid_cap_def obj_at_def
                         is_obj_defs dom_def
                         appropriate_cte_cap_def
                  split: cap.splits arch_cap.split_asm if_splits)
  apply (clarsimp simp: caps_of_state_valid_cap)
  done
```

## 2. 新增 lemma

```isabelle
lemma ex_nonz_tcb_cte_caps_no_tcb [CSpace_AI_assms]:
  "\<lbrakk>ex_nonz_cap_to t s; valid_objs s; ref \<in> dom tcb_cap_cases\<rbrakk>
   \<Longrightarrow> ex_cte_cap_wp_to (appropriate_cte_cap cp) (t, ref) s"
  apply (clarsimp simp: ex_nonz_cap_to_def ex_cte_cap_wp_to_def
                        cte_wp_at_caps_of_state)
  apply (subgoal_tac "s \<turnstile> cap")
   apply (rule_tac x=a in exI, rule_tac x=ba in exI)
   apply (clarsimp simp: valid_cap_def obj_at_def
                         is_obj_defs dom_def
                         appropriate_cte_cap_def
                  split: cap.splits arch_cap.split_asm if_splits)
  apply (clarsimp simp: caps_of_state_valid_cap)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `tcb_at t s` head `tcb_at` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`tcb_at t s`

**agent 论证**：The proof body of ex_nonz_tcb_cte_caps never mentions tcb_at: it derives 's ⊢ cap' purely via caps_of_state_valid_cap (which needs valid_objs, not tcb_at) and then unfolds valid_cap_def/obj_at_def. The tcb_at conjunct is never referenced by name or via a rule that would pull it in. Dropping it yields a strictly stronger implication lemma.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(ex_nonz_cap_to t s \<and> valid_objs s \<and> ref \<in> dom tcb_cap_cases) ==> (ex_nonz_cap_to t s \<and> tcb_at t s \<and> valid_objs s \<and> ref \<in> dom tcb_cap_cases)

**delivery**：named，目标：Future callers of ex_nonz_tcb_cte_caps that lack tcb_at evidence; e.g. sites in CSpace_AI that construct ex_cte_cap_wp_to from nonz caps without a separate tcb_at witness.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                              cp \<noteq> CNodeCap x61 x62 x63) \<and>
***                          (\<forall>x7. cp \<noteq> ThreadCap x7) \<and>
***                          cp \<noteq> DomainCap \<and>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchCSpace_AI:ex_nonz_tcb_cte_caps_no_tcb" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
