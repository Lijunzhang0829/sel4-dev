# record — `post_cap_delete_pre_is_final_cap''`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchCNodeInv_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=47504 ms · trial=48019 ms · Δ 1.08% |
| 锚点 | 插入于 L550 之后 |
| 锚定的原 lemma | `post_cap_delete_pre_is_final_cap'` (L540) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma post_cap_delete_pre_is_final_cap':
  "\<And>rv s'' rva s''a s.
       \<lbrakk>valid_ioports s; caps_of_state s slot = Some cap; is_final_cap' cap s; cap_cleanup_opt cap \<noteq> NullCap\<rbrakk>
       \<Longrightarrow> post_cap_delete_pre (cap_cleanup_opt cap) ((caps_of_state s)(slot \<mapsto> NullCap))"
  apply (clarsimp simp: cap_cleanup_opt_def cte_wp_at_def post_cap_delete_pre_def arch_cap_cleanup_opt_def
                      split: cap.split_asm if_split_asm
                      elim!: ranE dest!: caps_of_state_cteD)
   (* IRQHandlerCap case *)
   apply (drule(2) final_cap_duplicate_irq)
     apply simp+
  done
```

## 2. 新增 lemma

```isabelle
lemma post_cap_delete_pre_is_final_cap'':
  "\<And>rv s'' rva s''a s.
       \<lbrakk>caps_of_state s slot = Some cap; is_final_cap' cap s; cap_cleanup_opt cap \<noteq> NullCap\<rbrakk>
       \<Longrightarrow> post_cap_delete_pre (cap_cleanup_opt cap) ((caps_of_state s)(slot \<mapsto> NullCap))"
  apply (clarsimp simp: cap_cleanup_opt_def cte_wp_at_def post_cap_delete_pre_def arch_cap_cleanup_opt_def
                      split: cap.split_asm if_split_asm
                      elim!: ranE dest!: caps_of_state_cteD)
   (* IRQHandlerCap case *)
   apply (drule(2) final_cap_duplicate_irq)
     apply simp+
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `valid_ioports s` head `valid_ioports` never appears in the proof body (prefix match incl. _def/_E forms); 2 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`valid_ioports s`

**agent 论证**：The proof of post_cap_delete_pre_is_final_cap' never references valid_ioports: it unfolds cap_cleanup_opt_def/post_cap_delete_pre_def and dispatches the IRQHandlerCap case via final_cap_duplicate_irq, which takes caps_of_state and is_final_cap' but not valid_ioports. The valid_ioports hypothesis is inert to the proof.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(caps_of_state s slot = Some cap \<and> is_final_cap' cap s \<and> cap_cleanup_opt cap \<noteq> NullCap) ==> (valid_ioports s \<and> caps_of_state s slot = Some cap \<and> is_final_cap' cap s \<and> cap_cleanup_opt cap \<noteq> NullCap)

**delivery**：named，目标：The call site in rec_del_invs'' (L637) applies post_cap_delete_pre_is_final_cap' after extracting invs; a version without valid_ioports would allow dropping the valid_ioports extraction step there.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (48019ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchCNodeInv_AI:post_cap_delete_pre_is_final_cap''" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
