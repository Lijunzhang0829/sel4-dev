# LLM 主导的 seL4 spec 强化实验 — 总览

> 一份自包含的实验总结:**我们在做什么、为什么这么设计、做到了哪一步**;读到最后
> 若想知道**每个环节具体怎么实现**,§7 给出每个模块的核心代码与链接。
> 全程 **dry-run**(不写 l4v 源码,产出在 deliveries + ledger)。日期:2026-06-30。
>
> 延伸阅读:[detector-vs-llm-methodology.md](detector-vs-llm-methodology.md)(三角色分工 +
> 两条硬拒绝)· [experiment-workflow.md](experiment-workflow.md)(模块链接总图)·
> [signal-proposals/MINING-LOG.md](signal-proposals/MINING-LOG.md)(meta-loop 逐轮记录)。

---

## 1. 一句话

seL4 抽象规范的**公开承诺集**(所有可从 spec 派生的 Hoare 三元组定理)决定了下游
证明(Refine / CRefine / Access / InfoFlow)在 `wp` 阶段能用什么事实。**承诺越多、
越紧,下游证明的 `apply` 链越短、wall 越低、接口越清晰。** 本实验自动化地寻找**加性
强化**(additive strengthening):在不破坏旧承诺的前提下,给 spec 增添更弱前提 /
更强后置的新定理。

做法:让 **LLM 主导**这件事,但把每一步的智能**沉淀进可复现的确定性载体**——
确定性 detector 给方向、LLM 给 statement、Isabelle trial 给真值;再让 LLM 在
meta-level **挖掘新 detector 信号**,使 detector 可演化。

---

## 2. 项目结构

```
spec-strengthen/
├── strengthen.sh          ← 主编排:一个文件进,验证过的强化出(object-level 闭环)
├── scripts/
│   ├── spec_slot_hints.py     ← detector:P/Q 槽扫描(纯 regex,7 个信号)
│   ├── spec_frame_gap.py      ← detector:F 槽(frame-gap)扫描
│   ├── spec_agent.py          ← LLM 起草器(claude -p 流式 + 全程归档)
│   ├── spec_delivery_gate.py  ← 下游契约闸(零 build,死码先拒)
│   ├── spec_impact.py         ← 裁决器:additive / weakening / noop + wall 闸
│   ├── spec_signal_miner.py   ← meta-loop:LLM 挖 detector 信号 + held-out 收敛测试
│   └── …(probe / witness / lifecycle 等辅助)
├── candidates/candidate-ledger.jsonl   ← 账本:每候选一条 append-only 事件(含失败)
├── deliveries/            ← 交付包:21 个验证过的强化 + claude -p 全日志
├── experiments/          ← 47 次运行的原始 trace(meta-loop 的标注集来源)
└── README.md

reports/spec-strengthen/   ← 本目录:方法论、工作流、收敛记录、本总览
.claude/skills/isabelle_prover/scripts/check-theory.sh   ← trial:唯一真值 oracle
```

---

## 3. 为什么这么设计(理由与目的)

**目的**:把 seL4 spec 的承诺集做得更紧,从而降低下游证明的成本——这是工具链层面的
长期杠杆,不改证明内容、只增强它们能依赖的事实。

**为什么 LLM 主导,却不用"纯 LLM 扫描 + 改写"**:不是 LLM 能力不够,而是这个工作流的
**承重位置**需要采样器给不出的性质——

1. **可复现性** —— 纯 LLM 逐次输出会变,无法"跑一遍得到同样结果";detector 是确定
   性函数,同输入同输出。
2. **可控 / 可审计** —— 每个被接受的强化都要追溯到一个确定性原因(哪个信号、哪条前提),
   而不是"这次碰巧提到了"。
3. **完备性保证** —— detector 是 decision procedure,对一类结构**完备**(满足条件必报);
   采样器给不出"我没漏"。
4. **昂贵闸下成本有界** —— trial 是分钟级 build,LLM 的假阳性与"貌似合理"相关而非真值,
   会高信心地烧 build 预算;确定性前端的成本有界。

**结论**:承重的扫描交给确定性 detector;LLM 干它擅长的——**起草 statement、在 meta-level
总结有效信号指导 detector 演化**。这套分工的口诀是:

> **detector 给方向 · LLM 给 statement · trial 给真值。**

---

## 4. 实验的四个环节

整个系统是**两层同构的闭环**。object-level 每个文件自动跑一轮;meta-level 按需手动触发。

### 4.1 detector 扫描(给方向)

[spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py) /
[spec_frame_gap.py](../../spec-strengthen/scripts/spec_frame_gap.py) 纯静态扫描
lemma,**不调 prover**。加性强化的完整逻辑分解 = 三个槽:

