# record — `set_simple_ko_cur_thread`  (F-slot, IMPACT-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/KHeap_AI.thy` |
| Slot / delivery | F / wp (realized) |
| 裁决 | **IMPACT-FAILED** |
| 墙钟 | baseline=43071 ms · trial=42900 ms · Δ -0.4% |
| 锚点 | 插入于 L1287 之后 |

## 1. 参照 lemma（F-slot 无单一"原 lemma"——这是锚点处的同族 sibling，新 lemma 模仿它的形态）

```isabelle
lemma set_object_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

## 2. 新增 lemma

```isabelle
lemma set_simple_ko_cur_thread[wp]:
  "set_simple_ko f ptr val \<lbrace>\<lambda>s. P (cur_thread s)\<rbrace>"
  by (set_simple_ko_method wp_thm: set_object_cur_thread[THEN hoare_set_object_weaken_pre] get_object_wp)
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**agent 论证**：set_object_cur_thread[wp] exists at line 1285. The set_simple_ko_method macro requires get_object_wp to discharge the get_object subgoal; the original proposal omitted it, causing 2 residual subgoals. Pattern matches set_simple_ko_valid_ioc at line 1271.

**强化关系**（agent 自述）：Adds a new wp frame lemma not present in the file; downstream wp proofs involving cur_thread and set_simple_ko previously had to solve this goal manually.

**delivery**：wp；gate：F+wp accepted (frame-lemma default)

## 4. 修改过程（试错链）

- **attempt 0**（初稿，anchor L1287）→ trial 失败：
  ```
  ***                          (\<exists>!y.
  ***                              f y = Notification x4) \<longrightarrow>
  ***                          r = Notification x4 \<longrightarrow>
  ***                          (\<forall>x12.
  ```
- **repair 1**：agent 依据 prover 错误修正 → anchor L1287
- 最终结果：**IMPACT-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (42900ms)
```
**最终裁决**：`IMPACT-FAILED`（ledger 终态事件：`discovered`）
**impact**：verdict=`noop`，gate_pass=False，wall gate ✓（详见 `measurement.json`）
**落地**：未落地（IMPACT-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "F:KHeap_AI:set_simple_ko_cur_thread" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
