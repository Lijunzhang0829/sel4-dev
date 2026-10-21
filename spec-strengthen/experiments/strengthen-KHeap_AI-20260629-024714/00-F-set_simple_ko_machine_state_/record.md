# record — `set_simple_ko_machine_state`  (F-slot, IMPACT-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/KHeap_AI.thy` |
| Slot / delivery | F / wp (realized) |
| 裁决 | **IMPACT-FAILED** |
| 墙钟 | baseline=43071 ms · trial=42467 ms · Δ -1.4% |
| 锚点 | 插入于 L1279 之后 |

## 1. 参照 lemma（F-slot 无单一"原 lemma"——这是锚点处的同族 sibling，新 lemma 模仿它的形态）

```isabelle
lemma set_object_machine_state[wp]:
  "set_object p ko \<lbrace>\<lambda>s. P (machine_state s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

## 2. 新增 lemma

```isabelle
lemma set_simple_ko_machine_state[wp]:
  "set_simple_ko f ptr val \<lbrace>\<lambda>s. P (machine_state s)\<rbrace>"
  unfolding set_simple_ko_def
  by (wpsimp wp: set_object_machine_state get_object_wp)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**agent 论证**：Previous proof used set_simple_ko_method but omitted get_object_wp, causing the internal get_object call to leave an unresolved subgoal. Unfolding set_simple_ko_def and using wpsimp with both set_object_machine_state and get_object_wp mirrors the standard pattern for set_simple_ko frame lemmas.

**强化关系**（agent 自述）：set_simple_ko_machine_state[wp] is strictly stronger than the trivial True precondition; it adds a new promise not previously registered in the wp set.

**delivery**：wp；gate：F+wp accepted (frame-lemma default)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L1279）→ trial 失败：
  ```
  FAILED (43258ms)
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L1279
- 最终结果：**IMPACT-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (42467ms)
```
**最终裁决**：`IMPACT-FAILED`（ledger 终态事件：`discovered`）
**impact**：verdict=`noop`，gate_pass=False，wall gate ✓（详见 `measurement.json`）
**落地**：未落地（IMPACT-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "F:KHeap_AI:set_simple_ko_machine_state" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
