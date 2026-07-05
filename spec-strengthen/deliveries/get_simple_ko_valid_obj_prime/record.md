# record — `get_simple_ko_valid_obj'`  (P-slot, trial_passed)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/KHeap_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **trial_passed** |
| 墙钟 | baseline=47337 ms · trial=43793 ms · Δ -7.49% |
| 锚点 | 插入于 L357 之后 |
| 锚定的原 lemma | `get_simple_ko_valid_obj` (L346) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma get_simple_ko_valid_obj[wp]:
  "\<lbrace> valid_objs and obj_at (\<lambda>ko. bound (partial_inv f ko)) ep \<rbrace>
   get_simple_ko f ep
   \<lbrace> \<lambda>r. valid_obj ep (f r) \<rbrace>"
  apply (simp add: get_simple_ko_def)
  apply (rule bind_wp)
   prefer 2
   apply (rule hoare_pre_imp [OF _ get_object_valid])
   apply (simp add: invs_def valid_state_def valid_pspace_def)
  apply (wpsimp simp: partial_inv_def the_equality valid_obj_def
               split: option.splits)
  done
```

## 2. 新增 lemma

```isabelle
lemma get_simple_ko_valid_obj'[wp]:
  "\<lbrace> valid_objs \<rbrace>
   get_simple_ko f ep
   \<lbrace> \<lambda>r. valid_obj ep (f r) \<rbrace>"
  apply (simp add: get_simple_ko_def)
  apply (rule bind_wp)
   prefer 2
   apply (rule hoare_pre_imp [OF _ get_object_valid])
   apply (simp add: invs_def valid_state_def valid_pspace_def)
  apply (wpsimp simp: partial_inv_def the_equality valid_obj_def
               split: option.splits)
  done
```
（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive 纪律下零改动）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `valid_objs` head `valid_objs` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `get_simple_ko` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_objs`

**agent 论证**：get_simple_ko is a read/decode operation. If the object at ep does not satisfy bound (partial_inv f ko) then get_simple_ko assert-fails, making the Hoare triple vacuously true. When get_simple_ko does succeed, get_object_valid already supplies valid_obj ep ko, and the partial_inv/the_equality rewriting in the proof relates ko to f r — no use of the obj_at conjunct is made. The original proof body copies verbatim: the hoare_pre_imp step only needs to discharge valid_objs s => valid_objs s (trivial), and the final wpsimp still closes the continuation goal.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_objs and obj_at (\<lambda>ko. bound (partial_inv f ko)) ep) ==> (valid_objs) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `obj_at (\<lambda>ko. bound (partial_inv f ko)) ep`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Callers of get_simple_ko_valid_obj that already hold valid_objs but cannot guarantee obj_at (bound ...) — e.g. sites that call get_simple_ko on an endpoint whose existence is not yet established.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
OK (43793ms)
```
**最终裁决**：`trial_passed`（ledger 终态事件：`trial_passed`）
**impact**：verdict=`additive`，gate_pass=True，wall gate ✓（详见 `measurement.json`）
**落地**：dry-run 通过，未写源文件；复现：`./command.sh`

## 6. 跨批次追踪

```
grep "P:KHeap_AI:get_simple_ko_valid_obj'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 consumer 落地后推进到 realized，或 grace 超期后标 orphan）
