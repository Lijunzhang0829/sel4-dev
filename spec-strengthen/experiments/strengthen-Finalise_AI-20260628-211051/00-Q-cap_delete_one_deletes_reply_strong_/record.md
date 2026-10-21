# record — `cap_delete_one_deletes_reply_strong`  (Q-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Finalise_AI.thy` |
| Slot / delivery | Q / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=41432 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L948 之后 |
| 锚定的原 lemma | `cap_delete_one_deletes_reply` (L929) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cap_delete_one_deletes_reply:
  "\<lbrace>cte_wp_at (is_reply_cap_to t) slot and valid_reply_caps\<rbrace>
    cap_delete_one slot
   \<lbrace>\<lambda>rv s. \<not> has_reply_cap t s\<rbrace>"
  apply (simp add: cap_delete_one_def unless_def is_final_cap_def)
  apply wp
     apply (rule_tac Q="\<lambda>rv s. \<forall>sl' R. if (sl' = slot)
                               then cte_wp_at (\<lambda>c. c = cap.NullCap) sl' s
                               else caps_of_state s sl' \<noteq> Some (cap.ReplyCap t False R)"
                  in hoare_post_imp)
      apply (clarsimp simp add: has_reply_cap_def is_reply_cap_to_def cte_wp_at_caps_of_state
                      simp del: split_paired_All split_paired_Ex
                         split: if_split_asm elim!: allEI)
     apply (rule hoare_vcg_all_lift)
     apply simp
     apply (wp hoare_weak_lift_imp empty_slot_deletes empty_slot_caps_of_state get_cap_wp)+
  apply (fastforce simp: cte_wp_at_caps_of_state valid_reply_caps_def
                        is_cap_simps unique_reply_caps_def is_reply_cap_to_def
              simp del: split_paired_All)
  done
```

## 2. 新增 lemma

```isabelle
lemma cap_delete_one_deletes_reply_strong:
  "\<lbrace>cte_wp_at (is_reply_cap_to t) slot and valid_reply_caps\<rbrace>
    cap_delete_one slot
   \<lbrace>\<lambda>rv s. \<forall>sl' R. if (sl' = slot)
       then cte_wp_at (\<lambda>c. c = cap.NullCap) sl' s
       else caps_of_state s sl' \<noteq> Some (cap.ReplyCap t False R)\<rbrace>"
  apply (simp add: cap_delete_one_def unless_def is_final_cap_def)
  apply wp
     apply (rule hoare_vcg_all_lift)
     apply simp
     apply (wp hoare_weak_lift_imp empty_slot_deletes empty_slot_caps_of_state get_cap_wp)+
  apply (fastforce simp: cte_wp_at_caps_of_state valid_reply_caps_def
                        is_cap_simps unique_reply_caps_def is_reply_cap_to_def
              simp del: split_paired_All)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`inline-redirect`，priority=low
> explicit stronger post in rule_tac Q="..." in hoare_post_imp [INTERIOR redirect — describes an inner Hoare step, not this lemma's post; verify which op it strengthens]
> proof 中路过的更强 post：`\<lambda>rv s. \<forall>sl' R. if (sl' = slot)
                               then cte_wp_at (\<lambda>c. c = cap.NullCap) sl' s
                               else caps_of_state s sl' \<noteq> Some (cap.ReplyCap t False R)`

**agent 论证**：The proof of cap_delete_one_deletes_reply (L929-948) internally establishes the stronger postcondition via rule_tac Q at L935-937, then immediately weakens it to \<not> has_reply_cap t s via hoare_post_imp. Removing that weakening step yields a named lemma with the precise per-slot information: slot is NullCap, other slots lack the reply cap. The proof is the body of cap_delete_one_deletes_reply minus the hoare_post_imp bridge.

**强化关系**（agent 自述）：(\<forall>sl' R. if (sl' = slot) then cte_wp_at (\<lambda>c. c = cap.NullCap) sl' s else caps_of_state s sl' \<noteq> Some (cap.ReplyCap t False R)) \<Longrightarrow> \<not> has_reply_cap t s

**delivery**：named，目标：Future consumer proving stronger slot-specific postcondition about reply caps after cap_delete_one；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***  3. \<And>cap.
***        cap \<noteq> NullCap \<Longrightarrow>
***        \<lbrace>?Q21 cap\<rbrace> gets (is_final_cap' cap)
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "Q:Finalise_AI:cap_delete_one_deletes_reply_strong" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
