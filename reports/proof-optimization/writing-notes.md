# seL4 proof 阶段论文写作方向报告

**日期**: 2026-06-30  
**目的**: 把本轮讨论沉淀成一份可复用的写作备忘，统一回答：

1. 论文主线应该怎么讲；
2. 现有 report 已经支持哪些论点；
3. 现在的做法会不会被挑刺；
4. 为什么最初聚焦 lemma 的 search 角度；
5. proof 优化是否还有其他角度，例如减少 lemma step。

---

## 1. 一句话定位

我们当前最稳的写法不是：

> “LLM 让 seL4 proof wall 普遍下降”

而是：

> “LLM 对 seL4 proof 的**局部优化能力**已经被验证，但端到端 wall 优化受限于
> 关键路径位置和非 search 型成本；因此 proof 优化的价值在于**能力展示、
> 边界刻画、以及 bottleneck-aware 的平台设计**。”

这条主线既保留了项目初衷（LLM 优化 seL4），也诚实反映了现有证据（proof 线有正例，
但 wall 不好动）。

---

## 2. 当前最适合的论文叙事

### 2.1 不要把论文缩成“为什么 wall 优化不动”

如果只讲 wall 不动，论文会从“LLM for seL4 optimization”滑向“seL4 proof cost
diagnosis”，主题会变窄，而且会掩盖你们已经做出的 LLM proof rewrite 能力。

更稳的框架是“两层结论”：

1. **能力层**：LLM 确实能改写 seL4 proof tactic，能做 staticize / reduce / rewrite，
   并且能通过 build gate 验证正确性。
2. **边界层**：这些局部改写并不自动转化为 build wall 改善，因为主导成本常常不在
   LLM 这层可处理的 search overhead 上。

也就是：

> **LLM can optimize proof commands, but proof-wall speedup is bottlenecked elsewhere.**

### 2.2 最合适的中心论点

目前最好的中心论点是：

> **LLM-driven proof optimization in seL4 has a real but narrow operating regime.**

展开后可分成三条：

1. LLM 能成功完成 proof command 的局部改写；
2. “search elimination / degradation / reduction” 三条 proof 优化路中，只有
   reduction 稳定产生正例；
3. 端到端 wall 主要由关键路径 session 和非 search 工作量支配，因此 lemma-level
   成功不必然反映到 build-wall。

### 2.3 与平台初衷如何对齐

这并没有偏离“用 LLM 做 seL4 优化平台”的初衷，而是在**校准平台该优化什么**：

- 在 `spec` 上，LLM 有较大、较稳定的强化空间；
- 在 `proof` 上，LLM 更像是**局部 proof optimizer / rewrite proposer**，
  而不是普适的 wall optimizer；
- 因此平台不应写成“LLM 自动降低 wall”，而应写成：
  **先识别瓶颈，再决定 LLM 在哪一层介入。**

### 2.4 目前真正需要强证据回答的问题

如果把 proof 线的写作问题全部收束一下，实际上只剩下一类问题必须靠强实证回答：

> **为什么 lemma-level 的成功 proof rewrite，没有稳定转化成 build wall 改善？**

这是核心科学问题，因为它决定论文主结论是否站得住。

其他问题虽然重要，但更多是**framing / scope 问题**，例如：

- 为什么 proof 优化要以 wall 为目标；
- proof 优化能不能看别的指标；
- 为什么先从 search 角度切入；
- step reduction / Isar 化为什么没被放到主线。

这些问题不一定需要大规模新实验，更多需要在论文里**主动交代研究目标与范围**。

---

## 3. 为什么本文先看 wall

这一节非常值得在论文里显式出现，因为它能主动化解“为什么只看 wall”的质疑。

### 3.1 不是说 proof 优化只能看 wall

首先必须明确：

> `wall` 不是 proof 优化唯一合法的目标。

proof 优化完全可以有别的目标，例如：

- 更低的 verification wall time；
- 更低的 proof-checking CPU 成本；
- 更少的 proof step；
- 更强的可读性与 maintainability；
- 更高的可审计性；
- 更适合作为 retrieval / few-shot 语料。

因此，不能把“proof optimization”与“wall optimization”画等号。

### 3.2 为什么这篇工作仍把 wall 放在中心

虽然不是唯一目标，但 wall 是目前最自然的中心指标，因为：

