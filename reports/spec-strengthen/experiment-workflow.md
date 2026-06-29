# 实验设计与工作流 — LLM 主导的 seL4 spec 强化

> 本文是这个实验的总图:**它是什么、为什么这么设计、每个模块在哪**。每个模块
> 都用链接指向真实实现文件。配套阅读
> [detector-vs-llm-methodology.md](detector-vs-llm-methodology.md)(三角色分工 +
> 两条硬拒绝)与 [execute-additive-design.md](execute-additive-design.md)(槽×下游
> 二维设计)。日期:2026-06-29。

---

## 1. Thesis(一句话)

> **这是一个 LLM 主导、但把智能沉淀进可复现载体的 spec 强化实验。** LLM 不只在
> object-level 起草候选,更在 meta-level **指导一个确定性 detector 的演化**;
> detector + trial 保住可控、可复现、可判真。

**为什么不用纯 LLM 扫描+强化**(不是 LLM 能力不够,是工作流承重位置需要的性质
采样器给不出):
1. **可复现性** —— 纯 LLM 逐次变化,无法"跑一遍得到同样结果"。
2. **可控/可审计** —— 每个被接受的强化要追溯到确定性原因,而非"碰巧提了"。
3. **完备性保证** —— 检测是 decision procedure,对类完备;采样器给不出"我没漏"。
4. **昂贵闸下成本有界** —— trial 贵,LLM 假阳性与"貌似合理"相关而非真值,会高
   信心烧 build 预算;决定过程成本有界。

**结论**:承重的扫描用确定性 detector;LLM 干它擅长的——起草、过滤噪声、**总结
有效信号指导 detector 演化**。

---

## 2. Object-level 闭环(每文件一轮,自动)

```
检测器 → LLM 起草 → trial 判真 → impact 裁决 → ledger 留痕
```

主驱动:[strengthen.sh](../../spec-strengthen/strengthen.sh) 编排下面全部。

| 阶段 | 模块 | 角色 |
|---|---|---|
| 检测(给方向) | [spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py)(P/Q,纯 regex)· [spec_frame_gap.py](../../spec-strengthen/scripts/spec_frame_gap.py)(F) | 确定性、对类完备、可复现 |
| 起草(给 statement) | [spec_agent.py](../../spec-strengthen/scripts/spec_agent.py)(claude -p,流式、禁工具、--effort) | 创造性,被 trial 证伪 |
| 下游契约闸(零 build) | [spec_delivery_gate.py](../../spec-strengthen/scripts/spec_delivery_gate.py) | 死码 build 前拒收 |
| trial(判真) | [check-theory.sh](../../.claude/skills/isabelle_prover/scripts/check-theory.sh) | **唯一真值 oracle** |
| 裁决 + wall 闸 | [spec_impact.py](../../spec-strengthen/scripts/spec_impact.py) | 结构化判 additive/weakening/noop |
| 账本(留痕) | [candidate-ledger.jsonl](../../spec-strengthen/candidates/candidate-ledger.jsonl) | 每候选一条 append-only 事件 |
| 交付包 | [deliveries/](../../spec-strengthen/deliveries/) | 验证过的强化 + 完整 claude -p 日志 |
| 生命周期(周期) | [spec_delivery_lifecycle.py](../../spec-strengthen/scripts/spec_delivery_lifecycle.py) | planned→realized/orphan |

**分工口诀**:detector 给方向 · LLM 给 statement · trial 给真值。

---

## 3. 检测器信号 = 被固化的知识(object-level 的核心资产)

槽(P/Q/F)是 additive 强化的**完整逻辑分解,不增长**;真正演化的是槽内的
**机械信号**。全部在 [spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py):

| # | 信号 | 类型 | 来源 |
|---|---|---|---|
| 1 | unused-premise(head 不在证明文本) | P 召回 | 初版 |
| 2 | differential(其它 conjunct 可见消费 → high) | P 精度 | 初版 |
| 3 | frame-premise(head 也在 post → 永不 flag) | P 精度 | live |
| 4 | op 读/写分类(write 降级、read 保留) | P 精度 | live 案例 |
| 5 | implication scope(`⟦…⟧⟹C` 假设弱化) | P 召回 | discovery pass |
| 6 | rule-precondition 依赖(head 喂了被调用规则的前提 → 降级) | P 精度 | live 案例 |
| 7 | compositional wp-chain idiom(`op_def`+`hoare_pre`+纯 wp 链 → 升级) | P 召回 | **meta-loop 挖出 + 已 promote**(§4) |
| (拒) | 非约束自动化 tactic(blast/auto → 降级) | P 精度 | meta-loop 挖出但 **promote 时回归 gts_wf,已拒**([MINING-LOG](signal-proposals/MINING-LOG.md)) |

Q 侧:redirect 四分类 + exactness(`scan_q`/`scan_q_exactness`)。F 侧:frame-gap。

---

## 4. Meta-level 闭环(按需,手动触发,LLM 主导)

> object-level 跑完若干轮后,operator 觉得该升级 detector 时**手动**跑一次。
> "何时升级"由人定(没有好的自动启发式);"怎么升级"由 LLM 主导。

