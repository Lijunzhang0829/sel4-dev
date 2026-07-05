# record — `store_pde_global_objs_no_arch_state`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchRetype_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=78972 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L309 之后 |
| 锚定的原 lemma | `store_pde_global_objs` (L296) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma store_pde_global_objs[wp]:
  "\<lbrace>valid_global_objs and valid_global_refs and
    valid_arch_state and
    (\<lambda>s. (\<forall>pd. (obj_at (empty_table (set (second_level_tables (arch_state s))))
                   (p && ~~ mask pd_bits) s
           \<and> ko_at (ArchObj (PageDirectory pd)) (p && ~~ mask pd_bits) s
             \<longrightarrow> empty_table (set (second_level_tables (arch_state s)))
                                 (ArchObj (PageDirectory (pd(ucast (p && mask pd_bits >> 2) := pde))))))
        \<or> (\<exists>slot. cte_wp_at (\<lambda>cap. p && ~~ mask pd_bits \<in> obj_refs cap) slot s))\<rbrace>
     store_pde p pde \<lbrace>\<lambda>rv. valid_global_objs\<rbrace>"
  apply (simp add: store_pde_def)
  apply wp
  apply clarsimp
  done
```

## 2. 新增 lemma

```isabelle
lemma store_pde_global_objs_no_arch_state[wp]:
  "\\<lbrace>valid_global_objs and valid_global_refs and
    (\\<lambda>s. (\\<forall>pd. (obj_at (empty_table (set (second_level_tables (arch_state s))))
                     (p \\<and>\\<and> \\<not>\\<not> mask pd_bits) s
             \\<and> ko_at (ArchObj (PageDirectory pd)) (p \\<and>\\<and> \\<not>\\<not> mask pd_bits) s
               \\<longrightarrow> empty_table (set (second_level_tables (arch_state s)))
                                   (ArchObj (PageDirectory (pd(ucast (p \\<and>\\<and> mask pd_bits >> 2) := pde)))))
          \\<or> (\\<exists>slot. cte_wp_at (\\<lambda>cap. p \\<and>\\<and> \\<not>\\<not> mask pd_bits \\<in> obj_refs cap) slot s))\\<rbrace>
     store_pde p pde \\<lbrace>\\<lambda>rv. valid_global_objs\\<rbrace>"
  apply (simp add: store_pde_def)
  apply wp
  apply clarsimp
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `valid_global_refs` head `valid_global_refs` never appears in the proof body (prefix match incl. _def/_E forms); NO conjunct visibly consumed — proof likely opaque, weak signal; op `store_pde` is write (write/modify → premise usually load-bearing, demoted); wp-chain implicit use is the trial's job
> 目标前提：`valid_global_refs`

**agent 论证**：Same lemma and same proof body as hint 8: `valid_arch_state` never appears by name in the proof. The `second_level_tables (arch_state s)` is already threaded through the complex lambda conjunct, so no separate `valid_arch_state` predicate is needed for the wp/clarsimp chain. Dropping it yields a stronger triple; trial build decides.

**强化关系**（机械验证未通过：post differs from the hinted lemma — not a pure premise-weakening；以下为 agent 自述）：(valid_global_objs and valid_global_refs and <lambda>) <= (valid_global_objs and valid_global_refs and valid_arch_state and <lambda>)

**delivery**：named，目标：future consumer: copy_global_invs_mappings_restricted and similar callers could drop valid_arch_state from store_pde_global_objs precondition；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (23051ms)
*** Malformed command syntax
*** At command "<malformed>" (line 311 of "/tmp/tmp.hNB7GAyzny/Tmp_96e30109e4c15783.thy")
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchRetype_AI:store_pde_global_objs_no_arch_state" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
