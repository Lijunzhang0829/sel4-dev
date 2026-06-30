# LLM 主导的 seL4 spec 强化实验 — 总览

> 一份自包含的实验总结:**我们在做什么、为什么这么设计、做到了哪一步**;读到最后
> 若想知道**每个环节具体怎么实现**,§7 给出每个模块的核心代码与链接。
> 全程 **dry-run**(不写 l4v 源码,产出在 deliveries + ledger)。日期:2026-06-30。

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

实验主要针对下面三种类型的spec增强
| 槽 | "有潜力"的判据 |
|---|---|
| **P**(更弱前提) | 某前提 conjunct 的 head 在证明文本里**前缀匹配不到**、且不在后置(非 frame)→ 这个前提疑似多余、可删 |
| **Q**(更强后置) | 后置可被重定向到更强形式(redirect 四分类 + exactness) |
| **F**(frame) | post 缺了一个 frame conjunct(`pre P → post P`) |
P和Q的实现：[spec_slot_hints.py](../../spec-strengthen/scripts/spec_slot_hints.py) 
F的实现：[spec_frame_gap.py](../../spec-strengthen/scripts/spec_frame_gap.py) 
这些方法纯静态扫描lemma,**不调 prover**。

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

### 5.1 成功案例:两个 claude -p 交互实录

下面直接贴 claude -p 的交互日志(取自交付包的 `agent.log`),让读者看见"detector
给 hint → claude 思考 → 产出带 rationale 的候选 → trial 判真"这条链是怎么走的。

**案例 A — `get_simple_ko_valid_obj'`(read-op,删冗余前提)**

detector 注入 9 条 hint,claude 选中其中关于 `get_simple_ko_valid_obj` 的两条
(read 类 op `get_simple_ko`),先读证明体再下判断:

```text
$ claude -p <prompt:85768 chars> --model sonnet --tools "" --effort low
      --max-turns 6 --output-format stream-json --verbose
  ● session init: model=claude-sonnet-4-6 cwd=/tmp tools=0
  ◇ (thinking) Looking at the hints, I'll focus on the best 2 P-slot candidates.
      Hints 0 & 1 are about `get_simple_ko_valid_obj` (L346) - a READ op
      (get_simple_ko). Good candidates. The proof at L350-357: ...
  ▸ proposal:
      lemma get_simple_ko_valid_obj'[wp]:
        "⟨ valid_objs ⟩ get_simple_ko f ep ⟨ λr. valid_obj ep (f r) ⟩"
        apply (simp add: get_simple_ko_def) ...
      rationale: "get_simple_ko is a read/decode operation. If the object at ep
        does not satisfy bound (partial_inv f ko) then get_simple_ko assert-fails,
        making the Hoare triple vacuously true. When it succeeds, get_object_valid
        already supplies valid_obj ep ko ... no use of the obj_at conjunct is made."
  ✓ result: 1 turn, 104796ms $0.3585
[spec_agent] 1 proposal emitted
```

**结果**:trial 通过。原 lemma 的前提里有一个 `obj_at (bound ...) ep`,claude 论证
它在两种分支下都不被用到(失败分支 vacuous、成功分支由 `get_object_valid` 直接供给),
trial 在临时副本上真编译验证了这个判断 → 进交付包。

> 看点:claude 没有去"在脑子里证明",它给的是一个**可被证伪的结构论证**;真假由
> trial 定。这正是分工的样子。

**案例 B — `pd_at_asid_unique_no_vspace_objs`(differential 信号,一次产 3 个候选)**

这是一条蕴含式 lemma。detector 的 differential 信号发现:5 个前提 conjunct 在证明体里
可见被消费,唯独 `valid_vspace_objs` 从不出现 → high。claude 据此一次产出 3 个并行候选
(分别试删不同前提):

