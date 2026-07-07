# record — `set_cap_cdt_wp`  (F-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/CSpace_AI.thy` |
| Slot / delivery | F / wp (realized) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=65050 ms · trial=65995 ms · Δ 1.45% |
| 锚点 | 插入于 L888 之后 |

## 1. 参照 lemma（F-slot 无单一"原 lemma"——这是锚点处的同族 sibling，新 lemma 模仿它的形态）

```isabelle
lemma mdb_set_cap:
  "(x,s') \<in> fst (set_cap p c s) \<Longrightarrow> cdt s' = cdt s"
  by (clarsimp simp: set_cap_def in_monad split_def get_object_def set_object_def
```

## 2. 新增 lemma

```isabelle
lemma set_cap_cdt_wp[wp]:
  "\<lbrace>\<lambda>s. P (cdt s)\<rbrace> set_cap cap p \<lbrace>\<lambda>_ s. P (cdt s)\<rbrace>"
  by (wpsimp simp: set_cap_def split_def set_object_def wp: get_object_wp | clarsimp)+
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**agent 论证**：mdb_set_cap at line 885 already proves (as a monad fact) that set_cap preserves cdt. This adds the canonical Hoare-triple wp form so the frame fires automatically in wp tactic searches.

**强化关系**（agent 自述）：Strictly stronger delivery: converts the existing monad-equality fact into a wp-registered Hoare triple usable directly by the wp tactic.

**delivery**：wp；gate：F+wp accepted (frame-lemma default)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (65995ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "F:CSpace_AI:set_cap_cdt_wp" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 已是 `realized`——wp 自动生效或 consumer 已落地，无需 lifecycle sweep 推进）
