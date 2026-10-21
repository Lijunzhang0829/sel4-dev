# record — `vs_cap_ref_master_no_base`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchCNodeInv_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=47504 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L296 之后 |
| 锚定的原 lemma | `vs_cap_ref_master` (L286) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma vs_cap_ref_master [CNodeInv_AI_assms]:
  "\<lbrakk> cap_master_cap cap = cap_master_cap cap';
           cap_asid cap = cap_asid cap';
           cap_asid_base cap = cap_asid_base cap';
           cap_vptr cap = cap_vptr cap' \<rbrakk>
        \<Longrightarrow> vs_cap_ref cap = vs_cap_ref cap'"
  apply (rule ccontr)
  apply (clarsimp simp: vs_cap_ref_def cap_master_cap_def
                 split: cap.split_asm)
  apply (clarsimp simp: cap_asid_def split: arch_cap.split_asm option.split_asm)
  done
```

## 2. 新增 lemma

```isabelle
lemma vs_cap_ref_master_no_base [CNodeInv_AI_assms]:
  "\<lbrakk> cap_master_cap cap = cap_master_cap cap';
           cap_asid cap = cap_asid cap';
           cap_vptr cap = cap_vptr cap' \<rbrakk>
       \<Longrightarrow> vs_cap_ref cap = vs_cap_ref cap'"
  apply (rule ccontr)
  apply (clarsimp simp: vs_cap_ref_def cap_master_cap_def
                 split: cap.split_asm)
  apply (clarsimp simp: cap_asid_def split: arch_cap.split_asm option.split_asm)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `cap_asid_base cap = cap_asid_base cap'` head `cap_asid_base` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`cap_asid_base cap = cap_asid_base cap'`

**agent 论证**：The proof of vs_cap_ref_master only unfolds vs_cap_ref_def, cap_master_cap_def, and cap_asid_def. The term cap_asid_base never appears in any simp set or split used by the proof. The proof reaches a contradiction purely from cap_master_cap and cap_asid equality, making cap_asid_base = cap_asid_base' superfluous.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(cap_master_cap cap = cap_master_cap cap' \<and> cap_asid cap = cap_asid cap' \<and> cap_vptr cap = cap_vptr cap') ==> (cap_master_cap cap = cap_master_cap cap' \<and> cap_asid cap = cap_asid cap' \<and> cap_asid_base cap = cap_asid_base cap' \<and> cap_vptr cap = cap_vptr cap')

**delivery**：named，目标：weak_derived_vs_cap_ref and any consumer of vs_cap_ref_master that already has cap_master_cap and cap_asid equal but not necessarily cap_asid_base equal.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***         cap' = ArchObjectCap (ASIDPoolCap x11 x12b)\<rbrakk>
***        \<Longrightarrow> False
*** At command "done" (line 307 of "/tmp/tmp.TLRoJiya2O/Tmp_5902eb1fb519b230.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchCNodeInv_AI:vs_cap_ref_master_no_base" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