| 槽 | "有潜力"的判据 |
|---|---|
| **P**(更弱前提) | 某前提 conjunct 的 head 在证明文本里**前缀匹配不到**、且不在后置(非 frame)→ 这个前提疑似多余、可删 |
| **Q**(更强后置) | 后置可被重定向到更强形式(redirect 四分类 + exactness) |
| **F**(frame) | post 缺了一个 frame conjunct(`pre P → post P`) |

P 槽下 7 个信号按严格优先级把候选排成 high/low(决定**值不值得花 trial**,不影响判真):
rule-precondition 依赖 → comp-wp idiom → 写类 op → differential。

### 4.2 LLM 起草增强(给 statement)

[spec_agent.py](../../spec-strengthen/scripts/spec_agent.py) 把 detector 的 hint +
源码喂给 `claude -p`(流式、禁工具、`--effort`),让它写出具体的新 lemma statement。
prompt 明确告诉它"**trial 才是裁判,别在脑子里证明可删性**"——LLM 负责创造,真假由
下一关定。每次调用的完整 NDJSON 过程都归档(`agent-raw.txt`),事后可审计。

### 4.3 脚本验证(给真值)

候选依次过四道闸,**廉价在前**:

1. **P-claim 机械验证**([spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py) `--check-p-claim`):新前提是旧前提的严格子集 + post 不变(零 build)。
2. **delivery 契约闸**([spec_delivery_gate.py](../../spec-strengthen/scripts/spec_delivery_gate.py)):死码在 build 前就拒(零 build)。
3. **TRIAL**([check-theory.sh](../../.claude/skills/isabelle_prover/scripts/check-theory.sh) `--patch`):在临时副本上真编译,**唯一真值 oracle**;失败可把 prover 错误回灌给 agent 修。
4. **impact 裁决 + wall 闸**([spec_impact.py](../../spec-strengthen/scripts/spec_impact.py)):结构化判 additive / weakening / noop,要求无 weakening + ≥1 强化,且 trial wall ≤ baseline×1.30。

全过才打包进 deliveries + 记 ledger。`--apply` 模式才会写源(本实验不用,保持 dry-run)。

### 4.4 LLM 指导 detector 更新(meta-level)

[spec_signal_miner.py](../../spec-strengthen/scripts/spec_signal_miner.py):object-level
跑出一批赢/输后,operator 觉得该升级 detector 时**手动**跑一次。

```
读本地全样本(experiments + ledger,含赢和输 —— 失败只在本地,不在 commit)
  → 按【原始 lemma】80/20 切 train/held-out(同一 lemma 的 drop 不跨界,防泄漏)
  → LLM 只看 train,提一个【现有信号没捕捉的】机械信号(结构签名 + 可执行 predicate)
  → 确定性校准:predicate 在 train / held-out / 全集三跑(硬门:零误杀 win)
  → 泛化裁决 → 人 review → 手写进 detector → commit(git 留痕)
```

它和 object-level **同构**,只是升了一层:**LLM 提方向(信号草案)→ 确定性闸判真
(校准)→ git 留痕(detector 改动可复现)**。纪律:**只 propose,不自动改 detector**;
promotion 经人 review;校准零误杀 win。

---

## 5. 现有成果

