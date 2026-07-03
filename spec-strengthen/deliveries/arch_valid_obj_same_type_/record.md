# record — `arch_valid_obj_same_type'`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchKHeap_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=43934 ms · trial=41505 ms · Δ -5.53% |
| 锚点 | 插入于 L834 之后 |
| 锚定的原 lemma | `arch_valid_obj_same_type` (L830) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma arch_valid_obj_same_type:
  "\<lbrakk> arch_valid_obj ao s; kheap s p = Some ko; a_type k = a_type ko \<rbrakk>
   \<Longrightarrow> arch_valid_obj ao (s\<lparr>kheap := (kheap s)(p \<mapsto> k)\<rparr>)"
  by (induction ao rule: arch_kernel_obj.induct;
```

## 2. 新增 lemma

```isabelle
lemma arch_valid_obj_same_type':
  "\<lbrakk> arch_valid_obj ao s; kheap s p = Some ko \<rbrakk>
   \<Longrightarrow> arch_valid_obj ao (s\<lparr>kheap := (kheap s)(p \<mapsto> k)\<rparr>)"
  by (induction ao rule: arch_kernel_obj.induct;
         clarsimp simp: typ_at_same_type)
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> [implication lemma — assumption-weakening] conjunct `a_type k = a_type ko` head `a_type` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`a_type k = a_type ko`

**agent 论证**：The scanner flags 'NO conjunct visibly consumed' — the strongest signal. The proof uses only `induction ao` and `clarsimp simp: typ_at_same_type`. For all ARM arch_kernel_obj constructors (ASIDPool, PageTable, PageDirectory, DataPage), arch_valid_obj unfolds to conditions on entries that are independent of the type of the replacement object k. If typ_at_same_type is used purely as a rewrite to equate typ_at before/after the kheap update, it may fire without needing a_type equality as a hypothesis (relying instead on the kheap membership fact). The trial will confirm.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(arch_valid_obj ao s \<and> kheap s p = Some ko) \<Longrightarrow> (arch_valid_obj ao s \<and> kheap s p = Some ko \<and> a_type k = a_type ko)

**delivery**：named，目标：Future callers of arch_valid_obj_same_type that do not have a_type equality available；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (41505ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchKHeap_AI:arch_valid_obj_same_type'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
