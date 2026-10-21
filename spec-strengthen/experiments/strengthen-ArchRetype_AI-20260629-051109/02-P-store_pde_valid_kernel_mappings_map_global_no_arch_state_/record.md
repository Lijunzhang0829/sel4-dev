# record — `store_pde_valid_kernel_mappings_map_global_no_arch_state`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchRetype_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=78972 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L325 之后 |
| 锚定的原 lemma | `store_pde_valid_kernel_mappings_map_global` (L312) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma store_pde_valid_kernel_mappings_map_global:
  "\<lbrace>valid_kernel_mappings and valid_arch_state and valid_global_objs
     and K (VSRef (p && mask pd_bits >> 2) (Some APageDirectory)
                \<in> kernel_vsrefs)
     and (\<lambda>s. \<forall>p. pde_ref pde = Some p
             \<longrightarrow> p \<in> set (arm_global_pts (arch_state s)))\<rbrace>
     store_pde p pde
   \<lbrace>\<lambda>rv. valid_kernel_mappings\<rbrace>"
  apply (simp add: store_pde_def)
  apply (wp set_pd_valid_kernel_mappings_map)
  apply (clarsimp simp: obj_at_def)
  apply (rule conjI, rule glob_vs_refs_add_one')
  apply (clarsimp simp: ucast_ucast_mask_shift_helper)
  done
```

## 2. 新增 lemma

```isabelle
lemma store_pde_valid_kernel_mappings_map_global_no_arch_state:
  "\\<lbrace>valid_kernel_mappings and valid_global_objs
       and K (VSRef (p \\<and>\\<and> mask pd_bits >> 2) (Some APageDirectory)
                  \\<in> kernel_vsrefs)
       and (\\<lambda>s. \\<forall>p. pde_ref pde = Some p
               \\<longrightarrow> p \\<in> set (arm_global_pts (arch_state s)))\\<rbrace>
       store_pde p pde
     \\<lbrace>\\<lambda>rv. valid_kernel_mappings\\<rbrace>"
  apply (simp add: store_pde_def)
  apply (wp set_pd_valid_kernel_mappings_map)
  apply (clarsimp simp: obj_at_def)
  apply (rule conjI, rule glob_vs_refs_add_one')
  apply (clarsimp simp: ucast_ucast_mask_shift_helper)
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `valid_arch_state` head `valid_arch_state` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `store_pde` is write (write/modify → premise usually load-bearing, demoted); wp-chain implicit use is the trial's job
> 目标前提：`valid_arch_state`

**agent 论证**：The proof of store_pde_valid_kernel_mappings_map_global never mentions `valid_arch_state` by name; the tactic chain is `wp set_pd_valid_kernel_mappings_map; clarsimp; rule glob_vs_refs_add_one'; clarsimp ucast_ucast_mask_shift_helper`. None of those steps reference `valid_arch_state`. The scanner notes NO conjunct visibly consumed and the op is a write-type (demoted priority), but the proof transparency here is higher than hints 8/9 — worth a trial.

**强化关系**（机械验证未通过：post differs from the hinted lemma — not a pure premise-weakening；以下为 agent 自述）：(valid_kernel_mappings and valid_global_objs and K (...) and <lambda>) <= (valid_kernel_mappings and valid_arch_state and valid_global_objs and K (...) and <lambda>)

**delivery**：named，目标：future consumer: copy_global_invs_mappings_restricted wp chain for store_pde_valid_kernel_mappings_map_global；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (22584ms)
*** Malformed command syntax
*** At command "<malformed>" (line 327 of "/tmp/tmp.9KUt3UqdYz/Tmp_047d8e0bd52539d9.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchRetype_AI:store_pde_valid_kernel_mappings_map_global_no_arch_state" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
