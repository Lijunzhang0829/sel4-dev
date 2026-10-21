# record — `valid_vspace_obj'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchDetype_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=39565 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L310 之后 |
| 锚定的原 lemma | `valid_vspace_obj` (L302) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma valid_vspace_obj:
  "\<And>ao p. \<lbrakk> valid_vspace_obj ao s; ko_at (ArchObj ao) p s; (\<exists>\<rhd>p) s \<rbrakk> \<Longrightarrow>
       valid_vspace_obj ao (detype (untyped_range cap) s)"
  apply (case_tac ao; simp; erule allEI ballEI; clarsimp simp: ran_def;
         drule vs_lookup_pages_vs_lookupI)
  subgoal for p t r ref i by (crush i)
  subgoal for p t i ref by (cases "t i"; crush i)
  subgoal for p t i ref by (cases "t i"; crush i)
  done
```

## 2. 新增 lemma

```isabelle
lemma valid_vspace_obj':
  "\<And>ao p. \<lbrakk> valid_vspace_obj ao s; (\<exists>\<rhd>p) s \<rbrakk> \<Longrightarrow>
       valid_vspace_obj ao (detype (untyped_range cap) s)"
  apply (case_tac ao; simp; erule allEI ballEI; clarsimp simp: ran_def;
         drule vs_lookup_pages_vs_lookupI)
  subgoal for p t r ref i by (crush i)
  subgoal for p t i ref by (cases "t i"; crush i)
  subgoal for p t i ref by (cases "t i"; crush i)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> [implication lemma — assumption-weakening] conjunct `ko_at (ArchObj ao) p s` head `ko_at` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `` is unknown; wp-chain implicit use is the trial's job
> 目标前提：`ko_at (ArchObj ao) p s`

**agent 论证**：The scanner reports ko_at never appears in the proof body. The proof drives entirely through vs_lookup_pages_preserved via the crush private method, which consumes the `(\<exists>\<rhd>p) s` witness; ko_at is not passed to any rule in the tactic chain. The new lemma is placed inside the same `context begin` block (L285–312) so the private `crush` method is still in scope.

**强化关系**（机械验证未通过：could not parse a Hoare pre；以下为 agent 自述）：(valid_vspace_obj ao s \<and> (\<exists>\<rhd>p) s) ==> (valid_vspace_obj ao s \<and> ko_at (ArchObj ao) p s \<and> (\<exists>\<rhd>p) s)

**delivery**：named，目标：valid_vspace_obj_detype or any caller that can supply a vs_lookup witness but not a concrete ko_at for the page-structure object；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***      r \<in> untyped_range cap; (ref \<unrhd> p) s\<rbrakk>
***     \<Longrightarrow> False
*** At command "by" (line 317 of "/tmp/tmp.8j4q07yeEG/Tmp_e45e2c12066aa171.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchDetype_AI:valid_vspace_obj'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
