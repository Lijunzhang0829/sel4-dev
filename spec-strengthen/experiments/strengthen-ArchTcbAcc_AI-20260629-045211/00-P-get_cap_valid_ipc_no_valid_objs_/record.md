# record — `get_cap_valid_ipc_no_valid_objs`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchTcbAcc_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=43079 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L122 之后 |
| 锚定的原 lemma | `get_cap_valid_ipc` (L111) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma get_cap_valid_ipc [TcbAcc_AI_assms]:
  "\<lbrace>valid_objs and obj_at (\<lambda>ko. \<exists>tcb. ko = TCB tcb \<and> tcb_ipc_buffer tcb = v) t\<rbrace>
     get_cap (t, tcb_cnode_index 4)
   \<lbrace>\<lambda>rv s. valid_ipc_buffer_cap rv v\<rbrace>"
  apply (wp get_cap_wp)
  apply clarsimp
  apply (drule(1) cte_wp_tcb_cap_valid)
  apply (clarsimp simp add: tcb_cap_valid_def obj_at_def)
  apply (simp add: valid_ipc_buffer_cap_def mask_cap_def cap_rights_update_def
                   acap_rights_update_def is_tcb
            split: cap.split_asm arch_cap.split_asm)
  done
```

## 2. 新增 lemma

```isabelle
lemma get_cap_valid_ipc_no_valid_objs [TcbAcc_AI_assms]:
  "\<lbrace>obj_at (\<lambda>ko. \<exists>tcb. ko = TCB tcb \<and> tcb_ipc_buffer tcb = v) t\<rbrace>
     get_cap (t, tcb_cnode_index 4)
   \<lbrace>\<lambda>rv s. valid_ipc_buffer_cap rv v\<rbrace>"
  apply (wp get_cap_wp)
  apply clarsimp
  apply (drule(1) cte_wp_tcb_cap_valid)
  apply (clarsimp simp add: tcb_cap_valid_def obj_at_def)
  apply (simp add: valid_ipc_buffer_cap_def mask_cap_def cap_rights_update_def
                   acap_rights_update_def is_tcb
             split: cap.split_asm arch_cap.split_asm)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `valid_objs` head `valid_objs` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `get_cap` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_objs`

**agent 论证**：get_cap is a read-only op; the scanner flags valid_objs as never appearing in the proof text. The only explicit consumer of a context fact is drule(1) cte_wp_tcb_cap_valid, whose first argument after clarsimp is the obj_at assumption, not valid_objs. Worth a trial.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_objs and obj_at (\<lambda>ko. \<exists>tcb. ko = TCB tcb \<and> tcb_ipc_buffer tcb = v) t) ==> (obj_at (\<lambda>ko. \<exists>tcb. ko = TCB tcb \<and> tcb_ipc_buffer tcb = v) t) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `valid_objs`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future consumers of get_cap_valid_ipc that do not hold valid_objs；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                         ko = TCB tcb \<and> tcb_ipc_buffer tcb = v)
***                  t s;
***         cte_wp_at ((=) cap) (t, tcb_cnode_index 4) s\<rbrakk>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchTcbAcc_AI:get_cap_valid_ipc_no_valid_objs" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
