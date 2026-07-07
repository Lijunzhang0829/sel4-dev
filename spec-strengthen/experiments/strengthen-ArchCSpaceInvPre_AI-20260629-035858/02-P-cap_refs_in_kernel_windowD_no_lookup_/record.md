# record — `cap_refs_in_kernel_windowD_no_lookup`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchCSpaceInvPre_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=30696 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L280 之后 |
| 锚定的原 lemma | `cap_refs_in_kernel_windowD` (L273) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma cap_refs_in_kernel_windowD:
  "\<lbrakk> caps_of_state s ptr = Some cap; cap_refs_in_kernel_window s \<rbrakk>
   \<Longrightarrow> \<forall>ref \<in> cap_range cap.
         arm_kernel_vspace (arch_state s) ref = ArmVSpaceKernelWindow"
  apply (clarsimp simp: cap_refs_in_kernel_window_def valid_refs_def
                        cte_wp_at_caps_of_state)
  apply (cases ptr, fastforce)
  done
```

## 2. 新增 lemma

```isabelle
lemma cap_refs_in_kernel_windowD_no_lookup:
  "\<lbrakk> cap_refs_in_kernel_window s \<rbrakk>
   \<Longrightarrow> \<forall>ref \<in> cap_range cap.
         arm_kernel_vspace (arch_state s) ref = ArmVSpaceKernelWindow"
  apply (clarsimp simp: cap_refs_in_kernel_window_def valid_refs_def
                        cte_wp_at_caps_of_state)
  apply (cases ptr, fastforce)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `caps_of_state s ptr = Some cap` head `caps_of_state` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`caps_of_state s ptr = Some cap`

**agent 论证**：The scanner flagged caps_of_state head as absent from the proof body text. cap_refs_in_kernel_window_def quantifies over all caps in the range; if clarsimp can establish the goal for an arbitrary cap without needing the specific lookup hypothesis, the premise is droppable. Proof body copied verbatim; trial decides.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：cap_refs_in_kernel_window s ==> (caps_of_state s ptr = Some cap \<and> cap_refs_in_kernel_window s)

**delivery**：named，目标：Future callers of cap_refs_in_kernel_windowD that hold cap_refs_in_kernel_window but derive the cap lookup implicitly；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                    caps_of_state s (a, b) = Some cap \<longrightarrow>
***                    {x. arm_kernel_vspace (arch_state s) x \<noteq>
***                        ArmVSpaceKernelWindow} \<inter>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchCSpaceInvPre_AI:cap_refs_in_kernel_windowD_no_lookup" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
