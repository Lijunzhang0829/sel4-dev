# record — `invoke_untyped_valid_sched'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/DetSchedAux_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=45511 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L326 之后 |
| 锚定的原 lemma | `invoke_untyped_valid_sched` (L312) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma invoke_untyped_valid_sched:
  "\<lbrace>invs and valid_untyped_inv ui and ct_active and valid_sched and valid_idle \<rbrace>
     invoke_untyped ui
   \<lbrace> \<lambda>_ . valid_sched \<rbrace>"
  apply (rule hoare_pre)
   apply (rule_tac I="invs and valid_untyped_inv ui and ct_active"
                in valid_sched_tcb_state_preservation)
          apply (wpsimp wp: invoke_untyped_st_tcb_at invoke_untyped_etcb_at)+
     apply (rule hoare_strengthen_postE, rule invoke_untyp_invs; simp add: invs_valid_idle)
    apply simp
   apply (rule_tac f="\<lambda>s. P (scheduler_action s)" in hoare_lift_Pf)
    apply (rule_tac f="\<lambda>s. x (ready_queues s)" in hoare_lift_Pf)
     apply wp+
  apply simp+
  done
```

## 2. 新增 lemma

```isabelle
lemma invoke_untyped_valid_sched':
  "\<lbrace>invs and valid_untyped_inv ui and ct_active and valid_sched\<rbrace>
     invoke_untyped ui
   \<lbrace>\<lambda>_. valid_sched\<rbrace>"
  apply (rule hoare_pre)
   apply (rule_tac I="invs and valid_untyped_inv ui and ct_active"
                in valid_sched_tcb_state_preservation)
          apply (wpsimp wp: invoke_untyped_st_tcb_at invoke_untyped_etcb_at)+
     apply (rule hoare_strengthen_postE, rule invoke_untyp_invs; simp add: invs_valid_idle)
    apply simp
   apply (rule_tac f="\<lambda>s. P (scheduler_action s)" in hoare_lift_Pf)
    apply (rule_tac f="\<lambda>s. x (ready_queues s)" in hoare_lift_Pf)
     apply wp+
  apply simp+
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`named-redirect`，priority=low
> redirects to existing named rule `invoke_untyp_invs` — slot already filled

**agent 论证**：The proof at L320 already uses `invs_valid_idle` to derive valid_idle from invs inside valid_sched_tcb_state_preservation's assumption discharge. Therefore the outer precondition `valid_idle` is redundant: it is already entailed by `invs` which is a required conjunct. Dropping it gives a strictly stronger Hoare triple.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(invs and valid_untyped_inv ui and ct_active and valid_sched and valid_idle) ==> (invs and valid_untyped_inv ui and ct_active and valid_sched) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `valid_idle`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：callers of invoke_untyped_valid_sched that already hold invs (which subsumes valid_idle via invs_valid_idle)；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***        ct_active s \<and> valid_sched s \<Longrightarrow>
***        valid_idle s
*** At command "done" (line 342 of "/tmp/tmp.avaJfr3r9S/Tmp_d69250bdf541c10f.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:DetSchedAux_AI:invoke_untyped_valid_sched'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
