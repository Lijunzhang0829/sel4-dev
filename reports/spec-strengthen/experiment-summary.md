# spec-strengthen 实验收尾总结(阶段性结论)

> 本文是 **LLM 主导的 seL4 spec 强化实验**的阶段性收尾:做了什么、产出多少、
> **为什么现在停**(meta-loop 已量化收敛)、留下什么可复用资产、以及空间在什么
> 条件下重开。配套细节见
> [experiment-workflow.md](experiment-workflow.md)(总图 + 模块链接)、
> [detector-vs-llm-methodology.md](detector-vs-llm-methodology.md)(三角色 + 两条硬拒绝)、
> [signal-proposals/MINING-LOG.md](signal-proposals/MINING-LOG.md)(meta-loop 逐轮)。
> 全程 **dry-run**:不写 l4v 源码,产出在 deliveries + ledger。日期:2026-06-29。

---

## 1. 一句话

把"扫描 → 起草 → 判真"沉淀成**可复现载体**:确定性 detector 给方向、LLM 给
statement、Isabelle trial 给真值;并在 meta-level 让 **LLM 挖掘新 detector 信号**、
确定性闸校准、git 留痕——使 detector **可演化**。两层都由 LLM 主导,确定性闸兜底
可控可复现。

**为什么不用纯 LLM 扫描**(承重位置需要采样器给不出的性质):可复现、可审计、
对类完备、昂贵 trial 闸下成本有界。详见 methodology。

---

## 2. 两层闭环

**object-level(每文件一轮,自动)**
```
detector(spec_slot_hints/frame_gap)→ LLM 起草(spec_agent, claude -p)
   → delivery 契约闸 → check-theory --patch(TRIAL,唯一真值)
   → spec_impact 裁决(无 weakening + ≥1 强化 + wall ≤ baseline×1.30)→ ledger 留痕
```

**meta-level(按需,手动触发,LLM 主导)**
```
读本地全样本(赢+输,失败只在本地)→ 按【原始 lemma】80/20 切 train/held-out
   → LLM 只看 train 提新信号 → 确定性校准(train/held-out/全集三跑)
   → 泛化裁决 → 人 review → 手写进 detector → commit
```

---

## 3. 产出(数字)

| 项 | 量 |
|---|---|
| object-level 运行 | **47 次**,覆盖 **38 个 theory**(整个 AInvs ARM frontier) |
| 验证过的强化交付包 | **21 个**(14 P-slot + 7 F-slot),全部 `trial_passed` |
| ledger 事件 | 469 条 append-only:163 discovered · 55 additive · 71 trial_failed · 7 noop · 1 weakening(全留痕,含失败) |
| AInvs frontier 闭合 | 39 run + 43 detector-barren + 非 ARM out-of-scope(VCPU/VSpaceLookup) |
| detector 信号 | 7 个生效(#7 comp-wp 由 meta-loop 挖出并 promote)+ 1 个被拒(automation,promote 时回归 `gts_wf`) |
| meta-loop 轮次 | **3 轮**(automation→拒 · comp-wp→promote · erule/elim→拒);held-out 泛化测试已建 |

交付包是自包含审计单元:`proposal.json` · `patch.diff` · `trial.log` ·
`p_claim_check.json` · `measurement.json` · `agent-raw.txt`(claude -p 全过程)·
`decision.md` · `ledger-events.jsonl`。

---

## 4. 收敛结论(为什么现在停)

收敛 = **"在当前数据快照 + 当前特征族下,LLM 连一个能在 held-out 上泛化的新信号都
提不出"**,由确定性闸判真、跨轮趋势确认——不是 LLM 自己宣布。

**精度方向 — 已收敛(硬判据)。** 三轮独立都收敛到同一形态"证明用了 tactic X →
降级"(automation → hypothesis-manip → erule/elim),且**都回归同一个 `gts_wf` 族
win**(它正好用这些 tactic,但前提仍可丢)。held-out 把它从"2 轮趋势"升级为硬判据:
没有任何 mined 精度信号在留出集上是零回归的。残差 loss 用**证明-tactic 类特征**和
win 不可分——它们贴在 **trial 的语义底**(只有 prover 能判)。

| Round 3 校准 | losses demoted | wins regressed | reg-free |
|---|---|---|---|
| TRAIN(LLM 见过) | 17/44 | 2 | False |
| **HELD-OUT** | 3/15 | 1 — `gts_wf'` | False |

**召回方向 — 已穷尽。** promote `compositional-wp`(信号#7)后,detector 排低的赢
(召回缺口)从 **5 → 1**,低于挖掘需要的 ≥3 下限——上一个 promote 的信号把它本要填
的缺口填掉了。

**收敛标量** = 最佳信号的 held-out demote/boost-recall;跨轮稳定 ≈0 ⇒ 该方向对此
数据快照穷尽。

---

## 5. 收敛后不做什么 / 做什么

**不做**(均为负 EV):
- 不再跑 miner —— 收敛即"挖不动",再跑反复重发现同一被拒信号,还担 claude -p 不稳成本。
- 不盲目用升级后的 detector 重扫 frontier —— comp-wp 是**排序信号(BOOST)**而非新增
  检测维度:43 个 barren 文件不会复活(连 unused-premise 都没命中),39 个已跑文件增量极小。

**重开空间的两条路**(留待后续,非本阶段):
1. **新 object-level 范围** —— 跑 AInvs 之外的新 session,产生新赢/输,残差变了 miner
   才挖得动(边界:ASpec 不处理、非 ARM out-of-scope)。
2. **换非-tactic 特征族** —— 现在三轮都困在"证明文本里的 tactic token";改从前提的
   语义角色 / 被调用规则签名类型等维度挖,可能绕开语义底。

---

## 6. 可复用资产

| 资产 | 位置 |
|---|---|
| detector(P/Q 扫描,纯 regex,7 信号) | [spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py) |
| frame-gap detector(F) | [spec_frame_gap.py](../../spec-strengthen/scripts/spec_frame_gap.py) |
| LLM 起草器(claude -p 流式 + 归档) | [spec_agent.py](../../spec-strengthen/scripts/spec_agent.py) |
| 裁决 + wall 闸 | [spec_impact.py](../../spec-strengthen/scripts/spec_impact.py) |
| **meta-loop 信号挖掘器(含 held-out 收敛测试)** | [spec_signal_miner.py](../../spec-strengthen/scripts/spec_signal_miner.py) |
| 主编排 | [strengthen.sh](../../spec-strengthen/strengthen.sh) |
| 账本(每候选一事件) | [candidate-ledger.jsonl](../../spec-strengthen/candidates/candidate-ledger.jsonl) |
| 交付包 + claude -p 全日志 | [deliveries/](../../spec-strengthen/deliveries/) |
| meta-loop 逐轮记录 + proposal 归档 | [signal-proposals/](signal-proposals/) |

**方法论沉淀**:detector 给方向 · LLM 给 statement · trial 给真值;meta-loop 与
object-level 同构(LLM 提方向 → 确定性闸判真 → git 留痕),把 LLM 的智能编码成**可
复现的 detector 信号**而非每轮蒸发的采样。

---

## 7. 全程纪律(继承 parent SKILL)

1. **无直接改源** —— 一切走 patch + check-theory 验;本实验全程 dry-run。
2. **不绕 prover** —— 无 sorry/oops/axiomatization;check-theory 是唯一闸。
3. **per-file wall 是真值**。
4. **heap 易变性** —— 绝不 pkill 活跃 Isabelle build。
5. **meta-loop 只 propose 不自动改 detector** —— promotion 经人 review;校准零误杀 win。
