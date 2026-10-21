# record — `complete_signal_invs_no_tcb_at`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Ipc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=120624 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L2701 之后 |
| 锚定的原 lemma | `complete_signal_invs` (L2684) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma complete_signal_invs:
  "\<lbrace>invs and tcb_at tcb\<rbrace>
     complete_signal ntfnptr tcb
   \<lbrace>\<lambda>_. invs\<rbrace>"
  apply (simp add: complete_signal_def)
  apply (rule bind_wp[OF _ get_simple_ko_sp])
  apply (rule hoare_pre)
   apply (wp set_ntfn_minor_invs | wpc | simp)+
   apply (rule_tac Q="\<lambda>_ s. (state_refs_of s ntfnptr = ntfn_bound_refs (ntfn_bound_tcb ntfn))
                      \<and> (\<exists>T. typ_at T ntfnptr s) \<and> valid_ntfn (ntfn_set_obj ntfn IdleNtfn) s
                      \<and> ((\<exists>y. ntfn_bound_tcb ntfn = Some y) \<longrightarrow> ex_nonz_cap_to ntfnptr s)"
                      in hoare_strengthen_post)
    apply (wp hoare_vcg_all_lift hoare_weak_lift_imp hoare_vcg_ex_lift | wpc
         | simp add: live_def valid_ntfn_def valid_bound_tcb_def split: option.splits)+
    apply ((clarsimp simp: obj_at_def state_refs_of_def)+)[2]
  apply (rule_tac obj_at_valid_objsE[OF _ invs_valid_objs]; clarsimp)
    apply assumption+
  by (fastforce simp: ko_at_state_refs_ofD valid_ntfn_def valid_obj_def obj_at_def is_ntfn live_def elim: if_live_then_nonz_capD[OF invs_iflive])
```

## 2. 新增 lemma

```isabelle
lemma complete_signal_invs_no_tcb_at:
  "\<lbrace>invs\<rbrace>
     complete_signal ntfnptr tcb
   \<lbrace>\<lambda>_. invs\<rbrace>"
  by (wpsimp simp: complete_signal_def invs_def valid_state_def valid_pspace_def
            wp: get_simple_ko_wp set_simple_ko_valid_objs set_simple_ko_refs_of
                hoare_vcg_all_lift valid_irq_node_typ set_ntfn_minor_invs)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `tcb_at tcb` head `tcb_at` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `complete_signal` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`tcb_at tcb`

**agent 论证**：complete_signal only reads and updates the notification object; tcb_at is never needed because any assertion on a missing TCB makes the triple vacuously true on the failure path. Anchor corrected to line 2701 (final line of complete_signal_invs) and backslash escaping fixed.

**强化关系**（机械验证未通过：post differs from the hinted lemma — not a pure premise-weakening；以下为 agent 自述）：(invs) ==> (invs and tcb_at tcb)

**delivery**：named，目标：Any caller of complete_signal_invs that does not independently hold tcb_at tcb can switch to this variant; handle_recv and receive_ipc proofs are likely consumers；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L2687）→ trial 失败：
  ```
  FAILED (129825ms)
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L2701（与初稿不同）
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (129825ms)
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:Ipc_AI:complete_signal_invs_no_tcb_at" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
