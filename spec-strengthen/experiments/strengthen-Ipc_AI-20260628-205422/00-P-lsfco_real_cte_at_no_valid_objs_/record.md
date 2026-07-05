# record — `lsfco_real_cte_at_no_valid_objs`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Ipc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=120624 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L60 之后 |
| 锚定的原 lemma | `lsfco_real_cte_at` (L56) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma lsfco_real_cte_at:
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. real_cte_at rv\<rbrace>,-"
  by (rule lookup_cnode_slot_real_cte)
```

## 2. 新增 lemma

```isabelle
lemma lsfco_real_cte_at_no_valid_objs:
  "\<lbrace>valid_cap cn\<rbrace>
  lookup_slot_for_cnode_op f cn idx depth
  \<lbrace>\<lambda>rv. real_cte_at rv\<rbrace>,-"
  by (rule lookup_cnode_slot_real_cte)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `valid_objs` head `valid_objs` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `lookup_slot_for_cnode_op` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_objs`

**agent 论证**：The proof is a single rule application of lookup_cnode_slot_real_cte, whose own precondition is valid_cap cn. Dropping valid_objs yields a strictly stronger precondition. The previous failure was purely a JSON escaping bug: \\< produced \< in the file instead of \<.

**强化关系**（机械验证未通过：post differs from the hinted lemma — not a pure premise-weakening；以下为 agent 自述）：(valid_cap cn) ==> (valid_objs and valid_cap cn)

**delivery**：named，目标：Any caller of lsfco_real_cte_at that holds only valid_cap cn but not valid_objs can switch to this stronger variant；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L60）→ trial 失败：
  ```
  *** goal (1 subgoal):
  ***  1. \<lbrace>valid_cap cn\<rbrace> lookup_slot_for_cnode_op f cn idx depth 
  ***     \<lbrace>real_cte_at\<rbrace>, -
  *** At command "by" (line 66 of "/tmp/tmp.3kCZbOchH9/Tmp_2b853af6ec1f8dd6.thy")
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L60
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***  1. \<lbrace>valid_cap cn\<rbrace> lookup_slot_for_cnode_op f cn idx depth 
***     \<lbrace>real_cte_at\<rbrace>, -
*** At command "by" (line 66 of "/tmp/tmp.3kCZbOchH9/Tmp_2b853af6ec1f8dd6.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:Ipc_AI:lsfco_real_cte_at_no_valid_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
