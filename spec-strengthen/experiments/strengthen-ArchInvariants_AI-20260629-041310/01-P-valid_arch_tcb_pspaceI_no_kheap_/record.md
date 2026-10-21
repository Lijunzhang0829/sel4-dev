# record — `valid_arch_tcb_pspaceI_no_kheap`  (P-slot, IMPACT-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchInvariants_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **IMPACT-FAILED** |
| 墙钟 | baseline=76551 ms · trial=76160 ms · Δ -0.51% |
| 锚点 | 插入于 L2397 之后 |
| 锚定的原 lemma | `valid_arch_tcb_pspaceI` (L2394) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma valid_arch_tcb_pspaceI:
  "\<lbrakk> valid_arch_tcb t s; kheap s = kheap s' \<rbrakk> \<Longrightarrow> valid_arch_tcb t s'"
  unfolding valid_arch_tcb_def obj_at_def by (simp)

```

## 2. 新增 lemma

```isabelle
lemma valid_arch_tcb_pspaceI_no_kheap:
  "valid_arch_tcb t s \<Longrightarrow> valid_arch_tcb t s'"
  by (simp add: valid_arch_tcb_def)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `kheap s = kheap s'` head `kheap` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`kheap s = kheap s'`

**agent 论证**：valid_arch_tcb is defined as \<lambda>a. \<top> (L251), so valid_arch_tcb t s = True for every t and s. The premise valid_arch_tcb t s and the conclusion valid_arch_tcb t s' are both trivially True. The conjunct kheap s = kheap s' is therefore structurally irrelevant; simp add: valid_arch_tcb_def closes the stronger statement directly.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(valid_arch_tcb t s) ==> (valid_arch_tcb t s \<and> kheap s = kheap s')

**delivery**：named，目标：Any caller that passes a kheap-equality proof to valid_arch_tcb_pspaceI; the stronger form needs only the valid_arch_tcb hypothesis；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (76160ms)
```
**最终裁决**：`IMPACT-FAILED`（ledger 终态事件：`discovered`）
**impact**：verdict=`noop`，gate_pass=False，wall gate ✓（详见 `measurement.json`）
**落地**：未落地（IMPACT-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchInvariants_AI:valid_arch_tcb_pspaceI_no_kheap" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
