# record — `same_object_also_valid_no_asid_base`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchTcb_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=93808 ms · trial=94258 ms · Δ 0.48% |
| 锚点 | 插入于 L38 之后 |
| 锚定的原 lemma | `same_object_also_valid` (L28) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma same_object_also_valid:  (* arch specific *)
  "\<lbrakk> same_object_as cap cap'; s \<turnstile> cap'; wellformed_cap cap;
     cap_asid cap = None \<or> (\<exists>asid. cap_asid cap = Some asid \<and> 0 < asid \<and> asid \<le> 2^asid_bits - 1);
     cap_vptr cap = None; cap_asid_base cap = None \<rbrakk>
     \<Longrightarrow> s \<turnstile> cap"
  apply (cases cap,
         (clarsimp simp: same_object_as_def is_cap_simps cap_asid_def
                         wellformed_cap_def wellformed_acap_def
                         valid_cap_def bits_of_def cap_aligned_def
                   split: cap.split_asm arch_cap.split_asm option.splits)+)
  done
```

## 2. 新增 lemma

```isabelle
lemma same_object_also_valid_no_asid_base:  (* arch specific *)
  "\<lbrakk> same_object_as cap cap'; s \<turnstile> cap'; wellformed_cap cap;
     cap_asid cap = None \<or> (\<exists>asid. cap_asid cap = Some asid \<and> 0 < asid \<and> asid \<le> 2^asid_bits - 1);
     cap_vptr cap = None \<rbrakk>
     \<Longrightarrow> s \<turnstile> cap"
  apply (cases cap,
         (clarsimp simp: same_object_as_def is_cap_simps cap_asid_def
                         wellformed_cap_def wellformed_acap_def
                         valid_cap_def bits_of_def cap_aligned_def
                   split: cap.split_asm arch_cap.split_asm option.splits)+)
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `cap_vptr cap = None` head `cap_vptr` never appears in the proof body (prefix match incl. _def/_E forms); 3 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`cap_vptr cap = None`

**agent 论证**：The proof body of same_object_also_valid never mentions cap_asid_base anywhere — not in any simp lemma name, frule, drule, or clarsimp hint. Dropping it yields a strictly stronger implication lemma. The proof body is copied verbatim.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(same_object_as cap cap' \<and> s \<turnstile> cap' \<and> wellformed_cap cap \<and> cap_asid_cond \<and> cap_vptr cap = None) ==> (same_object_as cap cap' \<and> s \<turnstile> cap' \<and> wellformed_cap cap \<and> cap_asid_cond \<and> cap_vptr cap = None \<and> cap_asid_base cap = None)

**delivery**：named，目标：Future callers of same_object_also_valid that do not have cap_asid_base cap = None in context；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (94258ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:ArchTcb_AI:same_object_also_valid_no_asid_base" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
