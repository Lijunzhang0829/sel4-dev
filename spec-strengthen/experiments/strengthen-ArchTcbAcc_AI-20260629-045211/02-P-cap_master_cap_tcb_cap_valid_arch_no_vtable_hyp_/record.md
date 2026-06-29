# record — `cap_master_cap_tcb_cap_valid_arch_no_vtable_hyp`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchTcbAcc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=43079 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L55 之后 |
| 锚定的原 lemma | `cap_master_cap_tcb_cap_valid_arch` (L45) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cap_master_cap_tcb_cap_valid_arch:
  "\<lbrakk> cap_master_cap c = cap_master_cap c'; is_arch_cap c' ;
     is_valid_vtable_root c \<Longrightarrow> is_valid_vtable_root c' ; tcb_cap_valid c p s \<rbrakk> \<Longrightarrow>
   tcb_cap_valid c' p s"
  (* slow: 5 to 10s *)
  by (auto simp: cap_master_cap_def tcb_cap_valid_def tcb_cap_cases_def
```

## 2. 新增 lemma

```isabelle
lemma cap_master_cap_tcb_cap_valid_arch_no_vtable_hyp:
  "\<lbrakk> cap_master_cap c = cap_master_cap c'; is_arch_cap c' ; tcb_cap_valid c p s \<rbrakk> \<Longrightarrow>
   tcb_cap_valid c' p s"
  (* slow: 5 to 10s *)
  by (auto simp: cap_master_cap_def tcb_cap_valid_def tcb_cap_cases_def
                 valid_ipc_buffer_cap_def  is_cap_simps
                 is_nondevice_page_cap_simps is_nondevice_page_cap_arch_def
           elim: pred_tcb_weakenE
          split: option.splits cap.splits arch_cap.splits
                 Structures_A.thread_state.splits)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `is_arch_cap c'` head `is_arch_cap` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`is_arch_cap c'`

**agent 论证**：is_valid_vtable_root never appears textually in the proof body; the auto tactic with arch_cap.splits and cap_master_cap_def likely resolves the vtable slot via structural case analysis without needing the explicit transfer hypothesis.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(cap_master_cap c = cap_master_cap c' and is_arch_cap c' and tcb_cap_valid c p s) <= (cap_master_cap c = cap_master_cap c' and is_arch_cap c' and (is_valid_vtable_root c => is_valid_vtable_root c') and tcb_cap_valid c p s)

**delivery**：named，目标：Future callers that cannot supply the vtable-root transfer hypothesis；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***         c = ArchObjectCap (ASIDPoolCap x11 x12b);
***         st_tcb_at
***          (\<lambda>st.
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchTcbAcc_AI:cap_master_cap_tcb_cap_valid_arch_no_vtable_hyp" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
