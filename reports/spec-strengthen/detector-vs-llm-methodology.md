# 检测器 vs 纯 LLM 扫描 — spec 强化的方法论主线与硬拒绝

> **一句话立场**:用 LLM 做 seL4 spec 强化,正确的分工不是"LLM 取代检测器",
> 也不是"检测器取代 LLM",而是**三个角色各就各位**——LLM 负责*创造*(发现新
> 槽类 + 起草强化语句),确定性检测器负责*执行*(可复现、对类完备的召回),
> Isabelle trial 负责*判真*。LLM 的两处创造都有外部验证(trial 证伪起草、
> 蒸馏沉淀发现);检测这个**承重位置**则结构性地不能交给 LLM。
>
> 本文是讲这个故事的底稿:给出三角色分工、当前已形式化的槽类 taxonomy、
> 新槽类从 LLM 发现到检测器固化的生命周期、检测器精度修复,以及"为什么不能
> 直接用 LLM 扫描"的**两条换模型也驳不倒的硬拒绝**。
>
> 承接 [`execute-additive-design.md`](execute-additive-design.md)(槽位×下游机制
> 的二维设计)与 [`qp-retrieval-upgrade.md`](qp-retrieval-upgrade.md)(检测层与
> 闭环修复的首次落地)。日期:2026-06-28。

---

## 1. Bottom line — 三个角色

把系统讲成**三种能力的分工**,而不是"检测器 vs LLM 之争":

| 角色 | 谁干 | 为什么是它 | 验证 |
|---|---|---|---|
| **发现新槽类** | LLM | 创造性、语义判断——找正则词表外的强化形态 | 蒸馏成检测器(§5) |
| **形式化/执行检测** | 确定性检测器 | 可复现、对类完备、可审计——**科学贡献本体** | 标注集 precision/recall |
| **起草强化语句+证明** | LLM | 创造性、但被 trial 当场证伪 | trial build |
| **判真** | Isabelle trial | 唯一真值 oracle | check-theory.sh |

> LLM 负责"创造"的地方(发现 + 起草),且这两处都有外部验证;检测器负责"承重"
> 的地方(检测),因为承重处要的是确定性。LLM 推进*科学*(扩张 taxonomy)和*生产*
> (起草),检测器是被固化的*知识*。

这与 [`execute-additive-design.md`](execute-additive-design.md) §6.3 的原始分工
("detector 给方向,agent 给 statement,trial 给真值")一脉相承,本文把它从一句
口号升级为一个**可讲、可辩护、可扩展**的方法论。

---

## 2. 为什么检测不能交给 LLM — 两条硬拒绝

软理由(漏召回、烧 build、不可复现、mode collapse)都会被一句"换个更强的模型 /
多跑几轮 / 改 prompt"驳回。要**结构性**的、换模型也不消失的理由。两条:

### 硬拒绝 1 — 检测是 decision procedure,不是 sampling

检测器对它定义的信号类是**完备**的:每一个"前提有未用 conjunct"的 lemma 都被
标记,确定性、可枚举、可审计。LLM scan 给你的是这个空间的一个**随机样本** +
**未校准的自报信心**,且"是否扫遍了每个 lemma×conjunct"**无法验证**。

一个形式化验证流水线的前端,必须是封闭、可复现、对类完备的——不能是一个随机
oracle。**这与模型强弱无关:采样器永远给不出"我没漏"的保证**,而检测的全部价值
恰恰在那个保证上。§3 的实测就是这个结构的投影——纯 LLM 系统性漏掉 `gts_wf`,
不是因为它笨,是因为叙事采样**结构上不枚举**。

### 硬拒绝 2 — 昂贵 soundness gate 下,随机前端的对抗成本无界

trial build(~60s/候选,Isabelle session 重建;实测 TcbAcc baseline 59.5s、
Untyped 66.6s)是唯一真值且贵。前端的唯一职责是 precision-at-recall 去**配给**
这个 oracle。

LLM 的假阳性与**可信度**相关、与**真值**无关——它**高信心地**提出不可证的
候选(§3 实测:删 `K (P ts)`,而 post `st_tcb_at P t` 里 P 还在),而"貌似对但
实际错"正是最坏情况:它精准地把 build 预算烧在不可证提议上。确定性检测器的假
阳性结构有界,且 `K`-guard 这类是**可证的跳过**(`head_ident` 落入 skip 集)。

所以同一个昂贵闸下,**随机前端成本无界,决定过程成本有界**。

### 合成(可引用)

> 检测是一个要求**确定性、对类完备、可审计**的承重位置,因为流水线的经济
> (配给昂贵的 trial)和它的科学主张(一个*可执行的强化槽刻画*)都依赖于此。
> LLM 在检测位置不是"较弱",而是**资质不符**——采样器给不出完备性保证,自报
> 信心给不出成本上界。所以把 LLM 放在它有验证的创造位置(起草→trial 证伪、
> 发现→蒸馏成检测器),把检测留给确定性前端。**贡献不是"LLM 找到了 N 条强化"
> (轶事),而是"强化槽的可执行刻画 + LLM 加速其扩张"(方法)。**

