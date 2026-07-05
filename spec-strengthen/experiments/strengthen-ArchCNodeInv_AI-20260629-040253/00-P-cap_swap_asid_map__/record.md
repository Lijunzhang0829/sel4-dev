# record — `cap_swap_asid_map'`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchCNodeInv_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=47504 ms · trial=47866 ms · Δ 0.76% |
| 锚点 | 插入于 L409 之后 |
| 锚定的原 lemma | `cap_swap_asid_map` (L400) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cap_swap_asid_map[wp, CNodeInv_AI_assms]:
  "\<lbrace>valid_asid_map and
    cte_wp_at (weak_derived c) a and
    cte_wp_at (weak_derived c') b\<rbrace>
     cap_swap c a c' b \<lbrace>\<lambda>rv. valid_asid_map\<rbrace>"
  apply (simp add: cap_swap_def set_cdt_def valid_asid_map_def vspace_at_asid_def)
  apply (rule hoare_pre)
   apply (wp set_cap.vs_lookup|simp
          |rule hoare_lift_Pf [where f=arch_state])+
  done
```

## 2. 新增 lemma

```isabelle
lemma cap_swap_asid_map'[CNodeInv_AI_assms]:
  "\<lbrace>valid_asid_map\<rbrace>
     cap_swap c a c' b \<lbrace>\<lambda>rv. valid_asid_map\<rbrace>"
  apply (simp add: cap_swap_def set_cdt_def valid_asid_map_def vspace_at_asid_def)
  apply (rule hoare_pre)
   apply (wp set_cap.vs_lookup|simp
          |rule hoare_lift_Pf [where f=arch_state])+
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `cte_wp_at (weak_derived c) a` head `cte_wp_at` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `cap_swap` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`cte_wp_at (weak_derived c) a`

**agent 论证**：The proof of cap_swap_asid_map never mentions cte_wp_at, weak_derived, or either slot name: it unfolds cap_swap_def/set_cdt_def and then discharges everything via set_cap.vs_lookup and hoare_lift_Pf on arch_state. Both cte_wp_at conjuncts are therefore inert to the proof. Dropping them both in one step yields the strongest additive strengthening.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_asid_map and
    cte_wp_at (weak_derived c) a and
    cte_wp_at (weak_derived c') b) ==> (valid_asid_map) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `cte_wp_at (weak_derived c) a`; `cte_wp_at (weak_derived c') b`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future consumers of cap_swap_asid_map that currently carry the cte_wp_at preconditions can be simplified to call cap_swap_asid_map' instead.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (47866ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchCNodeInv_AI:cap_swap_asid_map'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