1. **它最贴近 proof engineering cost。**
   我们真正关心的是：seL4 的 proof 在工程上有没有更便宜、更快地被重检。
2. **它最贴近平台初衷。**
   如果说这是一个 “LLM 优化 seL4” 平台，那么 verification cost 是最直接的优化对象。
3. **它最容易与 build/session 级别成本接轨。**
   wall 能把单 lemma 改写和系统级重建代价联系起来，而不是只停留在 proof style 上。

因此更准确的说法是：

> 本文聚焦 wall，不是因为别的指标不重要，而是因为我们在这篇工作里优先研究
> **verification cost**。

### 3.3 这篇工作不否认其他目标

为了避免读者误解，论文里应主动写明：

> 我们的负结果是**针对 wall-oriented proof optimization** 的，
> 并不排除在 maintainability、normalization、Isar recovery、proof-style quality
> 等其他指标上存在更大的收益。

换句话说：

- 如果目标是 `wall / cost`，那么当前结果是“LLM 有局部能力，但 wall 受更深层瓶颈支配”；
- 如果目标是 `maintainability / style`，那么 step reduction、Isar 化、proof normalization
  反而可能是更自然的主线。

---

## 4. 现有材料已经支持什么

本节只总结已经有的证据，不再重复细节。

### 4.1 search 角度的 proof 优化尝试已经相当完整

来自 [search-angle-optimization/REPORT.md](1-modify-lemma/REPORT.md) 和
[proof-staticize-archive/](archive/) 的内容，已经覆盖了三条几乎穷尽
“proof search 优化”的路线：

1. **eliminate**：把 automation tactic 完全换成静态 rule-chain；
2. **degrade**：把 `auto/fastforce/force` 降成 `clarsimp`；
3. **reduce**：保留 tactic，只缩小搜索空间，如 `simp only:`、`simp del:`、
   定向 facts、分支换序。

这意味着：如果后面有人质疑“为什么只看一种 proof 优化”，我们其实可以回答：

> 在 search 这一大类里，我们已经系统扫过三条主路线，而不是只试了一个技巧。

### 4.2 “LLM 能改 proof”已有正面证据

现有材料已经足够支撑下列写法：

- LLM 不只是提出 theorem 或 hint，它能直接参与 tactic rewrite；
- LLM 可以找到 DFS / 固定菜单漏掉的 rewrite；
- LLM + build gate + transcript 形成了可审计改写闭环；
- 在 reduction 路线上，已有一批 build-verified 正确改写；
- 至少若干案例有比较扎实的速度改进证据，尤其是
  `empty_slot_pas_refined:1103`。

所以 proof 线不能写成“没做成”，而应写成：

> **proof 层 LLM 改写能力已经被证明存在。**

### 4.3 “为什么 wall 不动”也已有大量证据

来自 [proof-structural-optimization/REPORT-2026-06-26.md](2-modify-structure/REPORT-2026-06-26.md)
与 search-angle report 的联合证据，已经能支撑：

- 关键路径是 CRefine/CRefineSyscall 一类 session；
- 很多可改写成功的 lemma 位于并行旁支，省掉的 CPU 会被并行 slack 吸收；
- CRefine 的主要成本大量来自 `simp` work、VCG、ccorres machinery，不只是 classical search；
- 结构性杠杆（threads / parallel_proofs / parent-swap / session 冗余）基本已被摸透，
  真正还能动的大杠杆很少。

所以“wall 不动”不是一句观察，而是已有一整套可引用的证据链。

---

## 5. 现在的写法会不会被挑刺

会，主要有三类挑刺，而且都值得提前在文中主动回应。

### 5.1 挑刺一：为什么只选 lemma 的 search 角度去优化

这是最自然的质疑。

回应不能是“因为我们手头工具擅长这个”，而要写成：

1. **search 角度是最自然的第一步**。因为：
   - automation tactic 是可见、可局部替换的优化对象；
   - LLM 在 tactic rewrite / staticize 上有直接介入点；
   - 这条路最适合验证“LLM 能不能改 proof”。
2. **但我们并没有把 proof 优化等同于 search 优化**。现有结构优化报告已经明确承认：
   wall 的主要瓶颈可能在 search 之外。
3. 因此 search 角度在论文里应被表述为：
   **一个被系统审计的起点，而不是 proof optimization 的全部。**

