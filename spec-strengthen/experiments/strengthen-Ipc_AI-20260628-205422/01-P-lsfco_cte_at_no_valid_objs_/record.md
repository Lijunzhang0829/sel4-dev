# record — `lsfco_cte_at_no_valid_objs`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Ipc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=120624 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L54 之后 |
| 锚定的原 lemma | `lsfco_cte_at` (L50) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma lsfco_cte_at:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. cte_at rv\<rbrace>,-"
  by (rule hoare_strengthen_postE_R, rule lookup_cnode_slot_real_cte, simp add: real_cte_at_cte)
```

## 2. 新增 lemma

```isabelle
lemma lsfco_cte_at_no_valid_objs:
  "\<lbrace>valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. cte_at rv\<rbrace>,-"
  by (rule hoare_strengthen_postE_R, rule lookup_cnode_slot_real_cte, simp add: real_cte_at_cte)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `valid_objs` head `valid_objs` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `lookup_slot_for_cnode_op` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_objs`

**agent 论证**：Fixed escaping: quadruple-backslash in JSON produced literal \\< in the .thy file rather than Isabelle unicode escapes, causing the malformed-command parse error. Now using double-backslash in JSON so the generated file contains proper \<lbrace> etc. The proof itself delegates to lookup_cnode_slot_real_cte via hoare_strengthen_postE_R then simp add: real_cte_at_cte.

**强化关系**（机械验证未通过：post differs from the hinted lemma — not a pure premise-weakening；以下为 agent 自述）：(valid_cap cn) ==> (valid_objs and valid_cap cn)

**delivery**：named，目标：Any caller of lsfco_cte_at that holds only valid_cap cn but not valid_objs can switch to this stronger variant; lsfco_cte_wp_at_univ could also be re-based on this；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L54）→ trial 失败：
  ```
  *** goal (1 subgoal):
  ***  1. \<lbrace>valid_cap cn\<rbrace> lookup_slot_for_cnode_op f cn idx depth 
  ***     \<lbrace>cte_wp_at (\<lambda>_. True)\<rbrace>, -
  *** At command "by" (line 60 of "/tmp/tmp.rmVW8VAk6t/Tmp_f9725265ddabc1fa.thy")
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L54
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***  1. \<lbrace>valid_cap cn\<rbrace> lookup_slot_for_cnode_op f cn idx depth 
***     \<lbrace>cte_wp_at (\<lambda>_. True)\<rbrace>, -
*** At command "by" (line 60 of "/tmp/tmp.rmVW8VAk6t/Tmp_f9725265ddabc1fa.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:Ipc_AI:lsfco_cte_at_no_valid_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
