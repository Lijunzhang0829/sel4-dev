# record — `decode_unbind_notification_wf_strong`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Tcb_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=47149 ms · trial=40869 ms · Δ -13.32% |
| 锚点 | 插入于 L1215 之后 |
| 锚定的原 lemma | `decode_unbind_notification_wf` (L1207) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma decode_unbind_notification_wf:
  "\<lbrace>invs and tcb_at t and ex_nonz_cap_to t\<rbrace>
     decode_unbind_notification (cap.ThreadCap t)
   \<lbrace>tcb_inv_wf\<rbrace>,-"
  apply (simp add: decode_unbind_notification_def)
  apply (rule hoare_pre)
   apply (wp gbn_wp | wpc)+
  apply clarsimp
  done
```

## 2. 新增 lemma

```isabelle
lemma decode_unbind_notification_wf_strong:
  "\<lbrace>tcb_at t and ex_nonz_cap_to t\<rbrace>
     decode_unbind_notification (cap.ThreadCap t)
   \<lbrace>tcb_inv_wf\<rbrace>,-"
  apply (simp add: decode_unbind_notification_def)
  apply (rule hoare_pre)
   apply (wp gbn_wp | wpc)+
  apply clarsimp
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `invs` head `invs` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; wp-chain implicit use is the trial's job
> 目标前提：`invs`

**agent 论证**：tcb_inv_wf (NotificationControl t None) unfolds to (tcb_at t and ex_nonz_cap_to t), so the only facts the proof needs are those two conjuncts. The wp chain uses only gbn_wp (a pure read-wp rule with no invs dependency) and wpc for case splits; the final clarsimp discharges the residual goal purely from the tcb_at t and ex_nonz_cap_to t hypotheses. invs never appears in the proof body and is not reachable by any implicit wp-chain dependency here. Dropping it yields a strictly weaker precondition with the original proof verbatim.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(invs and tcb_at t and ex_nonz_cap_to t) ==> (tcb_at t and ex_nonz_cap_to t) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `invs`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Any future caller of decode_unbind_notification_wf that cannot supply invs (e.g., a context where only the thread's cap witness is available) can use decode_unbind_notification_wf_strong directly via `rule` or `wp`.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (40869ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:Tcb_AI:decode_unbind_notification_wf_strong" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