### 5.2 挑刺二：为什么不用 step 数、proof 长度、Isar 化程度做目标

这也是合理质疑。

需要明确区分：

- **proof style / readability optimization**
- **verification wall-time optimization**

减少 step、缩短 proof、把 tactic proof 改成 structured Isar proof，这些都可能让 proof
更好读、更好维护，但**不保证更快**。这恰好是当前研究的重要发现之一：

> “更静态、更结构化、不等于更便宜。”

也就是说，step reduction 是一个**合法的 proof 优化角度**，但它优化的是：

- 可读性
- 可维护性
- 可审计性
- 未来 few-shot / retrieval 的可复用性

而不必然是 wall。

### 5.3 挑刺三：为何不直接优化关键路径上的 CRefine

这个质疑也会出现。

目前最好的回应是：

- 我们不是没意识到关键路径的重要性；
- 相反，现有报告已经明确指出 CRefine 才是关键路径；
- 真问题在于：**关键路径 proof 的测量和验证成本极高，现有工具链对它的可操作性差**；
- 即便如此，报告里已经有 `Invoke_C:3113` 一类关键路径上的分布外案例和 own-session 探索，
  说明我们没有忽略它，而是在被基础设施墙限制。

因此合理写法是：

> 我们的负结果并不是“关键路径无关”，而是“在当前可操作子集上，LLM 有局部能力；
> 真正影响 wall 的关键路径需要更昂贵的专门实验”。

> **§3.7 更新(2026-07-01)**:那个"更昂贵的专门实验"已经做了。用 own-session 单 theory build 在 CRefine 关键路径上直测:①纠正了 §3.6 的 anon% 假象(那 93.8%–99.7% 是 `Adding rewrite rule` simpset 重建、非 def 展开);②`CSpace_C:2408` 删一条白试全局 `[simp]`(`ctes_of_not_0`)实测 **−7.4% 且绿**,证明关键路径**局部可缩**;③但该规则承重,整 theory 删即断。写法可更强:"**关键路径不仅测过、还测出可缩的 7.4%,只是收益有界且规则承重**"。见 [1-modify-lemma/REPORT.md §3.7](1-modify-lemma/REPORT.md)。

### 5.4 挑刺四：既然 wall 不动，为什么不干脆换目标

这个问题也很可能出现，尤其当读者接受“LLM 能改 proof”，但不接受“wall 不动还值得写”时。

最好的回应不是防守，而是主动区分：

> 换目标当然合理，但那会变成**另一篇论文的问题设定**。

具体说：

- 如果目标是 `wall / verification cost`，那么当前论文的中心问题就是
  “为什么局部 proof rewrite 不足以带来端到端提速”；
- 如果目标是 `proof style / maintainability / auditability`，那么 step reduction、
  Isar 化、proof normalization 将成为更自然的主线。

因此，本文不是否认那些目标，而是有意识地把它们与 wall 目标分开，不混在同一问题设定里。

---

## 6. 为什么最初会从 search 角度切入

这件事在论文里需要正面讲清楚，否则会显得像任意选题。

### 6.1 search 角度的合理性

proof search 是最自然的起点，因为它同时满足：

1. **局部性强**：一条 tactic 命令就是一个明确对象；
2. **可替换**：可以尝试静态化、降级、缩减输入；
3. **最接近 LLM 的优势**：LLM 擅长提出替代脚本、补 facts、重组步骤；
4. **最容易做 build gate**：改一个 command span 后可立即验证。

换句话说，search 角度是“**最像 LLM 能做的 proof 优化**”。

### 6.2 search 角度的局限

但这条路也天然有边界：

- tactic 的运行成本不一定来自 search；
- `simp` work、VCG、ccorres 等可能更贵；
- 就算 lemma 本体更快，也可能不在关键路径上；
- build wall 是 session-DAG 级别的量，不是单条 tactic 的量。

所以 search 角度在 hindsight 上看，最适合作为：

> **能力验证 + 边界刻画的实验场**

而不适合作为 proof 优化的唯一叙事。

---

## 7. proof 优化还有哪些其他角度

有，而且最好在文中主动列出来，既显示完整视野，也减少“只挑方便做的”这种批评。

下面按“优化对象”分类。

### 7.1 step reduction / proof shortening

目标：

