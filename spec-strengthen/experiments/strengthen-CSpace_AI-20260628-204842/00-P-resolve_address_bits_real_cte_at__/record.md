# record — `resolve_address_bits_real_cte_at'`  (P-slot, TRIAL-FAILED)

| 字段 | 值 |
|---|---|
| 文件 | `proof/invariant-abstract/CSpace_AI.thy` |
| Slot / delivery | P / named (planned) |
| 裁决 | **TRIAL-FAILED** |
| 墙钟 | baseline=64020 ms · trial=? ms · Δ ?% |
| 锚点 | 插入于 L233 之后 |
| 锚定的原 lemma | `resolve_address_bits_real_cte_at` (L195) |

## 1. 原 lemma（additive：保持原样，未被修改）

```isabelle
lemma resolve_address_bits_real_cte_at:
  "\<lbrace> valid_objs and valid_cap (fst args) \<rbrace>
  resolve_address_bits args
  \<lbrace>\<lambda>rv. real_cte_at (fst rv)\<rbrace>, -"
unfolding resolve_address_bits_def
proof (induct args rule: resolve_address_bits'.induct)
  case (1 z cap cref)
  show ?case
    apply (clarsimp simp add: validE_R_def validE_def valid_def split: sum.split)
    apply (subst (asm) resolve_address_bits'.simps)
    apply (cases cap)
              defer 6 (* cnode *)
          apply (auto simp: in_monad)[11]
    apply (rename_tac obj_ref nat list)
    apply (simp only: cap.simps)
    apply (case_tac "nat + length list = 0")
     apply (simp add: fail_def)
    apply (simp only: if_False)
    apply (simp only: K_bind_def in_bindE_R)
    apply (elim conjE exE)
    apply (simp only: split: if_split_asm)
     apply (clarsimp simp add: in_monad)
     apply (clarsimp simp add: valid_cap_def)
    apply (simp only: K_bind_def in_bindE_R)
    apply (elim conjE exE)
    apply (simp only: split: if_split_asm)
     apply (frule (8) "1.hyps")
     apply (clarsimp simp: in_monad validE_def validE_R_def valid_def)
     apply (frule in_inv_by_hoareD [OF get_cap_inv])
     apply simp
     apply (frule (1) post_by_hoare [OF get_cap_valid])
     apply (erule allE, erule impE, blast)
     apply (clarsimp simp: in_monad split: cap.splits)
     apply (drule (1) bspec, simp)+
    apply (clarsimp simp: in_monad)
    apply (frule in_inv_by_hoareD [OF get_cap_inv])
    apply (clarsimp simp add: valid_cap_def)
    done
qed
```

## 2. 新增 lemma

```isabelle
lemma resolve_address_bits_real_cte_at':
  "\<lbrace> valid_cap (fst args) \<rbrace>
  resolve_address_bits args
  \<lbrace>\<lambda>rv. real_cte_at (fst rv)\<rbrace>, -"
unfolding resolve_address_bits_def
proof (induct args rule: resolve_address_bits'.induct)
  case (1 z cap cref)
  show ?case
    apply (clarsimp simp add: validE_R_def validE_def valid_def split: sum.split)
    apply (subst (asm) resolve_address_bits'.simps)
    apply (cases cap)
              defer 6 (* cnode *)
          apply (auto simp: in_monad)[11]
    apply (rename_tac obj_ref nat list)
    apply (simp only: cap.simps)
    apply (case_tac "nat + length list = 0")
     apply (simp add: fail_def)
    apply (simp only: if_False)
    apply (simp only: K_bind_def in_bindE_R)
    apply (elim conjE exE)
    apply (simp only: split: if_split_asm)
     apply (clarsimp simp add: in_monad)
     apply (clarsimp simp add: valid_cap_def)
    apply (simp only: K_bind_def in_bindE_R)
    apply (elim conjE exE)
    apply (simp only: split: if_split_asm)
     apply (frule (8) "1.hyps")
     apply (clarsimp simp: in_monad validE_def validE_R_def valid_def)
     apply (frule in_inv_by_hoareD [OF get_cap_inv])
     apply simp
     apply (frule (1) post_by_hoare [OF get_cap_valid])
     apply (erule allE, erule impE, blast)
     apply (clarsimp simp: in_monad split: cap.splits)
     apply (drule (1) bspec, simp)+
    apply (clarsimp simp: in_monad)
    apply (frule in_inv_by_hoareD [OF get_cap_inv])
    apply (clarsimp simp add: valid_cap_def)
    done
qed
```
（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的内容，原始补丁输入见 `range-patch.patch.txt`）

## 3. 为什么这么改

**检测器信号**（机械，`hints.json`）：kind=`unused-premise`，priority=high
> conjunct `valid_objs` head `valid_objs` never appears in the proof body (prefix match incl. _def/_E forms); 1 other conjunct(s) ARE visibly consumed — differential signal (0023 shape); op `resolve_address_bits` is read (read/decode → premise often droppable); wp-chain implicit use is the trial's job
> 目标前提：`valid_objs`

**agent 论证**：High-priority hint: the token `valid_objs` never appears literally in the proof body of resolve_address_bits_real_cte_at. The resolve_address_bits operation is a read/decode traversal. The only possible implicit consumer is the tactic `frule (1) post_by_hoare [OF get_cap_valid]` at line 225, where `get_cap_valid` has precondition `valid_objs`; however `get_cap_valid` is a named lemma reference, not a `valid_objs` reference — the trial determines whether the wp-chain forces the dependency. The proof body is copied verbatim; if `get_cap_valid` pulls in `valid_objs` as a subgoal the trial rejects this in under a minute.

**强化关系**（机械导出 ✓，`p_claim_check.json`）：(valid_objs and valid_cap (fst args)) ==> (valid_cap (fst args)) — the OLD pre strictly implies the NEW (weaker) pre; dropped conjunct(s): `valid_objs`. Reverse direction fails, making the weakening strict. [claim mechanically derived by spec_slot_hints --check-p-claim]

**delivery**：named，目标：Callers of resolve_address_bits_real_cte_at that do not have valid_objs in context (e.g. lookup_slot_real_cte_at_wp can be re-derived from this stronger variant). Future strengthening of resolve_address_bits_cte_at' can delegate here.；gate：named-planned accepted (provisional, 8-week grace)

## 4. 修改过程（试错链）

- **repair 1**：agent 依据 prover 错误修正 → anchor L?
- 最终结果：**TRIAL-FAILED**

## 5. 验证

**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：
```
FAILED (63119ms)
```
**最终裁决**：`TRIAL-FAILED`（ledger 终态事件：`discovered`）
**落地**：未落地（TRIAL-FAILED）；该目录无 `command.sh`（仅成功候选生成）——复现方式：用 `range-patch.patch.txt` 重跑 `check-theory.sh <theory> <session> --patch <该文件>`

## 6. 跨批次追踪

```
grep "P:CSpace_AI:resolve_address_bits_real_cte_at'" spec-strengthen/candidates/candidate-ledger.jsonl
```
（该 key 在 ledger 中暂无 delivery 状态记录）
