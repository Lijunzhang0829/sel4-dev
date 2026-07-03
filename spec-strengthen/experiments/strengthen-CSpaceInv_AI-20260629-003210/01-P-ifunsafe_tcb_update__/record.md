# record — `ifunsafe_tcb_update'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/CSpaceInv_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=44739 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L245 之后 |
| 锚定的原 lemma | `ifunsafe_tcb_update` (L237) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma ifunsafe_tcb_update:
  "\<lbrakk> if_unsafe_then_cap s; obj_at (same_caps (TCB tcb)) t s \<rbrakk>
  \<Longrightarrow> if_unsafe_then_cap (s\<lparr>kheap := (kheap s)(t \<mapsto> TCB tcb)\<rparr>)"
  apply (simp add: if_unsafe_then_cap_def, elim allEI)
  apply (clarsimp dest!: caps_of_state_cteD
                   simp: cte_wp_at_after_update fun_upd_def)
  apply (clarsimp simp: cte_wp_at_caps_of_state
                        ex_cte_cap_to_after_update)
  done
```

## 2. 新增 lemma

```isabelle
lemma ifunsafe_tcb_update':
    "\<lbrakk> if_unsafe_then_cap s \<rbrakk>
    \<Longrightarrow> if_unsafe_then_cap (s\<lparr>kheap := (kheap s)(t \<mapsto> TCB tcb)\<rparr>)"
    apply (simp add: if_unsafe_then_cap_def, elim allEI)
    apply (clarsimp dest!: caps_of_state_cteD
                     simp: cte_wp_at_after_update fun_upd_def)
    apply (clarsimp simp: cte_wp_at_caps_of_state
                          ex_cte_cap_to_after_update)
    done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `obj_at (same_caps (TCB tcb)) t s` head `obj_at` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`obj_at (same_caps (TCB tcb)) t s`

**agent 论证**：The original proof body never textually mentions obj_at or same_caps. The same_caps premise would only be needed if cte_wp_at_after_update is conditional on it. For TCB heap updates, if cte_wp_at_after_update fires unconditionally (or discharges same_caps automatically from TCB structure), the premise is droppable. Trial decides.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(if_unsafe_then_cap s) ==> (if_unsafe_then_cap s \<and> obj_at (same_caps (TCB tcb)) t s)

**delivery**：named，目标：Any consumer that previously required obj_at (same_caps (TCB tcb)) t s before calling ifunsafe_tcb_update；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
*** goal (1 subgoal):
***  1. \<And>a b cap.
***        \<lbrakk>caps_of_state s (a, b) = Some cap \<longrightarrow>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:CSpaceInv_AI:ifunsafe_tcb_update'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
