# record — `valid_arch_state_global_pd'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchUntyped_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=44813 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L467 之后 |
| 锚定的原 lemma | `valid_arch_state_global_pd` (L458) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma valid_arch_state_global_pd:
  "\<lbrakk> valid_arch_state s; pspace_aligned s \<rbrakk>
    \<Longrightarrow> obj_at (\<lambda>ko. \<exists>pd. ko = ArchObj (PageDirectory pd)) (arm_global_pd (arch_state s)) s
           \<and> is_aligned (arm_global_pd (arch_state s)) pd_bits"
  apply (clarsimp simp: valid_arch_state_def a_type_def
                        pd_aligned pd_bits_def pageBits_def
                 elim!: obj_at_weakenE)
  apply (clarsimp split: Structures_A.kernel_object.split_asm
                         arch_kernel_obj.split_asm if_split_asm)
  done
```

## 2. 新增 lemma

```isabelle
lemma valid_arch_state_global_pd':
  "valid_arch_state s
    \<Longrightarrow> obj_at (\<lambda>ko. \<exists>pd. ko = ArchObj (PageDirectory pd)) (arm_global_pd (arch_state s)) s
           \<and> is_aligned (arm_global_pd (arch_state s)) pd_bits"
  apply (clarsimp simp: valid_arch_state_def a_type_def
                        pd_aligned pd_bits_def pageBits_def
                 elim!: obj_at_weakenE)
  apply (clarsimp split: Structures_A.kernel_object.split_asm
                         arch_kernel_obj.split_asm if_split_asm)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `pspace_aligned s` head `pspace_aligned` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`pspace_aligned s`

**agent 论证**：The conjunct `pspace_aligned s` never appears in the proof body of valid_arch_state_global_pd. The proof only uses `valid_arch_state_def`, `a_type_def`, `pd_aligned`, `pd_bits_def`, `pageBits_def`, and case splits — none of which require pspace_aligned as a hypothesis. This is a pure implication lemma making it a high-confidence P-slot candidate.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：valid_arch_state s ==> (valid_arch_state s \<and> pspace_aligned s)

**delivery**：named，目标：Callers of valid_arch_state_global_pd that have valid_arch_state but not pspace_aligned；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                       (ob = ArchObj x5 \<longrightarrow>
***                        (\<forall>x41 x42.
***                            (x41 \<longrightarrow>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchUntyped_AI:valid_arch_state_global_pd'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