模块:[spec_signal_miner.py](../../spec-strengthen/scripts/spec_signal_miner.py)

```
[1] 读本地全样本(experiments/ + ledger,含赢和输 —— 失败只在本地,不在 commit)
      ↓  每候选 = 特征 + win/loss + (失败的)trial 错误 + agent 自己的推理
[2] LLM 引导(claude -p):提一个【现有信号没捕捉的】机械信号,分开 loss/win
      ↓  输出:结构签名 + 可执行 predicate
[3] 确定性校准(不是 LLM 说了算):predicate 在标注集上跑
      ↓  硬门:零误杀 win;报 precision/recall
[4] 产出 proposal 报告(signal-proposals/<ts>.md)—— 【不自动改 detector】
      ↓
[5] 人/LLM review → 手写进 spec_slot_hints.py → commit(留 git 痕迹)
```

产出目录:[signal-proposals/](signal-proposals/)。

**为什么这个设计**:它和 object-level 同构,只是升了一层——
**LLM 提方向(信号草案)→ 确定性闸把真值(校准)→ git 留痕(detector 改动可复现)**。
LLM 的智能被编码成**可复现的 detector 信号**,而非每轮蒸发的采样。

**纪律**(保住可控性,即使 LLM 主导):
- 只 propose,**不自动改 detector**;promotion 经人 review。
- 校准是**确定性**的:新信号必须**零误杀已知 win**才可 promote。
- 读**本地日志**(experiments + ledger),不读 git —— **失败案例(精度信号的负例
  来源)只在本地**。
- 挖 agent 自己的推理 trace(`agent-raw.txt`):很多结构洞察 agent 当时就写了
  (例:它预判过"某规则显式列了这个前提 → 大概率失败")。

**首次实跑验证**(2026-06-29):14 赢/59 输 → LLM 提出"非约束自动化 tactic"信号
(正交于现有 6 个,看证明策略而非前提)→ 校准 **降级 15/59 输、误杀 0 赢 →
PROMOTABLE**。见 [signal-proposals/](signal-proposals/)。

---

## 5. 验收门(object-level,全过才 accept/apply)

按执行顺序、廉价在前:
1. **slot 纪律** + **malformed 过滤**([spec_agent.py](../../spec-strengthen/scripts/spec_agent.py),零 build)
2. **P-claim 机械验证**([spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py) `--check-p-claim`:严格子集 + post 相同)
3. **delivery 契约闸**([spec_delivery_gate.py](../../spec-strengthen/scripts/spec_delivery_gate.py),零 build)
4. **TRIAL**([check-theory.sh](../../.claude/skills/isabelle_prover/scripts/check-theory.sh) `--patch` → OK;**唯一真值**;失败可 repair)
5. **impact 裁决 + wall 闸**([spec_impact.py](../../spec-strengthen/scripts/spec_impact.py):无 weakening + 至少一个强化 verdict;trial wall ≤ baseline×1.30)
6. **APPLY**(仅 `--apply`:写源前 [check-theory.sh](../../.claude/skills/isabelle_prover/scripts/check-theory.sh) `--apply` 再 build 复验)

> 注:本实验全程 **dry-run**(不写 l4v 源码);强化在 deliveries + ledger,源码改动
> 留给 PR(隔离 clone / worktree,见会话记录)。

---

## 6. 五条硬规则(继承 parent SKILL,全程)

1. **无直接改源** —— 一切走 patch + [check-theory.sh](../../.claude/skills/isabelle_prover/scripts/check-theory.sh) 验/applied。
2. **不绕 prover** —— 无 `sorry/oops/axiomatization`;check-theory.sh 是唯一闸。
3. **per-file wall 是真值**。
4. **heap 易变性** —— 绝不 pkill 活跃 Isabelle build。
5. **PR 主线** —— 每个 applied 经 topic 分支 + PR 进 main;每候选恰好一条 ledger 事件。

---

## 7. 数据流总图

```
            ┌─────────────────── object-level(每文件,自动)────────────────────┐
 .thy 源码 → │ spec_slot_hints/frame_gap → spec_agent(claude -p) → delivery_gate │
            │      ↓hints                    ↓proposals             ↓accept       │
            │                         check-theory --patch (TRIAL) → spec_impact   │
            │                                ↓OK/FAIL                ↓verdict      │
            └──────────────────────────── ledger + deliveries ───────────────────┘
                                              │ 本地全样本(赢+输+agent推理)
                                              ↓
            ┌──────────────── meta-level(按需,手动,LLM 主导)──────────────────┐
            │ spec_signal_miner: 读 ledger/experiments → claude -p 提信号 →      │
            │     确定性校准(零误杀 win)→ signal-proposals/<ts>.md →           │
            │     人 review → 手写进 spec_slot_hints.py → commit                 │
            └───────────────────────────────────────────────────────────────────┘
                          ↑ 升级后的 detector 回到 object-level,下一轮更准
```

闭环:**object-level 产生赢/输 → meta-level 提炼信号升级 detector → 下一轮 object-level
更全面(召回)/更少噪声(精度)**。LLM 主导两层,确定性闸 + git 兜底可控可复现。
