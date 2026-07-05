# record — `update_cdt_is_original_cap`  (F-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/CSpace_AI.thy` |
| Slot / delivery | F / wp (realized) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=65050 ms · trial=65698 ms · Δ 1.0% |
| 锚点 | 插入于 L138 之后 |

## 1. 参照 lemma（F-slot 无单一"原 lemma"——这是锚点处的同族 sibling，新 lemma 模仿它的形态）

```isabelle
lemma update_cdt_cdt:
  "\<lbrace>\<lambda>s. valid_mdb (cdt_update (\<lambda>_. (m (cdt s))) s)\<rbrace> update_cdt m \<lbrace>\<lambda>_. valid_mdb\<rbrace>"
  by (simp add: update_cdt_def set_cdt_def) wp
```

## 2. 新增 lemma

```isabelle
lemma update_cdt_is_original_cap[wp]:
  "\<lbrace>\<lambda>s. P (is_original_cap s)\<rbrace> update_cdt f \<lbrace>\<lambda>_ s. P (is_original_cap s)\<rbrace>"
  by (wpsimp simp: update_cdt_def)
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**agent 论证**：update_cdt is defined as set_cdt applied to a modified cdt; it touches only the cdt field. The file proves update_cdt_cdt[wp] at line 136 for the cdt field; is_original_cap is the natural sibling frame to add.

**强化关系**（agent 自述）：Adds an explicit wp-registered frame for is_original_cap over update_cdt where none existed.

**delivery**：wp；gate：F+wp accepted (frame-lemma default)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (65698ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "F:CSpace_AI:update_cdt_is_original_cap" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 已是 `realized`——wp 自动生效或 consumer 已落地，无需 lifecycle sweep 推进）
