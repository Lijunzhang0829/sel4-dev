# record — `invoke_cnode_valid_pdpt_objs_min`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchVSpaceEntries_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=55083 ms · trial=55256 ms · Δ 0.31% |
| 锚点 | 插入于 L614 之后 |
| 锚定的原 lemma | `invoke_cnode_valid_pdpt_objs` (L609) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma invoke_cnode_valid_pdpt_objs[wp]:
  "\<lbrace>valid_pdpt_objs and invs and valid_cnode_inv i\<rbrace> invoke_cnode i \<lbrace>\<lambda>rv. valid_pdpt_objs\<rbrace>"
  apply (simp add: invoke_cnode_def)
  apply (rule hoare_pre)
   apply (wp get_cap_wp | wpc | simp split del: if_split)+
  done
```

## 2. 新增 lemma

```isabelle
lemma invoke_cnode_valid_pdpt_objs_min:
  "\<lbrace>valid_pdpt_objs\<rbrace> invoke_cnode i \<lbrace>\<lambda>rv. valid_pdpt_objs\<rbrace>"
  apply (simp add: invoke_cnode_def)
  apply (rule hoare_pre)
   apply (wp get_cap_wp | wpc | simp split del: if_split)+
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `invs` head `invs` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `invoke_cnode` is write (write/modify → premise usually load-bearing, demoted); wp-chain implicit use is the trial's job
> 目标前提：`invs`

**agent 论证**：An independent drop of valid_cnode_inv i from invoke_cnode_valid_pdpt_objs. Since the [wp] lemmas for all component operations (cap_insert, cap_swap, empty_slot, cap_revoke, etc. — L92, L418, L604, L607) carry only valid_pdpt_objs as their precondition for this postcondition, the wp chain needs nothing from valid_cnode_inv. Neither invs nor valid_cnode_inv is textually consumed; the proof body is verbatim delegation. If this verifies it subsumes both hint 10 and hint 11 in a single, maximally-strong proposal.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_pdpt_objs and invs and valid_cnode_inv i) ==> (valid_pdpt_objs) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `invs`; `valid_cnode_inv i`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：A maximally-weak precondition for invoke_cnode valid_pdpt_objs preservation; useful in contexts where no cnode validity is available；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (55256ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchVSpaceEntries_AI:invoke_cnode_valid_pdpt_objs_min" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
