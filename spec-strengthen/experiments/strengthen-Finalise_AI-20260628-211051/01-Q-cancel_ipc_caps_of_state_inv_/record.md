# record — `cancel_ipc_caps_of_state_inv`  (Q-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Finalise_AI.thy` |
| Slot / delivery | Q / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=41432 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L503 之后 |
| 锚定的原 lemma | `cancel_ipc_caps_of_state` (L482) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cancel_ipc_caps_of_state:
  "\<lbrace>\<lambda>s. (\<forall>p. cte_wp_at can_fast_finalise p s
           \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap)))
           \<and> P (caps_of_state s)\<rbrace>
     cancel_ipc t
   \<lbrace>\<lambda>rv s. P (caps_of_state s)\<rbrace>"
  apply (simp add: cancel_ipc_def reply_cancel_ipc_def
             cong: Structures_A.thread_state.case_cong)
  apply (wpsimp wp: cap_delete_one_caps_of_state)
     apply (rule_tac Q="\<lambda>_ s. (\<forall>p. cte_wp_at can_fast_finalise p s
                                \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap)))
                                \<and> P (caps_of_state s)"
                  in hoare_post_imp)
      apply (clarsimp simp: fun_upd_def[symmetric] split_paired_Ball)
     apply (simp add: cte_wp_at_caps_of_state)
     apply (wpsimp wp: hoare_vcg_all_lift hoare_convert_imp thread_set_caps_of_state_trivial
                 simp: ran_tcb_cap_cases)+
   prefer 2
   apply assumption
  apply (rule hoare_strengthen_post [OF gts_sp])
  apply (clarsimp simp: fun_upd_def[symmetric] cte_wp_at_caps_of_state)
  done
```

## 2. 新增 lemma

```isabelle
lemma cancel_ipc_caps_of_state_inv:
  "\<lbrace>\<lambda>s. (\<forall>p. cte_wp_at can_fast_finalise p s
           \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap)))
           \<and> P (caps_of_state s)\<rbrace>
     cancel_ipc t
   \<lbrace>\<lambda>rv s. (\<forall>p. cte_wp_at can_fast_finalise p s
             \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap)))
             \<and> P (caps_of_state s)\<rbrace>"
  apply (simp add: cancel_ipc_def reply_cancel_ipc_def
             cong: Structures_A.thread_state.case_cong)
  apply (wpsimp wp: cap_delete_one_caps_of_state)
     apply (rule_tac Q="\<lambda>_ s. (\<forall>p. cte_wp_at can_fast_finalise p s
                                \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap)))
                                \<and> P (caps_of_state s)"
                  in hoare_post_imp)
      apply (clarsimp simp: fun_upd_def[symmetric] split_paired_Ball)
     apply (simp add: cte_wp_at_caps_of_state)
     apply (wpsimp wp: hoare_vcg_all_lift hoare_convert_imp thread_set_caps_of_state_trivial
                 simp: ran_tcb_cap_cases)+
   prefer 2
   apply assumption
  apply (rule hoare_strengthen_post [OF gts_sp])
  apply (clarsimp simp: fun_upd_def[symmetric] cte_wp_at_caps_of_state)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`inline-redirect`，priority=low
> explicit stronger post in rule_tac Q="..." in hoare_post_imp [INTERIOR redirect — describes an inner Hoare step, not this lemma's post; verify which op it strengthens]
> proof 中路过的更强 post：`\<lambda>_ s. (\<forall>p. cte_wp_at can_fast_finalise p s
                                \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap)))
                                \<and> P (caps_of_state s)`

**agent 论证**：The proof of cancel_ipc_caps_of_state (L482-503) at L491-493 uses rule_tac Q with hoare_post_imp to maintain both conjuncts as an invariant through inner steps, then discards the first conjunct in the final post. The full invariant (both conjuncts preserved) is strictly stronger than just P (caps_of_state s) and is already proved internally; naming it avoids re-deriving the forall-part in downstream proofs.

**强化关系**（agent 自述）：((\<forall>p. cte_wp_at can_fast_finalise p s \<longrightarrow> P ((caps_of_state s) (p \<mapsto> cap.NullCap))) \<and> P (caps_of_state s)) \<Longrightarrow> P (caps_of_state s)

**delivery**：named，目标：Future consumer needing invariant that both conjuncts of cancel_ipc_caps_of_state precondition are preserved through cancel_ipc；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***        \<lbrace>?P'85 state x51 x52\<rbrace>
***        blocked_cancel_ipc (BlockedOnSend x51 x52) t 
***        \<lbrace>\<lambda>rv s. P (caps_of_state s)\<rbrace>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "Q:Finalise_AI:cancel_ipc_caps_of_state_inv" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