---

## 3. 经验证据 — 检测器 vs 纯 LLM 的对照实验

**方法**:取两个有 ground-truth applied 案例的文件,各跑修复后的机械检测器 +
一个无偏 LLM 扫描(不给 hints、不告知答案、读全文件,faithful 复刻 agent 任务)。

| 维度 | 固定检测器(修复后) | 纯 LLM |
|---|---|---|
| P ground truth `gts_wf`(删 `tcb_at t`→`gts_wf'`) | ✅ #1 high | ❌ **系统性漏** |
| Q ground truth `compute_free_index` exactness | ✅ #1 high | ✅ #1 high(还发现 `_exact` 已存在) |
| 假阳性 | `K`-guard 结构性跳过 | ❌ `sts_st_tcb_at'` 删 `K (P ts)`,标 **HIGH** 却**不可证** |
| 覆盖 | 175 lemma 全扫,确定性,毫秒 | "examined 76+/235",叙事**采样** |
| 语义新槽 | 盲(正则词表外) | ✅ 提出 `wellformed_cap`/分支特化 post |
| 可复现 | 是 | 否 |

**三个被源码确认的事实**:
1. `gts_wf: ⟨tcb_at t and invs⟩ get_thread_state t ⟨valid_tcb_state⟩` →
   `gts_wf': ⟨invs⟩ ...`(`TcbAcc_AI.thy:722,732`)——检测器 #1 命中,LLM 整个没列。
2. `sts_st_tcb_at': ⟨K (P ts)⟩ set_thread_state t ts ⟨λrv. st_tcb_at P t⟩`——
   post 里有 P,删 `K (P ts)` 必假;LLM 标 HIGH 信心,检测器 `head=K∈skip` 结构避开。
3. Q 侧两者都命中,但 LLM 多做了检测器做不到的事:**注意到强化版已存在**(去重)。

**读法**:实测同时印证了两条硬拒绝——LLM 在已知形状上**漏召回**(硬拒绝 1 的投影)
且在昂贵闸前**高信心烧预算**(硬拒绝 2 的投影);但 LLM 在**语义新槽**上确有检测器
够不到的发现力——这正是 §5 把它放在"发现"角色的依据。

---

## 4. 当前已形式化的 taxonomy

检测器 = [`spec-strengthen/scripts/spec_slot_hints.py`](../../spec-strengthen/scripts/spec_slot_hints.py)
(Q/P,纯 regex 零外部依赖)+ `spec_frame_gap.py`(F,仅用 `grep`)。截至本文:

| slot | 类 | 结构签名 | 状态 |
|---|---|---|---|
| **F** | frame-gap | op 改 state,但 "op 保字段 X" lemma 不存在 | 成熟批产 |
| **P** | unused-premise | 前提 conjunct head 在 proof body 零出现 + 差分证据 | 已破冰 `gts_wf'` |
| **Q** | redirect: inline-leading | 强 post 无名字、在前 1-2 tactic | 真槽 |
| **Q** | redirect: named | 强版本已具名 → alias | 低价值(已识别) |
| **Q** | redirect: interior | 描述内层 op,非本 lemma | 排除 |
| **Q** | exactness | post 仅承诺 `rv ≤ e`,op 实际定值 | 已破冰 `compute_free_index` |

redirect 四分类与 P 三条精度规则的来历见 [`qp-retrieval-upgrade.md`](qp-retrieval-upgrade.md)
——它们**本身就是从 live 样本里蒸馏出来的**,即 §5 回路的历史先例。

**待 discover 的空白**(LLM 已探到、检测器还没固化):`wellformed_cap` 式"证明中
临时建立的强属性未暴露"、`dui_sp_helper` 式"分支特化 post"。这两个是 §5 回路第 1 步
的现成输入。

---

## 5. 新槽类更新生命周期(LLM 发现 → 检测器固化)

这不是新编的——你们**现有的槽类就是这么诞生的**。`qp-retrieval-upgrade.md` 记着
"P precision rules learned from first live scan"、redirect 四分类是从 75 处
`hoare_strengthen_post*` live 样本里聚出来的。把这个已发生的过程**系统化**:

```
1. DISCOVER     LLM novel-slot pass(采样若干文件)→ 找出不匹配任何现有检测
                信号、却 trial-pass 的候选
2. VERIFY       每个过 Isabelle trial gate → 过滤掉 LLM 幻觉,只留真的
3. CHARACTERIZE 对 trial-pass 的新候选聚类 → 找共同的*结构签名*
                (它们语法上共享什么?这步把"轶事"变"类")
4. FORMALIZE    把签名写成 scanner(regex/AST)+ 精度规则
                (精度规则从假阳性里学,正如 P 的 前缀匹配/差分/排除opaque 三条)
5. CALIBRATE    在标注集上量 precision/recall,进 taxonomy
6. PROMOTE      新规则进确定性前端 → 此后全量 sweep 确定性命中该类,不再需 LLM
```

