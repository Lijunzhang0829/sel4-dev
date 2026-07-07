# record — `suspend_unlive'_no_valid_mdb`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchFinalise_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=58840 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L524 之后 |
| 锚定的原 lemma | `suspend_unlive'` (L508) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma suspend_unlive':
  "\<lbrace>bound_tcb_at ((=) None) t and valid_mdb and valid_objs and tcb_at t \<rbrace>
      suspend t
   \<lbrace>\<lambda>rv. obj_at (Not \<circ> live) t\<rbrace>"
  apply (simp add: suspend_def set_thread_state_def set_object_def get_object_def)
  supply hoare_vcg_if_split[wp_split del] if_split[split del]
  apply (wp | simp only: obj_at_exst_update)+
     apply (simp add: obj_at_def live_def hyp_live_def)
     apply (rule_tac Q="\<lambda>_. bound_tcb_at ((=) None) t" in hoare_strengthen_post)
      supply hoare_vcg_if_split[wp_split]
      apply wp
     apply (auto simp: pred_tcb_def2)[1]
    apply (simp flip: if_split)
    apply wp
   apply wp
  apply simp
  done
```

## 2. 新增 lemma

```isabelle
lemma suspend_unlive'_no_valid_mdb:
  "\\<lbrace>bound_tcb_at ((=) None) t and valid_objs and tcb_at t\\<rbrace>
      suspend t
   \\<lbrace>\\<lambda>rv. obj_at (Not \\<circ> live) t\\<rbrace>"
  apply (simp add: suspend_def set_thread_state_def set_object_def get_object_def)
  supply hoare_vcg_if_split[wp_split del] if_split[split del]
  apply (wp | simp only: obj_at_exst_update)+
     apply (simp add: obj_at_def live_def hyp_live_def)
     apply (rule_tac Q="\<lambda>_. bound_tcb_at ((=) None) t" in hoare_strengthen_post)
      supply hoare_vcg_if_split[wp_split]
      apply wp
     apply (auto simp: pred_tcb_def2)[1]
    apply (simp flip: if_split)
    apply wp
   apply wp
  apply simp
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`inline-redirect`，priority=low
> explicit stronger post in rule_tac Q="..." in hoare_strengthen_post [INTERIOR redirect — describes an inner Hoare step, not this lemma's post; verify which op it strengthens]
> proof 中路过的更强 post：`\<lambda>_. bound_tcb_at ((=) None) t`

**agent 论证**：The proof body of suspend_unlive' at L512-524 never references valid_mdb: it expands suspend_def then uses wp/simp to establish bound_tcb_at via hoare_strengthen_post. The valid_mdb conjunct is not consumed by any visible rule application. This is a borderline but worthwhile trial — removing it yields a strictly stronger precondition.

**强化关系**（机械验证未通过：post differs from the hinted lemma — not a pure premise-weakening；以下为 agent 自述）：(bound_tcb_at ((=) None) t and valid_objs and tcb_at t) ==> (bound_tcb_at ((=) None) t and valid_mdb and valid_objs and tcb_at t)

**delivery**：named，目标：finalise_cap_replaceable and other consumers of suspend_unlive' that currently require valid_mdb in their preconditions；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (22240ms)
*** Malformed command syntax
*** At command "<malformed>" (line 526 of "/tmp/tmp.BioXQNb6r9/Tmp_aac18bc726a3fabf.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchFinalise_AI:suspend_unlive'_no_valid_mdb" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
