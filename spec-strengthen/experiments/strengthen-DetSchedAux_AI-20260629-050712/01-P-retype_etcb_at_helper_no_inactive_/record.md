# record — `retype_etcb_at_helper_no_inactive`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/DetSchedAux_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=45511 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L72 之后 |
| 锚定的原 lemma | `retype_etcb_at_helper` (L58) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma retype_etcb_at_helper: "\<lbrakk>etcb_at' P t ekh; valid_etcbs_2 ekh kh; d \<noteq> apiobject_type.Untyped;
        foldr (\<lambda>p kh. kh(p \<mapsto> default_object d dev c))
         ptrs
         kh t =
        Some (TCB tcb);
        tcb_state tcb \<noteq> Inactive\<rbrakk>
       \<Longrightarrow> etcb_at' P t
           ((foldr (\<lambda>p ekh. ekh(p := default_ext d cdom))
             ptrs)
             ekh)"
  apply (induct ptrs)
  apply simp
  apply (case_tac d)
  apply (clarsimp split: if_split_asm simp: default_tcb_def default_object_def default_ext_def etcb_at'_def)+
  done
```

## 2. 新增 lemma

```isabelle
lemma retype_etcb_at_helper_no_inactive: "\<lbrakk>etcb_at' P t ekh; valid_etcbs_2 ekh kh; d \<noteq> apiobject_type.Untyped;
        foldr (\<lambda>p kh. kh(p \<mapsto> default_object d dev c))
         ptrs
         kh t =
        Some (TCB tcb)\<rbrakk>
       \<Longrightarrow> etcb_at' P t
           ((foldr (\<lambda>p ekh. ekh(p := default_ext d cdom))
             ptrs)
             ekh)"
  apply (induct ptrs)
  apply simp
  apply (case_tac d)
  apply (clarsimp split: if_split_asm simp: default_tcb_def default_object_def default_ext_def etcb_at'_def)+
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `valid_etcbs_2 ekh kh` head `valid_etcbs_2` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`valid_etcbs_2 ekh kh`

**agent 论证**：The proof of retype_etcb_at_helper (L68-72) never references tcb_state: the induction and clarsimp only inspect d and etcb_at'. The tcb_state ≠ Inactive conjunct is therefore not consumed by the proof.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(etcb_at' P t ekh ∧ valid_etcbs_2 ekh kh ∧ d ≠ Untyped ∧ foldr...t = Some(TCB tcb)) ==> (etcb_at' P t ekh ∧ valid_etcbs_2 ekh kh ∧ d ≠ Untyped ∧ foldr...t = Some(TCB tcb) ∧ tcb_state tcb ≠ Inactive)

**delivery**：named，目标：retype_region_etcb_at or any future consumer of retype_etcb_at_helper where tcb_state is not known；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                      tcb_bound_notification = None,
***                      tcb_mcpriority = minBound,
***                      tcb_arch = default_arch_tcb\<rparr>))
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:DetSchedAux_AI:retype_etcb_at_helper_no_inactive" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