**复利点在第 6 步**:每跑一次发现回路,确定性前端的词表就宽一截,且永久、
可复现、零 LLM 依赖。LLM 的语义优势被一次性**蒸馏**成检测器资产——这就是
"LLM 辅助检测器演化"的合法机制:LLM 不是检测器的替代品,是它的**发现引擎**。

> 关键纪律:LLM 的输出(发现/起草)**永远经过验证才落地**——发现经 trial + 人工
> 聚类才成类,起草经 trial 才入源。检测器本身的改动由人 review(精度规则是人从
> 假阳性里读出来的),不是 LLM 自动改检测器。这保住了硬拒绝 1 的"可审计"。

---

## 6. 检测器精度修复(本轮,2026-06-28)

精度直接决定有多少 ~60s 的 build 预算被浪费在假阳性上(硬拒绝 2)。本轮对
`spec_slot_hints.py` 做了 7 项修复,全部单测验证,两个 ground truth 修复后仍命中:

| # | 缺陷 | 修法 |
|---|---|---|
| C1 | `split_pre` 把 lambda 内 `\<and>` 切碎(根因:`\band\b` 连 `\<and>` 里的 `and` 也匹配) | 改 `(?<!<)\band\b`,只在 pred_conj `and` 切 |
| A | `scan_p` 截断前不排序 → high P hint 被静默丢 | 加 priority sort,对齐 `scan_q` |
| B | frame-premise(head 在 post)虚假抬高 differential | 拆 `consumed`(免标记)vs `body_consumed`(差分证据),只数 body 可见 |
| E | 不可解析 lemma 静默跳过 → 假"已覆盖" | stderr 报 `N/M fact headers parsed` |
| F | `check_p_claim` 子集判定对改写脆弱 | `_normc` 先剥外层括号再去空白 |
| G | Isar `by` 行截断多行证明 | proof-block 深度跟踪,depth>0 不在 `by` 终止 |
| H | exactness 正则过窄 | 放宽到 `<`/`\<ge>`/`>` + 符号界,但拒收跨连接词的界 |
| I | Q 双扫描不去重 | 按 lemma 去重,保排序后第一条 |

横切根因:**检测器是单文件纯 regex,没有 Isabelle 的 term 结构**——lambda 作用域、
连接词层级、cartouche、locale 限定名都超出 regex 能力。这是刻意的成本取舍(检测要
免费),代价就是这串边界 case。仍未修(已记录,低频):theorem/cartouche/crunch 解析
(E 的观测行已暴露占比)。

---

## 7. 成本结构与范围

**范围**(由 session 推断锚定):`spec/abstract/`(ASpec,102 文件/21k LOC)+
`proof/invariant-abstract/`(AInvs,233 文件/175k LOC)= **335 文件 / ~196k LOC**。

**三层时间开销**——检测器刻意做成免费,真正的墙在下游:

| 阶段 | 单位成本 | 全量 |
|---|---|---|
| ① 检测器扫描 | ms/文件 | **~40s 全量**(其中绝大部分是 335 次进程冷启动;纯 regex 毫秒级) |
| ② LLM 起草(`claude -p`) | ~分钟/文件 | 取决于喂多少 |
| ③ trial build | **~60s/候选** | **成本墙** |

实测全量 sweep:40.4s,12466/13077 fact header 解析(95.3%),1543 hints
(808 high-priority)。**这印证了方法论的经济学**:扫描可忽略,成本全在 ③ 的
trial build——所以检测器精度(§6)= 直接的 build 预算效率,这正是硬拒绝 2 的落点。
若 808 high 全部进 trial ≈ 808 × 60s ≈ 13+ 小时纯 build,故确定性前端的结构性
预过滤(K-guard 等可证跳过)是**省钱的本体**,而非锦上添花。

---

## 8. 与历史 report 的关系 / 未来工作

- [`phase-1-summary.md`](phase-1-summary.md) / [`pattern-G-automation-pipeline.md`](pattern-G-automation-pipeline.md)
  ——上一代 pattern 字母(G/C/A)框架与 F 全自动化。
- [`execute-additive-design.md`](execute-additive-design.md)——抛弃 pattern 字母,
  确立 slot×delivery 二维 + "answer不上下游机制=不收"。本文的角色分工是它 §6.3 的展开。
- [`qp-retrieval-upgrade.md`](qp-retrieval-upgrade.md)——检测层(redirect 分类学 +
  P 差分信号)+ 闭环修复的首次落地,产出首批 Q/P applied。本文把那次"检测层补齐"
  抽象成可重复的 §5 生命周期,并补上换模型也驳不倒的 §2 硬拒绝。

**未来工作**(即 §5 回路的下一次迭代):把 LLM 已探到的 `wellformed_cap`/分支特化
post 两个候选走 DISCOVER→VERIFY→CHARACTERIZE→FORMALIZE,固化为第 7、8 个 Q 子类;
并把 commit/ledger 的 applied 案例作为 few-shot **只喂 LLM 的起草步**(提 trial-pass
率),**不喂发现步**(避免 F 主导导致的 mode collapse)。