```text
  ◇ (thinking) Let me analyze the hints carefully and pick the 3 best candidates.
  ▸ proposal #1: pd_at_asid_unique_no_vspace_objs  (drop valid_vspace_objs)
      rationale: "The proof body of pd_at_asid_unique uses only valid_vs_lookupD,
        unique_table_refsD, and asid_low_high_bits. None of these require
        valid_vspace_objs. The scanner confirmed valid_vspace_objs never appears
        despite 5 other conjuncts being visibly consumed — strong differential."
  ▸ proposal #2: pd_at_asid_unique_no_global_objs  (drop valid_global_objs, 同构论证)
  ▸ proposal #3: lookup_pt_slot_is_aligned_no_global_objs (另一条 lemma 的并行删)
  ✓ result: 1 turn, 104020ms $0.3971
[spec_agent] 3 proposals emitted
```

**结果**:proposal #1、#2 trial 通过并进交付包。看点:detector 的 differential 信号
("别的前提都看得见被用,就这一个没有")给了一个**高可信方向**,claude 把它转成具体
statement 并复用同一证明体 —— 一个 hint 撬动多个强化。

### 5.2 失败案例分析:两类否决(trial 否决 + claude 起草前否决)

一个 detector 候选可能在**两个不同环节**被否决,两者都是宝贵的负信号:
- **trial 否决**(案例 C/D)—— claude 起草了,但 Isabelle 真编译失败。贵(花一次
  build),进 ledger 当负例,是 meta-loop 挖信号的主料。
- **claude 起草前否决**(案例 E)—— claude 读懂证明结构,判定某 hint 不可做,**根本
  没起草**。便宜(零 build),是 detector 假阳性的廉价过滤层。本实验 **78% 的 hint
  (360→78)是这样被滤掉的**;9 个 run 被全否决。

下面 C/D 两个 trial-failed 恰好对应 detector 现在的两个降级信号;E 是一个 claude
起草前否决,它直接**暴露并修掉了 detector 的一个解析 bug**。

