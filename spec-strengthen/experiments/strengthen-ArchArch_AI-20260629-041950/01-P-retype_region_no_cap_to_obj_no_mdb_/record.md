# record — `retype_region_no_cap_to_obj_no_mdb`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/ARM/ArchArch_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=64932 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L481 之后 |
| 锚定的原 lemma | `retype_region_no_cap_to_obj` (L466) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma retype_region_no_cap_to_obj:
  "\<lbrace>valid_pspace and valid_mdb
             and caps_overlap_reserved {ptr..ptr + 2 ^ obj_bits_api ty us - 1}
             and caps_no_overlap ptr sz
             and pspace_no_overlap_range_cover ptr sz
             and no_cap_to_obj_with_diff_ref cap S
             and (\<lambda>s. \<exists>slot. cte_wp_at (\<lambda>c. up_aligned_area ptr sz \<subseteq> cap_range c \<and> cap_is_device c = dev) slot s)
             and K (ty = Structures_A.CapTableObject \<longrightarrow> 0 < us)
             and K (range_cover ptr sz (obj_bits_api ty us) 1) \<rbrace>
     retype_region ptr 1 us ty dev
   \<lbrace>\<lambda>rv. no_cap_to_obj_with_diff_ref cap S\<rbrace>"
  apply (rule hoare_gen_asm)+
  apply (simp add: no_cap_to_obj_with_diff_ref_null_filter)
  apply (wp retype_region_caps_of | simp)+
  apply fastforce
  done
```

## 2. 新增 lemma

```isabelle
lemma retype_region_no_cap_to_obj_no_mdb:
  "\<lbrace>valid_pspace
             and caps_overlap_reserved {ptr..ptr + 2 ^ obj_bits_api ty us - 1}
             and caps_no_overlap ptr sz
             and pspace_no_overlap_range_cover ptr sz
             and no_cap_to_obj_with_diff_ref cap S
             and (\<lambda>s. \<exists>slot. cte_wp_at (\<lambda>c. up_aligned_area ptr sz \<subseteq> cap_range c \<and> cap_is_device c = dev) slot s)
             and K (ty = Structures_A.CapTableObject \<longrightarrow> 0 < us)
             and K (range_cover ptr sz (obj_bits_api ty us) 1) \<rbrace>
     retype_region ptr 1 us ty dev
   \<lbrace>\<lambda>rv. no_cap_to_obj_with_diff_ref cap S\<rbrace>"
  apply (rule hoare_gen_asm)+
  apply (simp add: no_cap_to_obj_with_diff_ref_null_filter)
  apply (wp retype_region_caps_of | simp)+
  apply fastforce
  done
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=low
> conjunct `valid_pspace` head `valid_pspace` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `retype_region` is write (write/modify → premise usually load-bearing, demoted); wp-chain implicit use is the trial's job
> 目标前提：`valid_pspace`

**agent 论证**：The string `valid_mdb` never appears in the proof body of retype_region_no_cap_to_obj. The proof reduces to retype_region_caps_of (tracking null_filter changes to caps_of_state) plus fastforce. The retype_region_caps_of lemma concerns how retype changes caps_of_state, and requires pspace overlap properties (encapsulated in valid_pspace) but not MDB integrity. The `valid_mdb` conjunct is a write-op demoted hint but the proof is sufficiently short and opaque that a trial is warranted.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_pspace and valid_mdb
             and caps_overlap_reserved {ptr..ptr + 2 ^ obj_bits_api ty us - 1}
             and caps_no_overlap ptr sz
             and pspace_no_overlap_range_cover ptr sz
             and no_cap_to_obj_with_diff_ref cap S
             and (\<lambda>s. \<exists>slot. cte_wp_at (\<lambda>c. up_aligned_area ptr sz \<subseteq> cap_range c \<and> cap_is_device c = dev) slot s)
             and K (ty = Structures_A.CapTableObject \<longrightarrow> 0 < us)
             and K (range_cover ptr sz (obj_bits_api ty us) 1)) ==> (valid_pspace
             and caps_overlap_reserved {ptr..ptr + 2 ^ obj_bits_api ty us - 1}
             and caps_no_overlap ptr sz
             and pspace_no_overlap_range_cover ptr sz
             and no_cap_to_obj_with_diff_ref cap S
             and (\<lambda>s. \<exists>slot. cte_wp_at (\<lambda>c. up_aligned_area ptr sz \<subseteq> cap_range c \<and> cap_is_device c = dev) slot s)
             and K (ty = Structures_A.CapTableObject \<longrightarrow> 0 < us)
             and K (range_cover ptr sz (obj_bits_api ty us) 1)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `valid_mdb`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Future callers of retype_region_no_cap_to_obj that do not separately have valid_mdb outside valid_pspace；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

一次通过，无修复轮。

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
***                  cap_is_device c = dev)
***              (a, b) s)\<rbrakk>
***        \<Longrightarrow> valid_mdb s \<and>
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:ArchArch_AI:retype_region_no_cap_to_obj_no_mdb" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