| 项 | 量 |
|---|---|
| object-level 运行 | **47 次**,覆盖 **38 个 theory** —— **整个 AInvs(ARM)frontier 已全跑一遍** |
| frontier 闭合 | 39 文件有候选并跑过 + 43 文件 detector-barren(无候选)+ 非 ARM(VCPU/VSpaceLookup)out-of-scope |
| 验证过的强化交付包 | **21 个**(14 P-slot + 7 F-slot),全部 `trial_passed` |
| 账本事件 | 469 条 append-only:163 discovered · 55 additive · 71 trial_failed · 7 noop · 1 weakening(含失败,全留痕) |
| detector 信号 | 7 个生效(#7 `compositional-wp` 由 meta-loop 挖出并 promote)+ 1 个被拒(`automation`,promote 时回归 `gts_wf`) |
| meta-loop | **3 轮**(automation→拒 · comp-wp→promote · erule/elim→拒);**held-out 泛化测试已建,精度方向量化收敛** |

**收敛**(为什么现在停):精度方向三轮独立都收敛到"证明用了 tactic X → 降级",且都
回归同一个 `gts_wf` 族 win —— held-out 测试证实残差 loss 用证明-tactic 特征和 win
**不可分**,它们贴在 trial 的语义底(只有 prover 能判)。召回方向也已穷尽(promote
comp-wp 后召回缺口 5→1)。**对当前数据快照,挖不出新的泛化信号**。重开空间需要新
object-level 数据或非-tactic 特征族。

每个交付包是自包含审计单元:`proposal.json` · `patch.diff` · `trial.log` ·
`p_claim_check.json` · `measurement.json` · `agent-raw.txt`(claude -p 全过程)·
`decision.md` · `ledger-events.jsonl`。

---

## 6. 全程纪律(继承 parent SKILL)

1. **无直接改源** —— 一切走 patch + check-theory 验;本实验全程 dry-run。
2. **不绕 prover** —— 无 `sorry/oops/axiomatization`;check-theory 是唯一闸。
3. **per-file wall 是真值**。
4. **heap 易变性** —— 绝不 pkill 活跃 Isabelle build。
5. **meta-loop 只 propose 不自动改 detector** —— promotion 经人 review,校准零误杀 win。

---

## 7. 具体怎么做的:每个模块的核心代码

> 想看实现细节的读者从这里入。每行给出**入口函数 + 链接**。

### 7.1 主编排 — [strengthen.sh](../../spec-strengthen/strengthen.sh)
一个 `.thy` 进,验证过的强化出。串起 scan → agent → gate → trial → impact →
(可选 apply),失败自动把 prover 错误回灌给 agent 做闭环修复。默认 dry-run。

### 7.2 detector(P/Q)— [spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py)
- [`scan_p`](../../spec-strengthen/scripts/spec_slot_hints.py#L463):P 槽主判定。前置过滤(has_assumes / <2 conjunct / opaque 一行证明)→ 对每个前提查 head 是否在证明体前缀匹配([:528](../../spec-strengthen/scripts/spec_slot_hints.py#L528))→ frame premise 豁免([:530](../../spec-strengthen/scripts/spec_slot_hints.py#L530))→ 7 信号定优先级([:560](../../spec-strengthen/scripts/spec_slot_hints.py#L560))。
- [`split_pre`](../../spec-strengthen/scripts/spec_slot_hints.py#L275):按 pred_conj 的 `and` 切前提,**不切** HOL `\<and>`(否则把 lambda 体撕成非法碎片 → 假候选)。
- [`is_compositional_wp`](../../spec-strengthen/scripts/spec_slot_hints.py#L446):meta-loop promote 的 #7 信号(`op_def`+`hoare_pre`+纯 wp 链 → boost)。

### 7.3 detector(F)— [spec_frame_gap.py](../../spec-strengthen/scripts/spec_frame_gap.py)
扫 post 缺失的 frame conjunct([`main`](../../spec-strengthen/scripts/spec_frame_gap.py#L398))。本实验 7 个 F-slot 交付出自这里。

### 7.4 LLM 起草器 — [spec_agent.py](../../spec-strengthen/scripts/spec_agent.py)
- [`build_prompt`](../../spec-strengthen/scripts/spec_agent.py#L222):把 hint + 源码 + 反内省指令组装成 prompt。
- [`run_claude_streaming`](../../spec-strengthen/scripts/spec_agent.py#L527):流式跑 claude -p,边跑边归档 NDJSON(可审计)。

### 7.5 下游契约闸 — [spec_delivery_gate.py](../../spec-strengthen/scripts/spec_delivery_gate.py)
[`main`](../../spec-strengthen/scripts/spec_delivery_gate.py#L334):零 build 把死码 / 形态不合规候选拦在 trial 前,省预算。

### 7.6 trial(唯一真值)— [check-theory.sh](../../.claude/skills/isabelle_prover/scripts/check-theory.sh)
`--patch` 在临时副本上真编译验证,`--apply` 才写源再复验。整个系统唯一的真假来源。

### 7.7 裁决器 — [spec_impact.py](../../spec-strengthen/scripts/spec_impact.py)
[`classify_delta`](../../spec-strengthen/scripts/spec_impact.py#L343):解析新旧 lemma,判 additive / weakening / noop;配合 wall 闸(trial wall ≤ baseline×1.30)。

### 7.8 meta-loop 信号挖掘器 — [spec_signal_miner.py](../../spec-strengthen/scripts/spec_signal_miner.py)
- [`collect_labeled_set`](../../spec-strengthen/scripts/spec_signal_miner.py#L112):从 experiments + ledger 重建赢/输标注集(含 applied 赢)。
- [`build_prompt`](../../spec-strengthen/scripts/spec_signal_miner.py#L287):注入现有信号 + 被拒信号,要 LLM 提"没被捕捉的"新信号。
- [`calibrate`](../../spec-strengthen/scripts/spec_signal_miner.py#L362):在标注集上跑 predicate,硬门=零误杀 win。
- [`split_train_test`](../../spec-strengthen/scripts/spec_signal_miner.py#L434) + [`generalization_verdict`](../../spec-strengthen/scripts/spec_signal_miner.py#L441):held-out 泛化测试 —— train 挖 / held-out 验,train-only 成功 = OVERFIT = 收敛证据。

### 7.9 账本与交付
- [candidate-ledger.jsonl](../../spec-strengthen/candidates/candidate-ledger.jsonl):每候选一条 append-only 事件(含失败,是 meta-loop 的负例来源)。
- [deliveries/](../../spec-strengthen/deliveries/):21 个自包含交付包(强化 + claude -p 全日志 + 验证证据)。