**失败案例 C — 删 `valid_mdb`(write-op,信号 #4 的来源)**

`retype_region` 是 write 类 op。detector 的 hint 已经**把 `valid_mdb` 标成
write-op 降级(low)**;但 claude 看到证明体很短,赌了一把:

```text
  ▸ proposal: retype_region_no_cap_to_obj_no_mdb  (drop valid_mdb)
      proof: apply (rule hoare_gen_asm)+
             apply (simp add: no_cap_to_obj_with_diff_ref_null_filter)
             apply (wp retype_region_caps_of | simp)+ ; apply fastforce ; done
      rationale: "The string `valid_mdb` never appears in the proof body ...
        The `valid_mdb` conjunct is a write-op demoted hint BUT the proof is
        sufficiently short and opaque that a trial is warranted."
```

**trial 否决**(64.6s 后 FAILED):

```text
*** Failed to apply proof method (line 497):
*** goal (1 subgoal):
***  1. ⟦range_cover ...; valid_pspace s ∧ caps_overlap_reserved ... ∧ ...⟧
***       ⟹ valid_mdb s ∧ ...           ← 删掉的前提在目标里又冒出来了
```

> 分析:`valid_mdb` 虽然不在证明文本里出现,但 `retype_region_caps_of` 的 wp 链
> **隐形消费**了它(write op 改 caps_of_state,需要 MDB 完整性)。这正是 op 读/写
> 分类信号(#4)存在的理由:**写类 op 的前提通常承重,即使文本里看不见。** 这次失败
> 是该信号的实证来源之一(write-op 4/4 FAIL)。claude 明知信号降级仍试 → trial 兜底。

**失败案例 D — 删 `equal_kernel_mappings`(rule-precondition,信号 #6 的来源)**

这条更典型:claude **自己预判了会失败**,但因为一次 trial 只花一分钟,仍决定试:

```text
  ▸ proposal: lookup_pt_slot_ptes_aligned_valid_no_ekm  (drop equal_kernel_mappings)
      rationale: "equal_kernel_mappings does not appear textually in the proof body;
        it is forwarded implicitly to kernel_mapping_slots_empty_pdeI. HOWEVER
        kernel_mapping_slots_empty_pdeI at L481 explicitly lists
        equal_kernel_mappings as a hypothesis, so THIS DROP WILL LIKELY FAIL —
        but the trial cost is one build minute ... worth the trial."
```

**trial 否决**(38.2s 后 FAILED):目标里 `kernel_mapping_slots_empty_pdeI` 这一步
卡住,前提集里赫然缺了 `equal_kernel_mappings`。

> 分析:这条前提从不出现在证明文本,却被**喂进了一个被调用规则
> (`kernel_mapping_slots_empty_pdeI`)的前提**——经那条规则隐形消费,几乎必然承重。
> claude 在 rationale 里精确指出了这一点(甚至 cite 了规则定义的行号),却仍选择 trial。
> 这个观察后来被固化成 detector 的 **rule-precondition 依赖信号(#6)**:扫描时若发现
> 某前提的 head 喂了被调用规则的前提,直接降级。**一次失败 → 一条确定性信号**,下次任何
> 文件遇到同结构都免 trial 直接降级。

**失败案例 E — claude 起草前否决 `pspace_alignedE`(暴露 detector 解析 bug)**

在 `Invariants_AI` 上 detector 给了 12 条 hint,claude 逐条读后**一条没起草**(全否决)。
其中对 `pspace_alignedE`(源码标了 `[elim]`)的否决直指 detector 的一个 bug:

```text
源码:  lemma pspace_alignedE [elim]:
         "⟦ pspace_aligned s; x ∈ dom (kheap s);
            is_aligned x (obj_bits (the (kheap s x))) ⟹ R ⟧ ⟹ R"

detector hint:  删 `is_aligned x (obj_bits (the (kheap s x)))`(head 不在证明体)
claude (thinking,起草前):
  "The hint suggests dropping the `is_aligned x ...` conjunct, but that's
   actually the conclusion premise in this ELIMINATION rule. Without it you'd
   lose the alignment assumption entirely, making the rule unsound."
→ proposal: []   (空,零 trial)
```

> 分析:detector 把消去规则的小前提 `is_aligned x … ⟹ R`(一个嵌套蕴含)当成了
> 普通可删 conjunct——它按 `;` 切假设、取 head `is_aligned`、又因结论是裸变量 `R`
> 没匹配上而漏掉了 frame 豁免。这是一个**真实的解析缺陷**,而且关键:**任何基于 trial
> 的挖掘都永远发现不了它**——因为 claude 在 trial 前就把它滤掉了,它从不进 win/loss
> 标注集。这正是"claude 起草前否决"作为信号源的独特价值:它覆盖了 trial-mining 的盲区。
>
> **已修复并 smoke-test**(commit `ff95cbd`):新增 `_has_toplevel_imp()`——conjunct
> 在括号深度 0 含 `⟹` 即 higher-order 规则前提,`scan_p` 标 not-assessable、永不 flag。
> 用这些旧案例验证:全 invariant-abstract(81 文件)的此类假阳性 **2→0**
> (`pspace_alignedE` + `cap_master_cap_tcb_cap_valid_arch` 的条件前提);已知 win
> `pd_at_asid_unique` 仍正常 flag(无回归)。**一次 claude 否决 → 一个 detector 精度
> 修复**,与 C/D 的"trial 失败 → 信号"同构,只是更便宜、且够得到 trial 的盲区。

> 三个失败的共同教训:**"前提不在证明文本里"是必要非充分条件**——它可能被 wp 链
> (C)、被调用规则(D)、或消去规则结构(E)隐形承重。区分"真冗余"和"隐形承重"有两道
> 闸:**trial**(贵、唯一真值、喂 meta-loop)和 **claude 起草前推理**(便宜、过滤假
> 阳性、且能触达 trial 够不到的结构缺陷)。两者的失败都不是浪费——都被回灌成 detector
> 下一轮在花 trial 前的**预测能力**。
>
> ⚠ 但 claude 起草前否决有一个不对称:它**未经 trial 验证**。它拦下的假阳性通常对,
> 但若误杀一个真能删的前提(假阴性),因为没 trial 跑过,我们**不可见**。所以这 78%
> 过滤层是用召回换成本、且静默——抽样 trial 一部分被否决 hint 来量化 claude 假阴性率,
> 是一个待办的审计动作。

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
