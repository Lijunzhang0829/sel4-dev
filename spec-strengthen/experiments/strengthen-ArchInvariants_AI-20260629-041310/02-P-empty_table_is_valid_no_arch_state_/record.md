# record — `empty_table_is_valid_no_arch_state`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchInvariants_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=76551 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L1943 之后 |
| 锚定的原 lemma | `empty_table_is_valid` (L1940) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma empty_table_is_valid:
  "\<lbrakk>empty_table (set (arm_global_pts (arch_state s))) (ArchObj ao);
    valid_arch_state s\<rbrakk>
   \<Longrightarrow> valid_vspace_obj ao s"
  by (cases ao, simp_all add: empty_table_def)
```

## 2. 新增 lemma

```isabelle
lemma empty_table_is_valid_no_arch_state:
  "empty_table (set (arm_global_pts (arch_state s))) (ArchObj ao) \<Longrightarrow>
   valid_vspace_obj ao s"
  by (cases ao; simp add: empty_table_def)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `valid_arch_state s` head `valid_arch_state` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`valid_arch_state s`

**agent 论证**：For a PageDirectory, empty_table forces every non-kernel-slot pde to be InvalidPDE; valid_vspace_obj (PageDirectory pd) requires valid_pde on those entries, which holds trivially for InvalidPDE (\<top>). For a PageTable, empty_table forces every pte to InvalidPTE; valid_vspace_obj (PageTable pt) holds trivially. DataPage and ASIDPool are ruled out by empty_table_arch False. In none of these cases is valid_arch_state s consulted, so it can be dropped.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(empty_table (set (arm_global_pts (arch_state s))) (ArchObj ao)) ==> (empty_table (set (arm_global_pts (arch_state s))) (ArchObj ao) \<and> valid_arch_state s)

**delivery**：named，目标：Callers of empty_table_is_valid that currently supply valid_arch_state s; the stronger form omits that witness；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (70860ms)
*** Bad context for command "lemma" (line 1945 of "/tmp/tmp.pXrmbY4A0M/Tmp_32303f2997e4c030.thy")
*** At command "lemma" (line 1945 of "/tmp/tmp.pXrmbY4A0M/Tmp_32303f2997e4c030.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchInvariants_AI:empty_table_is_valid_no_arch_state" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
