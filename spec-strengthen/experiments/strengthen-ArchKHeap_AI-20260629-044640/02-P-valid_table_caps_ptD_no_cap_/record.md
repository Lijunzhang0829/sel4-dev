# record — `valid_table_caps_ptD_no_cap`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchKHeap_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=43934 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L714 之后 |
| 锚定的原 lemma | `valid_table_caps_ptD` (L704) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma valid_table_caps_ptD:
  "\<lbrakk> (caps_of_state s) p = Some (ArchObjectCap (arch_cap.PageTableCap p' None));
     page_table_at p' s; valid_table_caps s \<rbrakk> \<Longrightarrow>
    \<exists>pt. ko_at (ArchObj (PageTable pt)) p' s \<and> valid_vspace_obj (PageTable pt) s"
  apply (clarsimp simp: valid_table_caps_def simp del: split_paired_All)
  apply (erule allE)+
  apply (erule (1) impE)
  apply (clarsimp simp add: is_pt_cap_def cap_asid_def)
  apply (erule impE, rule refl)
  apply (clarsimp simp: obj_at_def empty_table_def)
  done
```

## 2. 新增 lemma

```isabelle
lemma valid_table_caps_ptD_no_cap:
  "\<lbrakk> page_table_at p' s; valid_table_caps s \<rbrakk> \<Longrightarrow>
    \<exists>pt. ko_at (ArchObj (PageTable pt)) p' s \<and> valid_vspace_obj (PageTable pt) s"
  apply (clarsimp simp: valid_table_caps_def simp del: split_paired_All)
  apply (erule allE)+
  apply (erule (1) impE)
  apply (clarsimp simp add: is_pt_cap_def cap_asid_def)
  apply (erule impE, rule refl)
  apply (clarsimp simp: obj_at_def empty_table_def)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> [implication lemma — assumption-weakening] conjunct `(caps_of_state s) p = Some (ArchObjectCap (arch_cap.PageTabl` head `caps_of_state` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`(caps_of_state s) p = Some (ArchObjectCap (arch_cap.PageTableCap p' None))`

**agent 论证**：The scanner notes `caps_of_state` never appears literally in the proof body; the expansion via valid_table_caps_def drives the argument. If valid_table_caps internally existentially quantifies over caps referencing p', the page_table_at witness alone may be enough to trigger the required obj_at (empty_table) fact without an explicit caps_of_state equality. Low confidence but costs only one trial.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(page_table_at p' s \<and> valid_table_caps s) \<Longrightarrow> (caps_of_state s p = Some (ArchObjectCap (arch_cap.PageTableCap p' None)) \<and> page_table_at p' s \<and> valid_table_caps s)

**delivery**：named，目标：Callers that hold valid_table_caps and page_table_at but not the specific cap equality；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
*** At command "apply" (line 721 of "/tmp/tmp.48scCQWWEi/Tmp_8e65e7223c09038d.thy")
*** Failed to apply proof method (line 721 of "/tmp/tmp.48scCQWWEi/Tmp_8e65e7223c09038d.thy"):
*** goal (1 subgoal):
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchKHeap_AI:valid_table_caps_ptD_no_cap" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
