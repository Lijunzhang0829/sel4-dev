# record — `pd_at_asid_unique_no_vspace_objs`  (P-slot, IMPACT-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchVSpace_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **IMPACT-FAILED** |
| 墙钟 | baseline=227453 ms · trial=229688 ms · Δ 0.98% |
| 锚点 | 插入于 L114 之后 |
| 锚定的原 lemma | `pd_at_asid_unique` (L99) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma pd_at_asid_unique:
  "\<lbrakk> vspace_at_asid asid pd s; vspace_at_asid asid' pd s;
     unique_table_refs (caps_of_state s);
     valid_vs_lookup s; valid_vspace_objs s; valid_global_objs s;
     valid_arch_state s; asid < 2 ^ asid_bits; asid' < 2 ^ asid_bits \<rbrakk>
       \<Longrightarrow> asid = asid'"
  apply (clarsimp simp: vspace_at_asid_def)
  apply (drule(1) valid_vs_lookupD[OF vs_lookup_pages_vs_lookupI])+
  apply (clarsimp simp: table_cap_ref_ap_eq[symmetric])
  apply (clarsimp simp: table_cap_ref_def
                 split: cap.split_asm arch_cap.split_asm option.split_asm)
  apply (drule(2) unique_table_refsD,
         simp+, clarsimp simp: table_cap_ref_def,
         erule(1) asid_low_high_bits)
   apply simp+
  done
```

## 2. 新增 lemma

```isabelle
lemma pd_at_asid_unique_no_vspace_objs:
  "\<lbrakk> vspace_at_asid asid pd s; vspace_at_asid asid' pd s;
     unique_table_refs (caps_of_state s);
     valid_vs_lookup s; valid_global_objs s;
     valid_arch_state s; asid < 2 ^ asid_bits; asid' < 2 ^ asid_bits \<rbrakk>
       \<Longrightarrow> asid = asid'"
  apply (clarsimp simp: vspace_at_asid_def)
  apply (drule(1) valid_vs_lookupD[OF vs_lookup_pages_vs_lookupI])+
  apply (clarsimp simp: table_cap_ref_ap_eq[symmetric])
  apply (clarsimp simp: table_cap_ref_def
                 split: cap.split_asm arch_cap.split_asm option.split_asm)
  apply (drule(2) unique_table_refsD,
         simp+, clarsimp simp: table_cap_ref_def,
         erule(1) asid_low_high_bits)
   apply simp+
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `valid_vspace_objs s` head `valid_vspace_objs` never appears in the proof body (prefix match incl. _def/_E forms); 5 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`valid_vspace_objs s`

**agent 论证**：The proof body of pd_at_asid_unique uses only valid_vs_lookupD, unique_table_refsD, and asid_low_high_bits. None of these rules require valid_vspace_objs. The scanner confirmed valid_vspace_objs never appears in the proof text despite 5 other conjuncts being visibly consumed, giving a strong differential signal.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(vspace_at_asid asid pd s ∧ vspace_at_asid asid' pd s ∧ unique_table_refs (caps_of_state s) ∧ valid_vs_lookup s ∧ valid_global_objs s ∧ valid_arch_state s ∧ asid < 2^asid_bits ∧ asid' < 2^asid_bits) ⟹ (... ∧ valid_vspace_objs s ∧ ...)

**delivery**：named，目标：Future callers of pd_at_asid_unique that do not have valid_vspace_objs in scope can switch to this weaker-precondition variant.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (229688ms)
```
**最终裁决**：`IMPACT-FAILED`（ledger 终态事件：`discovered`）
**impact**：verdict=`noop`，gate_pass=False，wall gate ✓（详见 `measurement.json`）
**落地**：未落地（IMPACT-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchVSpace_AI:pd_at_asid_unique_no_vspace_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