- 减少 lemma 的 proof step 数；
- 减少 tactic 链长度；
- 合并冗余步骤；
- 用更直接的 rule / simp 组合替代长链。

价值：

- 提高可读性；
- 提高可维护性；
- 形成更稳定的 few-shot 模板。

风险：

- step 更少不等于更快；
- 有时更短的 tactic 反而调用更重的 automation。

适合作为：

- **proof style optimization**
- **maintainability optimization**

而不是默认的 wall optimization。

### 7.2 structure normalization / Isar 化

目标：

- 把 ad-hoc `apply` 链改成更结构化的 proof；
- 显式暴露子目标结构；
- 让 proof dependencies 更清晰。

价值：

- 提升人工审阅性；
- 提升改写语料质量；
- 有利于 LLM 未来做 retrieval / analogical rewriting。

这和本轮 proof-staticize archive 里“可审计性 / 语料积累”的价值是同方向的。

### 7.3 bottleneck-directed simplifier optimization

目标：

- 不是去掉 search，而是直接优化 `simp` work；
- 缩小 simpset；
- 避免热点 rewrite；
- 针对展开链做更定向的重写。

这可能是比“纯 search 优化”更贴近真实 wall 的方向，尤其在 CRefine 上。

> **§3.7 更新(2026-07-01)**:此方向已在 CRefine 上被系统尝试(`trial_probe`/`mutation_test`/`theory_simpdel` + `SIMP_TRACE_DEPTH`)。发现:(a)"缩小 simpset"若指删**没用到**的声明**无效**——discrimination net 让匹配不上的规则近乎免费;(b) 真正费时的是**被试了又条件 discharge 失败**的白试规则(`ctes_of_not_0` 96–107 次),去掉它单行省 7.4%;(c) 但这类规则通常**承重**,不能廉价全局删。结论:bottleneck-simplifier 有真信号但受"规则承重"限制,收益有界。

代价：

- 需要更细的测量和更高的领域知识；
- 改写常常更脆弱；
- 与 proof semantics、proof engineering 风格耦合更深。

### 7.4 key-path-first optimization

目标：

- 不再均匀扫 lemma，而是只盯 critical-path session；
- 先问“这条命令在不在关键路径上”，再问“它能不能变快”。

价值：

- 更接近真实 wall 目标；
- 能避免旁支优化被并行 slack 吸收。

难点：

- 当前基础设施对 CRefine / Refine 重 session 很不友好；
- 单点验证成本极高。

### 7.5 build-structural optimization

这是 proof 内容之外的另一条线，现有 structural report 已经在做：

- session DAG
- flatten import
- parent-swap
- duplicate session elimination
- threads / `parallel_proofs`

这条线的重要性在于：

> 如果论文目标是 wall，而非 proof 内容本身，那么结构线必须和 lemma 线并列出现。

---

## 8. 如果被问到这些问题，应该怎么回答

这一节是给后续讨论、答辩或写 rebuttal 直接复用的短答模板。

### 8.1 “proof 优化为什么以 wall 为目标”

推荐回答：

> 我们不是说 proof 优化只能看 wall，而是这篇工作把问题限定为 verification cost。
> 在这个问题设定下，wall 是最直接、最工程相关、也最容易和 build/session 成本对齐的指标。
> 其他指标当然也重要，但回答的是不同问题。

### 8.2 “既然 wall 动不了，为什么不换个目标”

推荐回答：

> 换目标完全合理，但那会变成不同的问题设定。
> 如果目标是 maintainability、proof style 或 auditability，那么 step reduction 和
> Isar 化会更自然；而本文聚焦的是 wall-oriented proof optimization。

### 8.3 “为什么只从 search 角度切入”

推荐回答：

> search 角度不是 proof 优化的全部，而是最适合做 LLM tactic rewriting 实验的起点：
> 对象局部、改写明确、build gate 清晰、可以系统比较 eliminate / degrade / reduce。
> 我们的实验结果也正说明：search 是真实但不完整的切口。

### 8.4 “step reduction / Isar 化是不是也应该做”

推荐回答：

> 是，它们都是合法方向。但它们更偏向 proof maintainability、readability 和 auditability，
> 不自动等于 wall 优化。本文并不否认它们，只是没有把它们和 wall 目标混在一起。

---

## 9. 建议的写作姿势

### 9.1 主动承认“search 不是 proof 优化的全部”

