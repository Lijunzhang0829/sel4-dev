# record — `set_simple_ko_domain_index`  (F-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/KHeap_AI.thy` |
| Slot / delivery | F / wp (realized) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=43071 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L1437 之后 |

## 1. 参照 lemma（F-slot 无单一"原 lemma"——这是锚点处的同族 sibling，新 lemma 模仿它的形态）

```isabelle
lemma set_object_domain_index[wp]:
  "\<lbrace>\<lambda>s. P (domain_index s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (domain_index s)\<rbrace>"
  by (wpsimp wp: get_object_wp simp: set_object_def)
```

## 2. 新增 lemma

```isabelle
lemma set_simple_ko_domain_index[wp]:
  "set_simple_ko f ptr val \<lbrace>\<lambda>s. P (domain_index s)\<rbrace>"
  unfolding set_simple_ko_def
  by (wpsimp wp: set_object_domain_index)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**agent 论证**：set_simple_ko_method generated residual CNode subgoals; unfolding set_simple_ko_def and delegating to set_object_domain_index[wp] via wpsimp avoids those case splits entirely.

**强化关系**（agent 自述）：Pure-additive: new [wp] entry for domain_index field under set_simple_ko, not previously in the wp set.

**delivery**：wp；gate：F+wp accepted (frame-lemma default)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L1437）→ trial 失败：
  ```
  ***                  (\<exists>y. partial_inv f obj = Some y) \<longrightarrow>
  ***                  P (domain_index s)) \<and>
  ***                 (a_type obj = ANTFN \<longrightarrow>
  ***                  (\<exists>y. partial_inv f obj = Some y) \<longrightarrow>
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L1437
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                  P (domain_index s)) \<and>
***                 (a_type obj = ANTFN \<longrightarrow>
***                  (\<exists>y. partial_inv f obj = Some y) \<longrightarrow>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "F:KHeap_AI:set_simple_ko_domain_index" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
