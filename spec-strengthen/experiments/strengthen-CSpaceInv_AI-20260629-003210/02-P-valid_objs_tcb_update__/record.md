# record — `valid_objs_tcb_update'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/CSpaceInv_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=44739 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L216 之后 |
| 锚定的原 lemma | `valid_objs_tcb_update` (L201) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma valid_objs_tcb_update:
  "\<lbrakk>tcb_at t s; valid_tcb t tcb s; valid_objs s \<rbrakk>
  \<Longrightarrow> valid_objs (s\<lparr>kheap := (kheap s)(t \<mapsto> TCB tcb)\<rparr>)"
  apply (clarsimp simp: valid_objs_def dom_def
                 elim!: obj_atE)
  apply (intro conjI impI)
   apply (rule valid_obj_same_type)
      apply (simp add: valid_obj_def)+
   apply (clarsimp simp: a_type_def is_tcb)
  apply clarsimp
  apply (rule valid_obj_same_type)
     apply (drule_tac x=ptr in spec, simp)
    apply (simp add: valid_obj_def)
   apply assumption
  apply (clarsimp simp add: a_type_def is_tcb)
  done
```

## 2. 新增 lemma

```isabelle
lemma valid_objs_tcb_update':
    "\<lbrakk>valid_tcb t tcb s; valid_objs s \<rbrakk>
    \<Longrightarrow> valid_objs (s\<lparr>kheap := (kheap s)(t \<mapsto> TCB tcb)\<rparr>)"
    apply (clarsimp simp: valid_objs_def dom_def
                   elim!: obj_atE)
    apply (intro conjI impI)
     apply (rule valid_obj_same_type)
        apply (simp add: valid_obj_def)+
     apply (clarsimp simp: a_type_def is_tcb)
    apply clarsimp
    apply (rule valid_obj_same_type)
       apply (drule_tac x=ptr in spec, simp)
      apply (simp add: valid_obj_def)
     apply assumption
    apply (clarsimp simp add: a_type_def is_tcb)
    done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `tcb_at t s` head `tcb_at` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`tcb_at t s`

**agent 论证**：The scanner shows tcb_at head never appears in the proof body despite the differential signal. The clarsimp with elim!: obj_atE eliminates obj_at hypotheses, but valid_tcb t tcb s already implies the new object is a TCB. If valid_obj_same_type can prove the a_type equality using only the valid_tcb hypothesis (which carries TCB-ness implicitly), then tcb_at may be redundant. Borderline — trial decides.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(valid_tcb t tcb s \<and> valid_objs s) ==> (tcb_at t s \<and> valid_tcb t tcb s \<and> valid_objs s)

**delivery**：named，目标：Any consumer that previously required tcb_at t s before calling valid_objs_tcb_update；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***            (\<exists>obj.
***                kheap s ptr = Some obj \<and> valid_obj ptr obj s);
***         ptr = t\<rbrakk>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:CSpaceInv_AI:valid_objs_tcb_update'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
