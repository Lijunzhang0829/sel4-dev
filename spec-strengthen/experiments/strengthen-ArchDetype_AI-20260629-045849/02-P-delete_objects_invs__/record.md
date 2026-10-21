# record — `delete_objects_invs'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchDetype_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=39565 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L586 之后 |
| 锚定的原 lemma | `delete_objects_invs` (L566) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma delete_objects_invs[wp]:
  "\<lbrace>(\<lambda>s. \<exists>slot. cte_wp_at ((=) (cap.UntypedCap dev ptr bits f)) slot s
    \<and> descendants_range (cap.UntypedCap dev ptr bits f) slot s) and
    invs and ct_active\<rbrace>
    delete_objects ptr bits \<lbrace>\<lambda>_. invs\<rbrace>"
  apply (simp add: delete_objects_def)
  apply (simp add: freeMemory_def word_size_def bind_assoc
                   empty_fail_mapM_x ef_storeWord)
   apply (rule hoare_pre)
   apply (rule_tac G="is_aligned ptr bits \<and> word_size_bits \<le> bits \<and> bits \<le> word_bits"
                in hoare_grab_asm)
   apply (simp add: mapM_storeWord_clear_um[unfolded word_size_def]
                    intvl_range_conv[where 'a=machine_word_len, folded word_bits_def])
   apply wp
  apply clarsimp
  apply (frule invs_untyped_children)
  apply (frule detype_invariants, clarsimp+)
  apply (drule invs_valid_objs)
  apply (drule (1) cte_wp_valid_cap)
  apply (simp add: valid_cap_def cap_aligned_def word_size_bits_def untyped_min_bits_def)
  done
```

## 2. 新增 lemma

```isabelle
lemma delete_objects_invs'[wp]:
  "\<lbrace>(\<lambda>s. \<exists>slot. cte_wp_at ((=) (cap.UntypedCap dev ptr bits f)) slot s
    \<and> descendants_range (cap.UntypedCap dev ptr bits f) slot s) and
    invs\<rbrace>
    delete_objects ptr bits \<lbrace>\<lambda>_. invs\<rbrace>"
  apply (simp add: delete_objects_def)
  apply (simp add: freeMemory_def word_size_def bind_assoc
                   empty_fail_mapM_x ef_storeWord)
   apply (rule hoare_pre)
   apply (rule_tac G="is_aligned ptr bits \<and> word_size_bits \<le> bits \<and> bits \<le> word_bits"
                in hoare_grab_asm)
   apply (simp add: mapM_storeWord_clear_um[unfolded word_size_def]
                    intvl_range_conv[where 'a=machine_word_len, folded word_bits_def])
   apply wp
  apply clarsimp
  apply (frule invs_untyped_children)
  apply (frule detype_invariants, clarsimp+)
  apply (drule invs_valid_objs)
  apply (drule (1) cte_wp_valid_cap)
  apply (simp add: valid_cap_def cap_aligned_def word_size_bits_def untyped_min_bits_def)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `ct_active` head `ct_active` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `delete_objects` is write (write/modify → premise usually load-bearing, demoted); wp-chain implicit use is the trial's job
> 目标前提：`ct_active`

**agent 论证**：The proof body of the original `delete_objects_invs` never references `ct_active` — no `rule ct_active`, no `simp add: ct_active_def`, no `drule ct_active`. The detype_invariants frule needs the `detype_locale` hypotheses (invs + untyped cap witnesses), not ct_active specifically. If the trial succeeds, this gives a strictly weaker precondition usable in paths where the current thread is not active.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：((\<lambda>s. \<exists>slot. cte_wp_at ((=) (cap.UntypedCap dev ptr bits f)) slot s
    \<and> descendants_range (cap.UntypedCap dev ptr bits f) slot s) and
    invs and ct_active) ==> ((\<lambda>s. \<exists>slot. cte_wp_at ((=) (cap.UntypedCap dev ptr bits f)) slot s
    \<and> descendants_range (cap.UntypedCap dev ptr bits f) slot s) and
    invs) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `ct_active`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Any caller of delete_objects_invs that holds invs and the untyped cte_wp_at/descendants_range witnesses but not ct_active (e.g. non-active-thread delete paths)；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***            (clear_um (untyped_range (UntypedCap dev ptr bits f))
***              s))\<rbrakk>
***        \<Longrightarrow> is_aligned ptr bits \<and>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchDetype_AI:delete_objects_invs'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