论文里最怕的是被读者替你指出限制。

更稳的写法是：

> We start from the search angle because it is the most direct locus for LLM-driven tactic rewriting,
> but our experiments show that search is only one part of proof cost, and often not the dominant one.

这样 search 角度就不再显得狭窄，而是一个被自觉限定的研究切口。

### 9.2 把“step reduction”写成未来方向，而不是遗漏

完全可以直接写：

- 我们当前优先研究的是 wall-sensitive 优化；
- step reduction / proof shortening / Isar normalization 是正当方向；
- 但它们优化的更多是可维护性、可审计性与语料质量；
- 这部分值得作为下一阶段工作，而不应在本稿里和 wall 目标混为一谈。

### 9.3 最稳的 framing

如果要一句话概括 proof 阶段，最稳的是：

> We investigated proof optimization from both the local tactic-search angle and the global build-structure angle.
> LLMs can optimize local proof commands, but end-to-end wall speedup is constrained by critical-path placement
> and non-search proof costs.

---

## 10. 还值得补什么实验

如果后续还想把论文写得更硬，建议只补少量、但最能定性的实验。

### 10.1 关键路径上的 1–3 个定向案例

目标：

- 在 CRefine 上选少量代表性慢行；
- 用 own-session / 单 theory build 做 reduce-style 改写；
- 把“关键路径上是否存在可验证加速”做成直接证据。

这不是为了刷命中率，而是为了把下面这句话写硬：

> “我们没有回避关键路径；关键路径上也被测过，只是可操作性差、空间更窄。”

> **§3.7 更新(2026-07-01)**:这 1–3 个定向案例已完成。`CSpace_C:2408`(148s,CRefine 关键路径)用 own-session 单 theory build 做 reduce-style 改写(删白试的 `ctes_of_not_0[simp]`)→ **−7.4% 且 build-green**,是"关键路径上存在可验证加速"的直接证据;`Fastpath_C:2052`(408s)确认同类浪费模式。§10.1 的实验目标达成,结论写进 §3.7。

### 10.2 step reduction 的小型对照

目标：

- 选 3–5 条已成功 rewrite 的 lemma；
- 比较“step 更少 / style 更好”与“wall 是否更快”；
- 直接展示“proof shorter ≠ proof faster”。

这个对照很适合用来回应“为什么不优化 step”。

---

## 11. 最终建议

### 11.1 当前最值得坚持的写法

当前最值得坚持的不是：

- “LLM 让 seL4 proof 普遍提速”

而是：

- “LLM 证明了自己能做 proof optimization，但 proof-wall 的主瓶颈常常不在它能直接消除的 search overhead 上。”

### 11.2 proof 线在整个平台中的定位

在整个平台叙事里，proof 线最合适的定位是：

- **能力验证**：LLM 确实能改 proof；
- **边界刻画**：为什么改了也可能不降 wall；
- **方法论贡献**：proof 优化必须 bottleneck-aware；
- **资产沉淀**：transcript、rewrite case、few-shot 库、可审计记录。

而真正大规模、稳定产出的主线，当前仍是 spec strengthening。

### 11.3 一句适合放在摘要或引言里的话

> We show both the capability and the limit of LLM-based proof optimization for seL4:
> LLMs can successfully rewrite and reduce local proof commands, but these local improvements
> often fail to reduce end-to-end verification wall time because the dominant costs are
> structural and non-search-related.

---

## 12. 本报告服务的具体用途

后续可直接复用到：

- 论文引言里的 framing；
- proof 章节开头的问题设置；
- related limitations / threats to validity；
- rebuttal 或内部设计评审；
- 下一轮实验规划。

相关材料：

- [search-angle-optimization/REPORT.md](1-modify-lemma/REPORT.md)
- [proof-structural-optimization/REPORT-2026-06-26.md](2-modify-structure/REPORT-2026-06-26.md)
- [proof-staticize-archive/direction-A-search-reduction.md](archive/direction-A-search-reduction.md)
- [proof-staticize-archive/tactic-rewrite-and-claude-p-pipeline-20260616.md](archive/tactic-rewrite-and-claude-p-pipeline-20260616.md)
- [proof-staticize-archive/intra-tactic-profiling-and-setup-cost-20260605.md](archive/intra-tactic-profiling-and-setup-cost-20260605.md)
