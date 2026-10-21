# record — `wellformed_arch_pspace_no_kheap`  (P-slot, IMPACT-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchInvariants_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **IMPACT-FAILED** |
| 墙钟 | baseline=76551 ms · trial=75993 ms · Δ -0.73% |
| 锚点 | 插入于 L345 之后 |
| 锚定的原 lemma | `wellformed_arch_pspace` (L344) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma wellformed_arch_pspace: "\<And>ao. \<lbrakk>arch_valid_obj ao s; kheap s = kheap s'\<rbrakk>
          \<Longrightarrow> arch_valid_obj ao s'" by simp

```

## 2. 新增 lemma

```isabelle
lemma wellformed_arch_pspace_no_kheap: "\<And>ao. arch_valid_obj ao s \<Longrightarrow> arch_valid_obj ao s'"
  by simp
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> [implication lemma — assumption-weakening] conjunct `kheap s = kheap s'` head `kheap` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`kheap s = kheap s'`

**agent 论证**：arch_valid_obj ao s unfolds to wellformed_vspace_obj ao (L326), which mentions no state component at all. The conclusion arch_valid_obj ao s' is identical in content. The kheap s = kheap s' conjunct in the original is therefore never consumed: simp closes the goal purely by unfolding arch_valid_obj_def. Dropping it yields a strictly stronger implication lemma.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(arch_valid_obj ao s) ==> (arch_valid_obj ao s \<and> kheap s = kheap s')

**delivery**：named，目标：Any consumer that currently calls wellformed_arch_pspace with a kheap-equality witness; the stronger form drops that witness entirely；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (75993ms)
```
**最终裁决**：`IMPACT-FAILED`（ledger 终态事件：`discovered`）
**impact**：verdict=`weakening`，gate_pass=False，wall gate ✓（详见 `measurement.json`）
**落地**：未落地（IMPACT-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchInvariants_AI:wellformed_arch_pspace_no_kheap" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
