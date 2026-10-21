# record — `resolve_address_bits_valid_fault2_invs`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/Syscall_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=86011 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L823 之后 |
| 锚定的原 lemma | `resolve_address_bits_valid_fault2` (L814) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma resolve_address_bits_valid_fault2:
  "\<lbrace>invs and valid_cap (fst param)\<rbrace>
   resolve_address_bits param
   -,\<lbrace>\<lambda>f s. valid_fault (ExceptionTypes_A.fault.CapFault x y f)\<rbrace>"
  apply (cut_tac resolve_address_bits_valid_fault[of param x y])
  apply (clarsimp simp add: validE_E_def validE_def valid_def
                  split: sum.splits)
  apply (drule invs_valid_objs)
  apply fastforce
  done
```

## 2. 新增 lemma

```isabelle
lemma resolve_address_bits_valid_fault2_invs:
  "\<lbrace>invs\<rbrace>
   resolve_address_bits param
   -,\<lbrace>\<lambda>f s. valid_fault (ExceptionTypes_A.fault.CapFault x y f)\<rbrace>"
  apply (cut_tac resolve_address_bits_valid_fault[of param x y])
  apply (clarsimp simp add: validE_E_def validE_def valid_def
                  split: sum.splits)
  apply (drule invs_valid_objs)
  apply fastforce
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `valid_cap (fst param)` head `valid_cap` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `resolve_address_bits` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_cap (fst param)`

**agent 论证**：The conjunct `valid_cap (fst param)` does not appear in the proof body of `resolve_address_bits_valid_fault2`. The proof cuts in `resolve_address_bits_valid_fault` (which internally carries `valid_cap`), unfolds the validE_E obligation, obtains `valid_objs` from `invs_valid_objs`, and closes with `fastforce`. The `resolve_address_bits` op is read-only. The `valid_cap (fst param)` premise may be droppable if `fastforce` can close the residual obligation using only `valid_objs` together with the structure of valid_fault; the trial confirms this.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(invs and valid_cap (fst param)) ==> (invs) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `valid_cap (fst param)`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：resolve_address_bits consumers that hold only invs (e.g. lookup_cap_and_slot_valid_fault2, hw_invs); follow-up patch replaces resolve_address_bits_valid_fault2 with this stronger form.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (600438ms)
*** check-theory.sh: killed after 600s wall-time ceiling (CHECK_THEORY_TIMEOUT_S). Search-explosion guard.
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:Syscall_AI:resolve_address_bits_valid_fault2_invs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
