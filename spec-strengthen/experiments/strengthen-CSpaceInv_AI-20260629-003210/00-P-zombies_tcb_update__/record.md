# record — `zombies_tcb_update'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/CSpaceInv_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=44739 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L253 之后 |
| 锚定的原 lemma | `zombies_tcb_update` (L248) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma zombies_tcb_update:
  "\<lbrakk> zombies_final s; obj_at (same_caps (TCB tcb)) t s \<rbrakk>
   \<Longrightarrow> zombies_final (s\<lparr>kheap := (kheap s)(t \<mapsto> TCB tcb)\<rparr>)"
  apply (simp add: zombies_final_def is_final_cap'_def2, elim allEI)
  apply (clarsimp simp: cte_wp_at_after_update fun_upd_def)
  done
```

## 2. 新增 lemma

```isabelle
lemma zombies_tcb_update':
    "\<lbrakk> zombies_final s \<rbrakk>
     \<Longrightarrow> zombies_final (s\<lparr>kheap := (kheap s)(t \<mapsto> TCB tcb)\<rparr>)"
    apply (simp add: zombies_final_def is_final_cap'_def2, elim allEI)
    apply (clarsimp simp: cte_wp_at_after_update fun_upd_def)
    done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `obj_at (same_caps (TCB tcb)) t s` head `obj_at` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`obj_at (same_caps (TCB tcb)) t s`

**agent 论证**：The original proof body never textually references obj_at or same_caps — the only consumer of same_caps would be the conditional simp rule cte_wp_at_after_update. For TCB objects, same_caps holds structurally (TCBs always present the same cap slots regardless of field values), so cte_wp_at_after_update may discharge without the explicit obj_at premise. Trial decides.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(zombies_final s) ==> (zombies_final s \<and> obj_at (same_caps (TCB tcb)) t s)

**delivery**：named，目标：Any consumer that previously required obj_at (same_caps (TCB tcb)) t s before calling zombies_tcb_update；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***          (s\<lparr>kheap :=
***                      \<lambda>x.
***                         if x = t then Some (TCB tcb)
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:CSpaceInv_AI:zombies_tcb_update'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
