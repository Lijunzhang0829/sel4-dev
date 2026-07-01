> **方向一详报(改 lemma 本身)** · 整体报告与两方向索引见 [`../README.md`](../README.md) · 方向二见 [`../2-modify-structure/REPORT-2026-06-26.md`](../2-modify-structure/REPORT-2026-06-26.md)

# seL4 lemma 从 "search 角度" 优化的实验报告

_自动化 tactic → static tactic 改写:三方法对比与"提速空间"的实证_

日期:2026-06(本会话)。所有结果均可溯源到持久 run 目录 / 归档日志 / git 提交(见 §5)。

---

## 0. 问题、实验结构与目的

**问题**:seL4 的 lemma 能否从 "**search 角度**" 优化——通过操纵昂贵的自动化搜索 tactic(`auto/fastforce/force/blast`),**让 lemma(进而 proof build)变快**?

### 0.1 实验结构:把"操纵 search"拆成三个互斥且穷尽的具体操作

"从 search 角度提速"这件事,对一条 search tactic 只有三种可做的动作——**移除它 / 换便宜的 / 缩小它**。本报告就是把这三条路各做一个子实验、逐一证伪或证实:

| 子实验 | 对 search 的操作 | 假设 | 数据集 | 方法 / 工具 | 章节 |
|---|---|---|---|---|---|
| **A 消除**(eliminate) | 把 search tactic **整个换成**确定性 static 规则路径(reach-B 重建) | 搜索是白费,可被静态路径替代且更快 | 佐证集 12 + wasted 高价值 7 + DB-hottest | DFS / GenStat-stateful / GenStat-blind **三方法** | §2 · §3.1–3.3 · §4.1–4.2 |
| **B 降级**(degrade) | **保留但降级**:`auto/fastforce/force` → 更便宜的 `clarsimp` | 无规则链时 classical 回溯是白费,真功只是 simp | wasted-classical 困难臂 7 + 行为筛 26 | `wasted_ablate.py`(真换 + build + A/B) | §3.4 · §4.3 |
| **C 缩减**(reduce) | **保留 tactic、只缩小输入/搜索空间**(`simp only:` 前置 / `simp del:` / 定向 `simp:` / 换序 / 喂事实) | 喂窄输入能让同一 tactic 少搜、更快 | `high_value_candidates` 45 feasible | `reduce_agent.py`(claude)+ 双闸门验证 | §3.5 · §4.4 |

**贯穿的测量问题**:per-lemma 的正确度量是 **CPU(lemma 串行)** 还是文件 / build **wall**?(三子实验都撞到它 → §3.3 / §4.5)

### 0.2 目的

三个操作**穷尽**了"在 search 这条路上提速"的全部可做动作;逐一给出可溯源的证伪/证实,就能完整回答:**search 角度到底能不能提 seL4 的 per-lemma 成本、能不能提 build wall。**

### 0.3 一句话结论(先给,按子实验对应)

- **A 消除 → 结构性错误任务**:能重建的恰是 `<100ms` 的便宜行(无价值),秒级慢行 reach-B **三方 0/7**(不可达);static 化只 re-derive,省搜索不省推理。
- **B 降级 → 前提被证伪**:**23/23** 有效 clarsimp swap BUILD-FAIL,classical 在做真 case-work,不是"白费"。
- **C 缩减 → 唯一为正**:56 feasible 全扫(干净 own-session stock CPU,§3.5.3 更新块)→ **7 个稳健真加速(干净 ≥20%)**,含 2 个近乎消除(is_stateAssert_gets 100% / in_whileLoop 97.9%)、empty_slot 省 40s。**但 in-REPL 虚高 3×(报 22 FASTER)、~40% 候选 NO-PATH 缩不动。**
- **引用角度(§3.7,后加)→ 局部可缩已证 + 廉价全局删证伪**:换轴测"减少 ambient 引用集"。**纠正了 §3.6 的 anon% 假象**(那 93.8%–99.7% 是 `Adding rewrite rule` 的 simpset 重建、非 def 展开);关键路径行 `CSpace_C:2408` 删掉一条白试全局 `[simp]`(`ctes_of_not_0`)实测 **−7.4% 且绿**——**推翻 §3.6"关键路径不可缩"**。但该规则**承重**,整 theory 删即断证明(CSpace_C:1064),故"删全局声明→全局提速"的廉价杠杆不成立;只剩两条贵路(去全局+局部补回 / 规则再工程)待决策。
- **合**:**search 角度对 per-lemma 可行,对 build-wall 大体无效**——关键路径(CRefine)§3.6 曾判"不可缩",但 §3.7 用更细的 trace 解析纠正了其 anon% 证据、并实测关键路径行**局部可缩 7.4%**(廉价全局删仍被承重规则挡死)。本报告只覆盖"**改 tactic 内容 / 引用集**"这一轴;另一条"**改结构**"轴(session DAG / 并行 / 冗余 session)由独立报告 [`proof-structural-optimization/REPORT-2026-06-26.md`](../2-modify-structure/REPORT-2026-06-26.md) 给出,结论同样是"几乎无空间"(并行已满、冗余拒改、flatten 被基建挡)。**两轴合起来**:固定硬件 + 不改证明内容 + 不被上游拒,seL4 proof 墙几乎无真优化空间,真正的杠杆在更大硬件 / heap 缓存 / Docker(详见 §4.5)。

---

## 1. 候选集选择(及其演化)

| 候选集 | 选法 | 规模 | 产物 |
|---|---|---|---|
| DB 计时挖掘 | `scan_db_timings.py` 解 heap `command_timings` | 全量 | `tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json` |
| **DB-hottest 冻结集** | `curate_candidates.py`,3-arm/2-source(search≤30 / work-suspect≤10 / hard≤8;source A=db-scan ≥10s 搜索主导,source B=wasted 2–60s 带)| 46 | `lemma-staticize/experiment-candidates/{candidates.json,manifest.md}` |
| **佐证集**(历史已证可解) | `build_corroboration_set.py` | 16(fast 12) | `experiment-candidates/{corroboration-set.json,.manifest.md}` |
| wasted-classical 高价值 | `wasted_classical_json.py`(2–60s 带,精炼文本过滤) | 7(其中 5 个 ≥10s)| `wasted_candidates.json`(31 条),`lemma-staticize/runs/wasted_hv_candidates.json`(7) |
| 实测筛器候选 | `session_candidates.py`,≥2s 少过滤 | 1283 | `runs/session_candidates.json`,`runs/{pilot_ablate_set,rescan_set}.json` |

**贯穿教训(重要)**:
- **DB `command_timings` 的 elapsed 只能粗排**——来自并行 build(j=2),含调度竞争噪声、且 elapsed≠cpu,**跨配置不可比**;唯一交叉核对的 `rm_affects` 实测 DB 24.6s vs 串行 22.5s(`reduce-all-…/result.json` `orig_ms=22541.4`),**基本一致(~1.1×)**——DB 适合粗排候选,但不能当绝对耗时/提速依据。
- **per-lemma 难移动文件 wall**——典型单条 tactic ~1–4s(占 PolicySystemSAC 整文件 ~66s 的 <6%),最热的 `rm_affects`(~22s,≈文件 33%)其实不算小;真正的瓶颈是**贵的行改不动(reach-B 失败 / clarsimp swap 不适用),能改的只有便宜行且不提速(§3.3 / §4.2)**,而非分母可忽略。

---

## 2. 三种改写方法(统一判据:reach-B = 复现原 tactic 留下的目标状态)

**统一 reach-B 测试床**(三方法共享,见 [`genstat_stateful.py` 头注](../../../tools/seL4-proof-search/Isa-Repl/genstat_stateful.py#L7)):REPL 用 `sorry` 替换目标 tactic 行 → 取 **A** = 进入该行的真实 goal(`isa._extract_goal`)→ 对 A 施一次原 tactic 得 **B** = 结果状态签名([`react_agent.py:183`](../../../tools/seL4-proof-search/Isa-Repl/react_agent.py#L183))→ 成功 = 存在一条**确定性静态路径**从 A 到达签名 == B 的状态(schematic 变量名规范化后比对,见 [`canon`](../../../tools/seL4-proof-search/Isa-Repl/genstat_stateful.py#L95))。三方法只在**如何生成这条路径**上不同。

| 方法 | 工作流 | 关键 | 实现(点击查看) |
|---|---|---|---|
| **DFS** | ① 每节点构造受限、过审计的候选菜单(结构规则 + hammer facts)② 按结构序逐个试,死路**回溯**,已访状态规范化剪枝 ③ 仅 audit-passing 的静态 tactic 入路径 | 纯结构**菜单 + 回溯保证覆盖**(无 LLM 开放词表);天花板 = 菜单封顶(够不着库规则 / 复杂实例化) | [`react_agent.py` NO_CLAUDE](../../../tools/seL4-proof-search/Isa-Repl/react_agent.py#L151) · [菜单](../../../tools/seL4-proof-search/Isa-Repl/react_agent.py#L90) · [回溯循环](../../../tools/seL4-proof-search/Isa-Repl/react_agent.py#L219) · [审计](../../../tools/seL4-proof-search/Isa-Repl/react_agent.py#L123) |
| **GenStat-stateful** | ① 给 claude 看**当前活 goal** ② claude 提下一 1–3 行静态 apply(开放词表、多行)③ REPL 施用并返回**活的步后态**,循环至 reach-B 或轮次耗尽;失败回灌错误 | **活状态 = 就地纠偏**;开放词表突破菜单封顶 | [`genstat_stateful.py` SHOW_STATE=1](../../../tools/seL4-proof-search/Isa-Repl/genstat_stateful.py#L39) · [逐步施用](../../../tools/seL4-proof-search/Isa-Repl/genstat_stateful.py#L202) |
| **GenStat-blind** | ① 只给 claude **A + 目标 B**(无中间态)② 一次性生成**整段** apply-script ③ REPL reach-B 验证;失败回灌错误后整段重生成 | **一次性整段**:免逐步 churn,但无中途纠偏 | [`genstat_stateful.py` SHOW_STATE=0](../../../tools/seL4-proof-search/Isa-Repl/genstat_stateful.py#L298) |

桥接:容器内无 `claude` 二进制 → [`proposer_host.py`](../../../tools/seL4-proof-search/Isa-Repl/proposer_host.py#L40) 在 host 跑 `claude -p`(host OAuth,仅 goal 文本 + 候选 tactics 过桥,通道错误经 `err` 回传)。

---

## 3. 改写结果(均可溯源到 run 目录)

### 3.1 佐证集(12 个可解案例,统一 reach-B)
| 方法 | REACHED-B | 溯源 |
|---|---|---|
| DFS | **3/12** | [`corrob-dfs-…111056/summary.tsv`](../../../lemma-staticize/runs/corrob-dfs-20260622-111056/summary.tsv) + 每 case `stdout.log`(无 `result.json`) |
| Stateful | **12/12**(累计;首跑 7,余者重试 + 修 2 个 bug 后补齐) | [run1](../../../lemma-staticize/runs/corrob-stateful-20260622-112302/summary.tsv)(7)+ [run2 重试](../../../lemma-staticize/runs/corrob-stateful-20260623-051156/summary.tsv)(11)+ [`zombie_refix.log`](../../../lemma-staticize/runs/_archived-logs/zombie_refix.log)(12) |
| Blind | **12/12**(单跑干净) | [`corrob-blind-…020258`](../../../lemma-staticize/runs/corrob-blind-20260623-020258/) |

- **结论**:开放词表(stateful/blind)在 reach-B 覆盖上**碾压菜单 DFS**;DFS 卡在菜单封顶(够不着库规则/实例化)。
- **stateful ≈ blind**:活状态是**效率杠杆**,非能力天花板;churn 经 apply_chunk 修复后基本消除(单一案例 Zombie 即由此翻盘)。注:三者中**仅 Blind 是单跑干净 12/12**;Stateful 首跑 7/12 受通道不稳(2 CHANNEL-FAIL)+ Zombie churn 拖累,经重试+修复才累计 12/12——"覆盖率相当"指**能力**相当,非首跑稳定性相当。

**实验设置**:12 个佐证案例(历史已证可解的小 lemma)× 3 方法,统一 reach-B、串行。DFS 纯菜单无 LLM;stateful/blind 经 [`proposer_host.py`](../../../tools/seL4-proof-search/Isa-Repl/proposer_host.py#L40) 在 host 用 `claude -p` 提议,每轮交互(`prompt` + `response`)落盘 transcript 供审计。

**claude -p 交互日志(点击逐轮看 claude 如何处理)** —— 每条记录:`prompt` = 喂给 claude 的目标 / 当前 goal,`response` = claude 提议的静态 apply,`claude_secs` = 真实 claude -p 耗时:
- **Stateful**(逐步,`prompt` 为 "…STEP BY STEP … look at the CURRENT goal"):[`…is_transferable_IRQ.jsonl`](../../../tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-is_transferable_IRQ.jsonl)(6 轮,turn 5 claude 复盘"前两次各缺一块 → 合并 `option.inject`+`cap.distinct`+`option.distinct`"并闭合);[`…is_transferable_Zombie.jsonl`](../../../tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-is_transferable_Zombie.jsonl)(turn 1 提 `apply assumption` 被 REPL 拒回残余 goal → turn 2 据残余补 `option.inject` 闭合)。
- **Blind**(一次性,`prompt` 为 "…DETERMINISTIC static apply-script … to the SAME state … You do NOT get [中间态]"):[`…rm_affects-P25905.jsonl`](../../../tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-rm_affects-P25905.jsonl)(round 0 整段 `simp only: …; done`(74.8s)→ 失败后 round 1 **整段重生**为 `unfold …; simp only: …; done`(375.9s),全程无中间态)。

### 3.2 慢案例为何失败(reach-B 够不着)
三方在慢案例全失败(7-案例集,三方 **0/7**,详见 §3.4)。**(更正:无独立"8-案例"run;原稿"0/8"系与 §3.4 7-案例集混记。)** 从失败 claude -p 日志 + lemma 代码看,两类失败机制:

**① 大 case-split / 归纳树——静态脚本展不平** · [`SAC_partsSubjectAffects_exceptT`](../../../verification/l4v/proof/infoflow/PolicySystemSAC.thy#L909)(InfoFlow,`auto` 行 22.2s)。原证明是嵌套 `case_tac` + 组合子重复跑在几十个子目标上:

```isabelle
lemma SAC_partsSubjectAffects_exceptT : "x \<noteq> T \<Longrightarrow> partsSubjectAffects SACAuthGraph x = SACFlowDoms"
  apply (rule equalityI) defer
   apply (rule subsetI)
    apply (simp add: partsSubjectAffects_def image_def label_can_affect_partition_def)
    apply (case_tac x)
     apply ((erule disjE, clarify, simp add:SAC_affects SAC_reads, blast?)+, simp add:SAC_affects SAC_reads, blast?)+
   apply (rule subsetI) ...
    apply (case_tac x)
      apply (case_tac[!] xaa)
        apply (auto simp: SAC_affects SAC_reads)
  done
```

claude(blind)只提朴素 `apply (simp only: SAC_affects SAC_reads) done`(38.7s)→ 第二轮 `Reached max turns`,失败。搜索 tactic 在线枚举的 case 树,reach-B 静态重建够不着。日志:[`…SAC_partsSubjectAffects_exceptT.jsonl`](../../../tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-SAC_partsSubjectAffects_exceptT.jsonl)。

**② 巨型 goal——claude -p 自身超时** · [`requiv_user_mem_eq`](../../../verification/l4v/proof/infoflow/ARM/ArchUserOp_IF.thy#L817)(InfoFlow,`fastforce` 行 10.8s,**11 个前提**、深层 `fastforce simp:…`/`frule`/`context_conjI'` 证明)。[claude -p 日志](../../../tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-requiv_user_mem_eq.jsonl):round 0 `claude_secs=400.1 err='timeout'`、**空 response**——goal/上下文体量太大,LLM 调用本身超时,瓶颈是 goal 大小而非推理。

### 3.3 提速实测:工具、指标、以及"该测 CPU"的质疑

- **用的工具(粗筛)** = [`time_rewrite.py`](../../../tools/seL4-proof-search/Isa-Repl/time_rewrite.py):in-REPL A/B(py4j JavaGateway + `time.monotonic()`),从 checkpoint A 克隆,分别跑原 tactic 与 reach-B 静态路径;丢 warm-up → ≥9 reps **中位** → 每步减 py4j **IPC 基线** → 天花板 `C_i = orig_ms_adj − static_ms_adj` → A/A **噪声地板** `aa_spread`(原 tactic 跑两次)→ `C_i > 2×aa_spread` 才记显著。粗筛结果:成功案例全 **sub-60ms**、`significant:false`、blast 系为负。

- **⚠️ 这个测量不该当真,而且测错了量**:
  1. **它量的是墙钟,不是 CPU**——`time.monotonic()` 包住 py4j step,含 IPC + JVM GC + 冷启;`measurement-tools.md` 实测冷启 ↑4.5×、run-to-run 噪声 ≤58%(足以翻盘)。我们真正关心的是**重写后 tactic 的 CPU 时间有没有降**,in-REPL 墙钟答不了;残余噪声也远大于这些 sub-60ms 信号。

- **正确指标 = per-command CPU,但当前无可用测量手段**:
  - golden `command_timings`:**只有 `elapsed`、无 `cpu`**(实测字段 = `name/offset/file/elapsed`),且这些候选行在记录地板下**根本无记录**(§4.2);
  - IsarLite per-line cpu:**坏**(`missing-json`);
  - ML intra-tactic profiler:撞 parallel-futures / setup 墙,够不着目标行。

- **结论靠"界",不靠 in-REPL**:§4.2 已严谨定出**原始 `elapsed < 100ms`;对这些单命令、单线程 `by (…)`,`cpu ≲ elapsed`,故原始 cpu 也 < 100ms** —— 重写能省的 CPU 上界 < 100ms,与测量工具精度无关。**更关键的是没有可测对象**:所有**成功**重写的恰是这些 <100ms 行,而值得测 CPU 的慢行(≥100ms)reach-B **全失败、没有重写版可比** ⇒ "重写后 CPU 是否加速"在可测频段产不出有意义差值、在有价值频段无对象——本实验里**无法、也无需**用 CPU 测量翻案。

- **反相关**:可改写 = 快案例(无价值);有价值 = 慢案例(改不动)。

### 3.4 第二条思路:tactic 降级(clarsimp 消融)

**动机**:§3.1–3.3 的 reach-B 是**完全静态重建**(把 search 整个换成 rule 路径),贵。这里测一条**更便宜**的思路——不重建,只把贵的 `auto/fastforce/force`(带 `simp:`、无 dest/elim/intro/split 规则链)**降级成 `clarsimp`**,赌"无规则链时 classical 的回溯是白费的(wasted),真功只是 simp/clarify"。动机来自此前 **2 个已证实的 fastforce→clarsimp file-wall 提速**(如 `VSpacePre_AI:162`)。两支实验:

**(A) 高价值臂**——7 个手挑高价值 wasted-classical 候选(auto/ff,≥6–27s),**在同一组上同时跑 reach-B 三方法 + clarsimp 降级**:

| 方法(同 7 困难候选) | 操作 | 结果 | 日志 |
|---|---|---|---|
| reach-B / DFS | 纯菜单重建 | **0/7**(4 NO-PATH + 3 超时) | [`wasted_dfs.log`](../../../lemma-staticize/runs/_archived-logs/wasted_dfs.log) |
| reach-B / Stateful | claude -p 逐步重建 | **0/7**(全 ~600s 超时) | [`wasted_stateful.log`](../../../lemma-staticize/runs/_archived-logs/wasted_stateful.log) |
| reach-B / Blind | 一次性整段重建 | **0/7** | `corrob-blind-*` summary(§3.2) |
| 降级 / clarsimp 消融 | `auto/ff → clarsimp` + build | **4 build-fail + 3 重 session 未决** | [`clarsimp-ablate-hard-*.log`](../../../lemma-staticize/runs/clarsimp-ablate-hard-20260628-002501.log) |

clarsimp 4 个明确 fail = `cap_insert_simple_arch_caps_no_ap` 留 5 子目标、`refinement2_both`/CRefine proof-method 失败、`abstract_invs`/InfoFlowC FAILED、`SAC_partsSubjectAffects_exceptT` 留 11 子目标〔据 §3.4(B) 26 集〕;3 个未决 = `requiv_user_mem_eq` / `requiv_device_mem_eq` / `ckernel_invariant`(InfoFlow/Refine 重 session build 超时/锁冲突)。

**这个实验说明**:在**同一组高价值/慢 lemma** 上,**reach-B 重建(三方 0/7)与 clarsimp 降级(可决 4/4 fail)都不成立**——高价值频段上"移除"和"降级"两条 search 路都走不通;clarsimp 降级留下大量未闭合子目标,正说明 classical 搜索在这些 lemma 上是**真 case-work**(非"白费")。限定:3 个重 session 因 build 成本不可决,故是"未观察到成功",非"全证伪"(见下"外部效度")。

**(B) 行为式筛器臂**——不靠文本启发式,用 [`wasted_ablate.py`](../../../tools/seL4-proof-search/Isa-Repl/wasted_ablate.py) **真换 clarsimp**:先 build(便宜拒绝,多数死在这)→ 能 build 再 A/B 测 file-wall,报 `frac_saved`(省的文件墙比例)+ `non_overlap`(最差 clarsimp build 仍胜最好 baseline)。跑 26 个 ≥2s 候选(Access+InfoFlow:[pilot 12](../../../lemma-staticize/runs/_archived-logs/pilot_ablate2.log) + [rescan 14](../../../lemma-staticize/runs/_archived-logs/rescan_ablate.log))。

**这 26 个怎么来的**:`session_candidates.py` 实测扫出 1283 个 ≥2s 行 → 按 **wasted-classical 模式**过滤(`auto/ff/force` + `simp:`、且**无** `dest/elim/intro/split` 规则链 = "classical 最可能白费"的形态)→ 落在 Access / InfoFlow 两 session 的 26 个。

**为何在这上面能验 clarsimp 消融的可靠性**:① **最有利样本**——它们按"最可能可降级"的模式选出,若 clarsimp 消融在任何地方成立、最该在这里成立 → 23/23 失败是强否证;② **有墙钟信号**——≥2s 门槛保证"一条 line 的省时若存在能在文件墙显形"(不像 <100ms 佐证集天然测不出);③ **行为自证**——`wasted_ablate.py` 真换 + build + A/B,正确性(build 绿)与提速(`frac_saved`/`non_overlap`)都被直接验证,"win"不可能是文本/DB 假阳性。结果:

**主统计(分母 = 有效 swap,非 26)**:筛得 26 → 其中**有效 clarsimp swap = 23**,无效空替换 = 3(剔除)。

| 指标 | 数 |
|---|--:|
| 有效 clarsimp swap | **23** |
| build success | **0** |
| build fail | **23** |
| invalid / no-op swap(剔除) | 3 |

- **23/23 BUILD-FAIL**:换 clarsimp 直接编译失败、留下大量子目标——classical **不是白费,是真 case-work**(`SAC_partsSubjectAffects_exceptT` 留 11、`set_object_reads_respects_scheduler` 留 12、`rm_affects` 变体留 143:`*** Failed to finish proof: goal (N subgoals)`)。
- **3 个 no-op 已剔除**:`rm_affects` / `abd_reads` / `ntfn123_reads` 是 `by (simp …, blast?)+` 的 **blast 行**,swap 正则只认 `auto|fastforce|force` → `clar == orig`(程序复现确认未替换);其 1.009–1.062× 是同脚本两次 build 噪声(`non_overlap=false`),**非有效测试,不进分母**。

**意义**:把负结论从"**重建**不划算"扩展到"**降级也不划算**"(两个 search 子角度都关上);并把候选判定从**文本代理**(`search_frac` 正则)升级到**行为基准**(真换 + build)——23/23 可应用 swap 的候选全 BUILD-FAIL,就是"文本代理系统性高估搜索"的硬证据,也堵死了"是不是候选选错了"这一反驳。

**外部效度(范围限定)**:26 集只覆盖 **Access + InfoFlow**(困难臂补测的 CRefine/Refine/InfoFlowC 多因重 session build 超时未决)。故本结论严格成立于:**在当前可行为式验证的 Access/InfoFlow 子集上,wasted-classical 启发式被强烈证伪**;它**不**等于"整个 seL4/l4v 中 clarsimp 降级普遍失败"——CRefine/Refine 等关键路径重 session 尚未形成系统覆盖(工具墙解释合理,但结论不外推)。_This is a strong negative result for the feasible Access/InfoFlow subset, not a full-session proof that clarsimp degradation never works in CRefine/Refine._

### 3.5 搜索缩减(保留 tactic、缩小搜索空间)——per-line CPU 加速

与 §3.1–3.4 的"移除 / 降级"不同,这是**第三条思路**:**保留** search tactic、只**缩小它的输入 / 搜索空间**——`simp only:`/`clarsimp` 前置规范化、`simp del:` 砍热点重写、定向 `simp:` 集替代默认、把廉价分支换到 `|` 前面、喂够事实让它别搜。

**度量口径(严格)**:目标量是 lemma 本体成本,真正的指标是 **per-line CPU**;但**可获得的"铁数"仍是 `threads=1` 下的 per-command elapsed**(stock build 读 `command_timings`,见 §3.5.2)——由于单条 `by (…)` 命令内部基本串行,这个 `threads=1` elapsed 是**目前能拿到的、最接近 per-line CPU 的代理量**(_per-command elapsed under `threads=1`, the closest available proxy for per-line CPU_),但**严格说仍不是 CPU**(Isabelle 全程不落 per-command cpu,见 §3.3/§4.5)。下文"加速 X%"均指此代理量。它比文件 wall 更接近 lemma 本体:不在关键路径上的 lemma 即使本体变快也不反映到文件/build wall(见 §3.5.4)。

#### 3.5.1 数据集:为什么另起一个集,以及它怎么来的、凭什么

**为什么不复用消除集 `candidates.json`(§1,46 条)**:① 那是为"消除"实验**手工 3-arm 策展**的(search≤30 / work-suspect≤10 / hard≤8,2-source 混合),带经验挑选成分;② 它与本实验的目标行**基本不相交**(实测 lemma 交集 = 0,见下);③ 它含 `hard` 臂的重 session lemma(CRefine/Refine),本实验的 REPL 内层根本 init 不动。强行复用既不可复现、又大半跑不了。

**缩减实验改用 `high_value_candidates.json`,由 [`extract_high_value.py`](../../../tools/seL4-proof-search/Isa-Repl/extract_high_value.py) 生成**。设计与理由(逐条,均可复现):

| 设计 | 取值 | 凭什么(convincing reason) |
|---|---|---|
| **数据源** | build 自己的 `command_timings`(heap DB) | "高价值"必须是**实测贵**,不是臆测;用 build 自报的 per-command elapsed 是"哪行最费"的 ground truth。每条候选都溯源到一个测量值。 |
| **VALUE 门槛** | `command_timings elapsed ≥ MIN_ELAPSED=10s` | 只测**值得优化**的行。1s 行省 50% = 0.5s,无意义。10s 是"值得一次改写尝试"的下限。**⚠️ 门槛量是 `command_timings` 的 elapsed,不是 CPU**(与 §3.3/§4.5"golden 只有 elapsed、cpu 不可测"一致)。代码里此参数历史命名为 `MIN_CPU`、字段 `cpu_s`,但实为 **elapsed**——属命名遗留,数值与语义以本行为准。 |
| **REWRITE-TARGET** | tactic ∈ `{auto,fastforce,force,blast}` | 缩减命题 = "缩小**经典搜索** tactic 的搜索空间"。故只选经典搜索;**排除 clarsimp/simp**(simp-work,另一种优化)、**metis**(已是重建)——否则测在无关案例上。 |
| **FEASIBLE = 工具边界** | 文件 ≤2900 行 **且** session ∉ {CRefine,Refine,InfoFlowC} **且** 外围命令可 init(lemma/theorem/corollary) | 这是 **REPL 内层 reach-B 能不能真的尝试**的边界,**不是价值判断**:重 session 撞 init 墙(34min 重建 heap / gateway 超时 / churn-OOM,见记忆 `infoflow-repl-init-wall`),大文件 init 超时。**越界的标 INFEASIBLE、单独报,不静默丢**——边界对读者完全透明。 |
| **per-LINE 单位** | 一个 lemma 的每条搜索行都是候选(不按 lemma 去重) | 改写**任一条**搜索行让 lemma 变快就算加速 ⇒ 每条都试。 |
| **work_suspect 标记** | `simp:` 带 ≥2 个 def 且无 rule-chaining → 成本疑似 simp-work 非搜索 | 文本分不清"搜索 vs simp-work"(profiling 墙),故**标记不排除**,结果按它拆开报。 |

**全部参数写在产物里**(`high_value_candidates.json` 的 `params` 字段),可改可重跑:`MIN_ELAPSED=10`(产物字段名 `MIN_CPU`,实为 elapsed,见上)`/ TACTICS=auto,fastforce,force,blast / MAX_FILE_LINES=2900 / EXCLUDE_SESSIONS=CRefine,InfoFlowC,Refine`。**无任何手工挑选**——这正是此前明确要求的"以代码方式可复现、可上手验证,而非按经验挑"。

**规模与构成**:**115 搜索行 / 90 lemma**(16 个 lemma 有 >1 搜索行)→ **45 feasible**(35 search-suspect + 10 work-suspect)+ **70 infeasible(工具墙)**。

| | feasible session 分布 | infeasible(工具墙)session 分布 |
|---|---|---|
| | InfoFlow 31 · Access 6 · DRefine 4 · AInvs 3 · Lib 1 | **CRefine 48** · AInvs 10 · InfoFlowC 7 · DRefine 3 · InfoFlow 2 |

> **⚠️ 数据集设计最诚实的一点(也是本节最重要的结论之一)**:为让 REPL 跑得动而排除的 70 条里,**48 条在 CRefine——正是 build 关键路径**(关键路径分析:CRefine 占总串行成本 49%、关键路径 84%)。留下的 45 feasible **全在轻 session(InfoFlow/Access/… = AInvs 下的并行旁支)**。**这个数据集从设计上就把"能动 wall 的行"全划进了 infeasible**——直接预定了 §3.5.4 的结论:feasible 行的 per-lemma 加速**真实但不在关键路径上,注定不反映到 build wall**。把工具边界讲透,这个 null 结果就不是"方法弱",而是"数据集(被工具逼的)本就避开了关键路径"。

#### 3.5.2 方法与工具

**改写 agent** = [`reduce_agent.py`](../../../tools/seL4-proof-search/Isa-Repl/reduce_agent.py):给 claude 看 lemma 陈述 + 原 tactic + 目标态 B + hammer facts,让它提**缩减变体**(开放词表,见 §3.5 手法);逐轮反馈(失败/到错状态/不更快 → 回灌诊断重提)。claude -p 经 [`proposer_host.py`](../../../tools/seL4-proof-search/Isa-Repl/proposer_host.py) 桥接,全程 transcript 落盘。

**双闸门验证**:① **reach-B**(in-REPL 快速预筛,与 §2 同判据;⚠️ 长 goal 会 signature 碰撞假阳性 → 闸门②强制);② **更快 + build-green**——in-REPL A/B 判更快 + **stock `isabelle build` 验正确**([`build_verify_reachb.py`](../../../tools/seL4-proof-search/Isa-Repl/build_verify_reachb.py),B_sig 感知 splice:中段行不加 `done`)。per-lemma CPU 铁数用 [`measure_build_elapsed.py`](../../../tools/seL4-proof-search/Isa-Repl/measure_build_elapsed.py)(`threads=1` 串行 build 读 command_timings,orig/variant 同条件)。

#### 3.5.3 结果

> ## ⭐ 更新(2026-06-30):56 feasible 全集 · 干净 own-session stock CPU 全扫——**取代下方基于 in-REPL 的分类**
>
> 下方早稿的"10 confirmed faster"是基于 **in-REPL 计时**的软估计(其中 6 个"干净复核被 proof-cache 污染、未独立坐实")。2026-06-30 用 **own-session 单 theory stock build**(§3.6.1 那套:`parent=<session>` 只读 + 改名,单 theory 串行 ⇒ 干净 per-lemma CPU,无 proof-cache/冷启动污染;span 感知 splice 处理多行 tactic)对 **56 feasible 候选全量重扫**,结论硬化:
>
> **流程**:① 现有 reduce 变体 18 个直接干净测;② 对尚无 FASTER 变体的 44 个候选**串行**重跑 in-REPL reduce_agent(重启后 REPL 恢复;串行避免内存竞争)生成新变体;③ 新 FASTER 干净测。工具:[`measure_cases.py`](../../../tools/seL4-proof-search/Isa-Repl/measure_cases.py)(span-splice + 多命令求和)、[`rerun_campaign.sh`](../../../tools/seL4-proof-search/Isa-Repl/rerun_campaign.sh);铁数:[`runs/full_scan_final.json`](../../../tools/seL4-proof-search/Isa-Repl/runs/full_scan_final.json)。
>
> **44 重跑候选 verdict**:NO-PATH 20 · FASTER 9 · REACHED-B-NOT-FASTER 8 · ERROR 3 · TIMEOUT 2 · NO-TARGET 2。
>
> **24 个变体干净 CPU 裁定**:
>
> | 类别 | 数 | 案例(干净 CPU%) |
> |---|---|---|
> | ✅ **稳健真加速(干净 ≥20%)** | **7** | is_stateAssert_gets **100%** · in_whileLoop_corres **97.9%** · is_derived_cap_arch_asid 56.5% · set_cap_valid_arch_caps 54.9% · requiv_device_mem_eq 30.7% · empty_slot_pas_refined 22.7%(省 40s) · weak_derived_valid_cap 22.6% |
> | ⚠️ 临界(10–20%) | 3 | insert_cap_child / cap_insert / gets_apply_ready_queues |
> | ❌ 噪声/更慢(<10%) | 9 | 含 −121% / −33% / −14%(in-REPL 报成"更快"的假阳性) |
> | build-fail | 5 | |
>
> **三个硬结论**:
> 1. **LLM 有真能力但有限——7 个稳健真加速**(含 **2 个近乎消除**:is_stateAssert_gets 32.6s→~0、in_whileLoop 13.5s→0.3s;1 个大绝对值 empty_slot 省 40s)。这是 per-lemma CPU 判据(忽略关键路径)下的硬底。
> 2. **in-REPL 严重不可信(方法学)**:in-REPL 共报 **22 个 FASTER**,干净 CPU 只剩 **7 稳健 ≈ 3× 虚高**,还把 −121%/−33% 的**更慢**变体报成"更快"。**必须 own-session stock CPU 才能定真伪。** 但 in-REPL **无假阴性**(reached-B-not-faster 干净测确实不快)。
> 3. **~40% 候选 LLM 缩不动(NO-PATH)**:近一半昂贵搜索行找不到等价更窄路径。
>
> ⇒ search 缩减对 proof 的真实作用 = **轻 session 上 ~7/56 个昂贵 lemma 的 per-lemma CPU 真加速,够不着关键路径(§3.6 不可缩)**。

**(以下为早稿基于 in-REPL 的分类,已被上方全扫取代,保留作过程记录)** 系统样本 = 45 feasible 全跑。精确分类(均溯源 reduce-all run + `build_verify.json` + 复核 `reduce-*-RM.json`):

| 类别 | 数 | 说明 |
|---|---|---|
| **attempted** | **45** | 全部 feasible |
| **build-verified correct**(变体 stock 编译 green) | **13** | 下分 faster/slower/inconclusive |
| &nbsp;&nbsp;├ **confirmed faster** | **10** | 见下表(4 干净低噪声确认 + 6 高 margin in-REPL) |
| &nbsp;&nbsp;├ **confirmed slower** | **1** | `dmo_bind':93`:in-REPL 初判 "+16%" 是冷启动,严谨复核 **−76%(实为更慢)** |
| &nbsp;&nbsp;└ **inconclusive(信号 ≈ 噪声)** | **2** | `ntfn123`(16.6% @ 噪声 15.6%)、`cap_swap`(59.5% @ 噪声 58.3%) |
| **harness-indeterminate**(验不了正确性) | **1** | `Sys1AgentMap`:在 ArchNoninterference,build 验证器够不着(非 reach-B 失败) |
| **no valid rewrite** | **31** | 9 NO-PATH + 9 REACHED-B-NOT-FASTER + 11 ERROR(REPL JVM 崩)+ 1 CHANNEL-FAIL + 1 TIMEOUT |

合计 **13 + 1 + 31 = 45**。**10 confirmed faster** 的细分(诚实标置信)——只有 4 个有干净低噪声/stock 确认,另 6 个 build-green 且 in-REPL 高 margin 但**干净复核被 proof-cache 污染**(orig 重复施用命中 Isabelle proof cache → ~0ms,复核不可信,只能退回 in-REPL margin):

| 行 | LLM 手法 | 加速(threads=1 elapsed 代理) | 置信 |
|---|---|---|---|
| **empty_slot_pas_refined:1103** | 定向 dest 事实 | **23.3%**(stock 串行铁数 188.7→144.8s) | **最高(stock)** |
| is_derived_cap_arch_asid_issues:198 | 定向 simp 集 | 54.7%(复核噪声 5.4%) | 高(干净复核) |
| insert_cap_child_corres:332 | clarsimp + 定向 | 18.4%(噪声 0.5%) | 高(干净复核) |
| cap_insert_pas_refined:1032 | `simp del` | 8.1%(噪声 1.3%) | 中(干净复核,幅度小) |
| rm_affects/abd/in_whileLoop/requiv_device/invoke_cnode/dmo_user | 换序 / `simp only` / 定向 / clarsimp | 52%–94%(in-REPL margin) | 中(build-green + 高 margin,**干净复核被 proof-cache 污染、未独立坐实**) |

**与消除实验的对齐 = 3 个共享 lemma**(本集 high_value 与消除集 `wasted_hv_candidates.json` 的 lemma 交集;同一 lemma 上两条路的直接对比)。**两个数据集对这 3 行记录的成本完全一致**(下表"行成本"列,wasted_hv = high_value 逐字相同)——这本身就是两集**数据对齐**的硬证据:

| lemma:line | 行成本(两集一致) | 消除(reach-B,§3.4 wasted 臂) | 缩减(本节) |
|---|---|---|---|
| `requiv_device_mem_eq:811` | **10.49s** | NO-PATH(失败;DFS 跑 167s 后放弃) | ✅ **52% 更快**(build 验证) |
| `requiv_user_mem_eq:859` | **10.85s** | NO-PATH(失败;DFS 178s) | REACHED-B-NOT-FASTER(本已紧凑、无 slack) |
| `SAC_partsSubjectAffects_exceptT:921` | **22.23s** | NO-PATH(失败;DFS 91s) | REACHED-B-NOT-FASTER |

> 注:§3.4 列的 `167s/178s/91s` 是 **DFS agent 跑到 NO-PATH 的运行时**(`wasted_dfs.log` 的 `effort=0` 那列),**不是行的成本**;行成本是上表的 10.49/10.85/22.23s(两集逐字一致)。早稿曾把二者混写,此处更正。

**同一 lemma 上,消除全失败、缩减赢 1 平 2** —— 印证"消除够不着、缩减能动";而 `requiv_device`(消除 NO-PATH、缩减 52%)是同一行上两条路最干净的反差。

> **案例研究(分布外 / key-path anecdote,⚠️ 不计入上面 45 的系统样本)**
>
> Invoke_C:3113 是**手工**(grep `(* slow *)` 注释 + 开发者标注)找到的,**不经过 `high_value_candidates.json` 的采样流程**(它在 CRefine,被 `EXCLUDE_SESSIONS` 划为 infeasible),故**单独列、不进 §3.5.3 分母**。它的价值是:证明搜索缩减**在关键路径的硬 lemma 上也可能成立**——但因手工发现 + 缺精确 timing,**不作为系统证据**。
>
> 案例:`Invoke_C:3113 apply fastforce (* slow fastforce *)`(开发者**亲手标注慢**,golden elapsed 130s),缩成 seL4 别处证**同目标** `tcb_st_refs_of'(tcbState obja)={}` 用的 canonical 快版 `apply (fastforce simp: tcb_st_refs_of'_def elim: pred_tcb'_weakenE)`。**重建 C 链 heap(5h15m)后 stock build 验证 → 正确(OK)**;但**精确加速 ms 测不出**(串行 CRefine build >3h 超时;并行 build 的 per-line 被 `parallel_proofs` 吸收;关 `parallel_proofs` 又破坏对并行敏感的证明)。**结论:正确性已坐实 + 强证据更快(原 130s、变体是别处的 canonical 快版),但 timing 未量化 ⇒ 只作 anecdote、不计入系统成果。**

#### 3.5.4 分析:per-lemma 真加速,wall 不动是数据集 + 并行的必然

- **lemma 本体(干净 own-session stock CPU)层面:LLM 缩减是真加速、可行,但有限**——**56 feasible 全扫**(§3.5.3 更新块):24 个变体干净测 → **7 个稳健真加速(≥20%)**(含 2 个近乎消除 + empty_slot 省 40s)、3 临界、9 噪声/更慢、5 build-fail;另 ~22 候选 NO-PATH(LLM 缩不动)。**早稿基于 in-REPL 的"10 confirmed faster"虚高约 3×,已被全扫取代。** 这是本报告里**唯一为正**的 search 子角度(§3.1–3.4 全负);而 CRefine 关键路径 simp 经 §3.6 直测**不可缩**。
- **为什么不反映到 wall(双重原因,都已实证)**:① **数据集层面**——feasible 45 行**按设计全在并行旁支**(关键路径 CRefine 的 48 条被划为 infeasible,§3.5.1);② **并行层面**——端到端实测:把 empty_slot+cap_insert 应用后重建 Access ×3,**914→902s,delta 13s < 组内噪声 ±32s**([`build_wall_test.py`](../../../tools/seL4-proof-search/Isa-Repl/build_wall_test.py)),省的 CPU 落进并行 slack。**所以"per-lemma 加速 ≠ build 加速"既是数据集选择的结果、也是并行调度的结果**,与 §4 的 wall 负结论完全自洽。
- **测量纪律(本节硬知识,详见 [`measurement-tools.md`](../../../.claude/skills/isabelle_prover/references/measurement-tools.md))**:per-lemma 的正确度量 = **CPU 时间(lemma 串行)**;但干净测量极难——in-REPL 有 proof-cache + 冷启动污染、command_timings 只存 elapsed 且跨并行 build 噪声 30–60%、whole-theory/wall 被并行隐藏、串行 build 太慢、IsarLite 坏。最干净可得 = `threads=1` 串行 build 读 command_timings(empty_slot 23.3% 即此)。**注:§3.6 已用 own-session 单 theory build 攻克 CRefine 的测量成本,此处"CRefine 不可行"已被 §3.6 取代。**

### 3.6 关键路径直测:own-session + goal-aware + anon% 扫描(闭合 §4.5 的 open question)

§3.5 的 feasible 45 行**按工具边界全在并行旁支**,关键路径 CRefine 只有 `Invoke_C:3113` 一个**分布外 anecdote**(timing 未量化)。§4.5 因此把"缩减在关键路径上动不动 wall"标成 **open question,需专门实验**。§3.6 就是那个专门实验——直接在 CRefine 上测搜索缩减。

**这一节要回答的不是"我们试了几条 rewrite 有没有运气好的一条",而是更强的问题:关键路径 lemma 到底是"方法受限所以暂时没调出来",还是"其主耗时本身就不属于可缩的 search slack"。** 要区分这两者,实验必须同时满足两点:① **能在低噪声条件下稳定重跑关键路径上的单个 theory / 单条慢命令**,否则任何"没加速"都可能只是整 session build 太吵、太慢、测不准;② **能看到慢 proof 的内部成本结构**,即时间究竟花在少数可删具名规则上,还是花在不可避免的 def-unfolding / simplifier 真功夫上。`own-session` 解决①,`simp_trace`+`anon%` 解决②。只有两者合在一起,`no speedup` 才能被解释成"**无优化空间**"而不只是"**方法没打中**"。

#### 3.6.1 own-session 单 theory build:攻克 CRefine 的两道墙

CRefine 测不了的两个原因:① Isa-REPL **init 不动**重 session(重建 heap 34min / gateway 超时 / churn-OOM);② 整 CRefine session build **2.5h/次**(实测 A/A 探针 wall 9467s),且 per-step 默认并行噪声 **12–54%**(同源 A/A 实测)。

**解法 = own-session 单 theory build**([`crefine_ownsession_probe.sh`](../../../lemma-staticize/scripts/crefine_ownsession_probe.sh) / [`goal_aware_reduce.py`](../../../tools/seL4-proof-search/Isa-Repl/goal_aware_reduce.py) `_mk`):把目标 theory 改名 + imports 加 `CRefine.` 限定 → 建一个 `parent = CRefine`(**只读**)的 mini-session,`isabelle build` **只 elaborate 这一个 theory**(其余从已建 CRefine heap 秒加载)。**不碰 CRefine heap、不触 REPL init 墙**。成本 = 单 theory(分钟级),且单 theory 串行 → 噪声低。

验证:PSpace_C(200 行)mini-build **94s**;Tcb_C(4564 行)**20min**(vs 整 CRefine 2.5h);Tcb_C 9 个 tent-pole 行 own-session vs golden 并行**比值 0.72–0.95、中位 0.87x、排序保留**(远紧于全量并行 A/A 的 12–54%)→ own-session 是**干净、可比、低噪声**的关键路径 per-step 测量。

**它为什么能回答"关键路径能否优化"**:因为关键路径最终不是一个抽象概念,而是落在少数重 theory / 重命令上的真实成本。只要我们能把"整 session 2.5h + 并行噪声"降成"单 theory 分钟级 + 排序保留",就能对同一条慢 proof 做 baseline / variant 的可比实验。换言之,own-session 的作用不是为了"更快跑实验"本身,而是为了把关键路径从**不可测**变成**可证伪**:若某条缩减真能动关键路径,它必须先在这个低噪声单-theory探针上留下可重复信号;若连这里都没有信号,就不能再把失败推给整 session 的测量噪声。

#### 3.6.2 goal-aware 缩减(claude-driven + simp_trace profiling)

重 session 没有 in-REPL 廉价反馈(每轮验证 = 一次 own-session build),所以**前置 profiling 让 claude 一开始就 goal-aware**([`goal_aware_reduce.py`](../../../tools/seL4-proof-search/Isa-Repl/goal_aware_reduce.py),由 [`run_reduce.sh`](../../../tools/seL4-proof-search/Isa-Repl/run_reduce.sh) 按 session 路由:轻→in-REPL,重→goal-aware):

1. **profile**:把目标行的 method 用 `use [[simp_trace]] in \<open>…\<close>` **scope 到这一步**(避免全局 trace 抓错 goal),`isabelle process -T` → 该步真实 goal + 逐条 rewrite 规则频率 + **`anon%`**(匿名 def-展开 rewrite 占比);
2. **claude 决定**:goal + 规则频率 + 5 手法 + few-shot 经 proposer_host bridge 喂给 claude(**全程 transcript 记录**)→ claude 据证据做精确缩减;
3. **verify**:own-session build,封顶 **1.2× baseline wall**(爆炸变体快速杀,不再像盲试 auto→fastforce 烧 6.6h);接受 = build green 且整 theory wall < baseline 且目标行 < baseline。

这里的 `simp_trace` 不是为了"看个热闹",而是把慢 `simp` 的内部成本拆开。不开 trace,我们只知道"这一步花了 148s";开了 trace,我们才知道这 148s 是不是因为某条具名规则(例如 `add_Suc`)被重复施用数百次,从而值得 `simp del:` / `simp only:`,还是主要耗在系统自动做的定义展开与规范化上。前者意味着**存在明确抓手**,后者意味着**tactic 写法之外的真功夫**。也因此,goal-aware 不是"让 claude 更聪明一点"这么简单,而是强制它**按 profile 证据出招**:如果 profile 指向具名热点,claude 就删/限那条规则;如果 profile 没给出可操作热点,那本身就是"无 slack"的证据。

两个系统案例(claude **据证据**出招,非盲猜):

| 行 | 耗时 | anon% | profile 主导规则 | claude 决策 | 结果 |
|---|---|---|---|---|---|
| `CSpace_C:2408` | 148s | **93.8%** | cap_frame_cap_lift_def(26x,弱) | `simp only:` 具名规则 / `simp del` | **BUILD-FAIL / 无加速** → 不可缩(三证) |
| `Invoke_C:1134` | 68s | 43.4% | **`add_Suc` 624x** | **`simp del: add_Suc add_Suc_right`**(精准删 profile 报的 624x) | **build green + FASTER,但仅 2.7% wall / 1.4% line(噪声带内)** |

⇒ goal-aware 机器**正确工作**(claude 据真证据精准缩减),但**即便挑对靶子、删对规则,省的也只是噪声级**——`add_Suc` fire 624 次但每次极廉价,真耗时在不可删的 def 展开上。

#### 3.6.3 anon% 扫描:关键路径 simp 不可缩的硬证据

> **⚠️ 本小节结论已被 §3.7 部分推翻**:下表的 `anon%` 实为 `Adding rewrite rule "??.unknown"`(simpset 重复构造)占比,**不是** def-unfolding 计算占比;据此的"不可缩"判定失效,关键路径行实测**局部可缩 7.4%**。保留本节以存证据链。

对 CRefine simp tent-poles 扫 anon%([`anon_scan.py`](../../../tools/seL4-proof-search/Isa-Repl/anon_scan.py),scoped simp_trace,`runs/anon_scan.json`):

| 行 | 耗时 | **anon%** | 主导具名规则(fire) | 判定 |
|---|---|---|---|---|
| `CSpace_C:797` | 72s | **99.9%** | cap_lift_def (3) | 不可缩(纯 def 展开) |
| **`Fastpath_C:2052`** | **408s** | **99.7%** | ctes_of_not_0 (107) | 不可缩(**单条最贵的也是**) |
| `Retype_C:7071` | 53s | **98.3%** | atLeastatMost_empty (2113) | 不可缩 |
| `CSpace_C:2408` | 148s | **93.8%** | cap_frame_cap_lift_def (26) | 不可缩(已三证) |
| `Invoke_C:1134` | 68s | 43.4% | `add_Suc` (624) | 可删但仅省 2.7%(噪声) |
| `CSpace_C:2945/3009/2998` | 103/88/57s | n=0 | — | ⚠️ 工具未捕获(`case_tac,simp_all` 的 wrap 没 trace 到)→ inconclusive |

这里的 **`anon%`** 指:一次 `simp` 中,被 trace 归为**匿名 rewrite / def-unfolding** 的施用占比。它与可点名的具名规则(如 `add_Suc`,`cap_lift_def`)相对。直观上:
- **anon% 低** = 成本集中在少数具名规则上,通常还有 `simp del:` / 定向 `simp:` 的优化抓手;
- **anon% 高** = simplifier 大部分时间花在匿名定义展开、内部归约、规范化这些"基础工序"上,此时删掉一两条具名规则通常动不到主体成本。

因此,`anon%` 不是一个旁枝统计量,而是"**有没有 search slack 可削**"的判别器。若慢点的 profile 显示高 `anon%`,那说明它慢在 **必须做的定义展开真功夫**,不是慢在某条搜索规则被浪费性地乱试。

**5 个干净 profile**:**4/5 ≥ 93.8% 匿名 def-展开**(主导具名规则只 fire 几~上百次、可忽略),包括**最大的 Fastpath_C:2052(408s,99.7%)**;唯一低匿名的 Invoke_C(43.4%)删对规则也仅省 2.7%。**即:关键路径 CRefine simp 的耗时是不可删的 def-unfolding 真功夫,不是某条可删规则的浪费性重复施用。**

#### 3.6.4 结论:关键路径直测 → 不可缩(open question 闭合)

§3.6 把 §3.5/§4.5 的"关键路径只有 anecdote、缩减动不动 wall 未知"升级为**量化负结果**:在 CRefine 关键路径上,**三条搜索缩减算子(消除 §4.2 / 降级 §4.3 / 缩减 §3.5)全部证伪**——own-session + goal-aware 让缩减**第一次**真在关键路径上被系统测试,结果是 anon%-主导的不可缩。`Invoke_C:3113` anecdote 的"关键路径可能可缩"假设,被 §3.6 的系统数据**否定**(它当年只验了正确性、没验 timing;§3.6 量化后是噪声级)。**工具链(own-session 探针 + goal-aware + anon% 扫描)是可复用资产。**

换言之,这一节的负结论不是"我们的方法在关键路径上太弱,所以没调出来",而是"我们先把关键路径变成可测对象,再把慢 proof 的内部成本拆开,最后观察到主体成本落在高 `anon%` 的 def-unfolding 上"。这是**关于成本结构的证据**,不是单纯"若干次优化尝试失败"。因此它支持的判断是**关键路径 search 优化空间本身极小**,而非"尚待更强 agent / 更多 prompt engineering"。

> **⚠️ 重要更正(见 §3.7)**:§3.6.3 的 `anon%` 判据后来被证明**量错了对象**——它计的"rewrite"里 93.8%–99.7% 其实是 `Adding rewrite rule "??.unknown"`(**simpset 重复构造**),不是 def-unfolding 计算。据此得出的"关键路径不可缩"在 §3.7 被**部分推翻**:CSpace_C:2408 实测可缩 7.4%(绿)。§3.6.4 的"open question 闭合"应降级为"anon% 证据有缺陷,关键路径**局部可缩**、但廉价全局删被承重规则挡死"。

### 3.7 第四条思路:从"引用"角度缩减(reduce references)——纠正 §3.6 的 anon% 假象

§3.1–3.6 都在"**改一条 tactic 的写法**"这个轴上。本节换轴:**不改写法,只减少 tactic 面对的 ambient 引用集**(全局 `[simp]`/`[wp]` 声明、局部再加进 simpset 的规则)。动机:若某条关键路径 simp 慢在"被喂了太多规则去匹配 / 去试",那缩小引用集能**一次让作用域内很多 lemma 都变快**——这是之前所有 per-lemma 点修复都缺的**全局乘数**性质,也是唯一有希望绕开"per-lemma 加速落进并行 slack"的角度。

**工具**(run 目录 [`runs/reference-reduction-20260701/`](../../../tools/seL4-proof-search/Isa-Repl/runs/reference-reduction-20260701/)):
- [`trial_probe.py`](../../../tools/seL4-proof-search/Isa-Repl/trial_probe.py):比 §3.6 profiler 更细的 trace 解析器。旧 profiler 只数 `rewrite rule "X"`、且 `simp_trace_depth_limit=1`,把四件事混成一个数;新解析器分开:真 fire 的 `Rewriting:`、条件规则 `Trying to rewrite:` 及其 **FAILED(白试)/ SUCCEEDED**、以及 **`Adding rewrite rule`(往 simpset 加规则,根本不是 rewrite)**。
- [`mutation_test.py`](../../../tools/seL4-proof-search/Isa-Repl/mutation_test.py) / [`theory_simpdel_test.py`](../../../tools/seL4-proof-search/Isa-Repl/theory_simpdel_test.py):own-session 单 theory build 的 A/B(读真 `command_timings`),分别测"某行 `del:` 一条规则"与"整 theory `[simp del]` 一条规则"的 wall+绿变化。

#### 3.7.1 纠正:§3.6 的 anon% 大部分是 `Adding rewrite rule` 假象

对两条最贵的 tent-pole 用新解析器实测(`trial_cspace2408b.json` / `trial_fastpath2052.json`):

| 行 | 旧 anon% 计的"rewrite"总数 | 其中是 `Adding rewrite rule "??.unknown"`(建 simpset) | 真正 `Rewriting:` 操作 |
|---|---|---|---|
| `CSpace_C:2408`(135s) | 2853 | **2661(93.3%)** | **146** |
| `Fastpath_C:2052`(408s) | 110527 | **110216(99.7%)** | **153** |

§3.6.3 判为"99.7% 匿名 def-展开、不可缩"的那 99.7%,**逐字就是 `Adding rewrite rule "??.unknown"`**——去重后是**同一小批局部等式**(如 Fastpath 的 `msgLength (messageInfoFromWord msginfo)` 被加 4189 次、CSpace 的 `capa ≡ …` 被加 246 次)在 `intro conjI`/`rule conjI` 炸出的大量子目标里、**被每个子目标的 simp 反复重加进 simpset**。即 §3.6 的 anon% 度量的是"**往 simpset 加了多少匿名规则**"(≈引用集规模 × 子目标数),**不是** def-unfolding 计算量。真正的 rewrite 只有一两百次。**§3.6.3"不可缩"的硬证据据此失效。**

#### 3.7.2 浪费的条件 trial:`ctes_of_not_0` 被试了又失败 96–107 次

拆到条件规则层,两条行上都有一条全局 `[simp]` 规则在白做功——
`ctes_of_not_0 [simp] = valid_mdbD3'`(即 `⟦ctes_of s p = Some cte; valid_mdb' s⟧ ⟹ p ≠ 0`,见 `proof/crefine/ARM/SR_lemmas_C.thy:841`)。它挂在每个 `_ ≠ 0` 子项上,去 discharge 那个贵前提 `ctes_of s p = Some cte`,大多失败:

| 行 | `Trying`(条件 trial) | **FAILED(白试)** | SUCCEEDED |
|---|---|---|---|
| `CSpace_C:2408`(depth-2,`d2_cspace2408.json`) | 96 | **96(全部 ctes_of_not_0)** | 184(其他) |
| `Fastpath_C:2052`(depth-1) | 221 | **221**;其中 `ctes_of_not_0` 107、`list_emb_Nil2` 51、其余库规则 | 72 |

`ctes_of_not_0` 在两条行上都是头号白试规则,`valid_mdbD3'` 挂 `[simp]` ⇒ 全 CRefine 的每个 `_≠0`/`_=0` 目标都会触发它 → **疑似系统性浪费**。(另两条 `list_emb_Nil2`/`implies_True_equals` 是 Sublist/HOL 库规则,超出可改范围,且 depth-1 归因有噪声。)

#### 3.7.3 关键路径行**可缩**(推翻 §3.6),但廉价全局删被承重挡死

两个 own-session A/B(`mut_cspace2408.log` / `tdel_cspace.log`):

| 实验 | 操作 | green | 结果 |
|---|---|---|---|
| **单行 `del:`** | `CSpace_C:2408` 那步 `simp … del: ctes_of_not_0` | **✅ 双臂绿** | 行 **135.4→125.4s = −7.4%**(theory/wall 同向 −9%/−8.6%,但含跨 build 噪声,只信 per-line) |
| **整 theory `[simp del]`** | theory 顶部 `declare ctes_of_not_0[simp del]` | **❌ BUILD-FAIL** | 断在 `CSpace_C:1064`(目标 `mdb…≠0 ∨ …=0` 真需它 fire) |

两条硬结论:
1. **关键路径行不是"不可缩"**——删掉那条白试规则,绿、且 per-line 快 7.4%。**§3.6.4"关键路径不可缩(open question 闭合)"被此单点反例推翻**(§3.6 的 anon% 证据本身又是 §3.7.1 的假象)。⚠️ 7.4% 接近 own-session 噪声地板(§3.6.1 标定 own-session 比值 0.72–0.95,即 run-to-run ~5–28%),方向可信、幅度待复测。
2. **但"删全局 `[simp]` 声明→全局提速"这个廉价 (b) 杠杆不成立**——`ctes_of_not_0` **承重**(整 theory 删就断证明)。同一条规则**在少数地方 fire 承重、在多数地方白试**,不能简单删。

#### 3.7.4 结论:引用角度 = "局部可缩已证 + 廉价全局删证伪 + 只剩两条贵路"

- **正**:关键路径确有可去掉的浪费(白试条件规则),单行 `del:` 实测 −7.4%(绿);连带**纠正了 §3.6 的 anon% 假象**(那 99.7% 是 simpset 重建,不是不可缩的 def 展开)。
- **负**:最大的可去靶子 `ctes_of_not_0` 承重,**廉价的"删全局声明"死路**;剩下能去的白试都是 HOL/库规则(不可改)。
- **只剩两条有全局-wall 潜力、但都不便宜的路**(待决策,非本轮结论):**(A) 去全局 `[simp]` + 只在断掉的少数行 `simp add:` 补回**(杀掉所有白试、保留承重 fire;需迭代补断点 + 全 CRefine 绿测);**(B) 规则再工程**(收紧 `ctes_of_not_0` 的触发模式 / 让廉价失败前置,减白试保 fire;要改规则本体 + 上游接受性存疑)。

**工具链(`trial_probe.py` / `mutation_test.py` / `theory_simpdel_test.py` + `SIMP_TRACE_DEPTH` 参数化)是可复用资产**:第一次把"某条 ambient [simp] 规则在关键路径上白试多少次、去掉它省多少 wall、去了会不会断"变成可机械测量。

#### 3.7.5 方向关闭(引用轴收口)

"调整引用"分两层,均已收口,方向关闭:
- **A. import 层(= flatten,含"删无用 import"①与"重排 import DAG"②)**:①⊆②,只有关键路径级的 ② 能动 wall = flatten;而 flatten 的**验证**需在**不含父 heap** 的环境重建(判 import 是否语义必需),§3.7 的 own-session 廉价闸**救不了**(父 heap 仍含被删 theory 的内容,测不出提速/断裂),`thm_deps` 又不稳 ⇒ 维持结构报告 §1.4 的**阻塞**结论。
- **B. 规则属性层(§3.7)**:有 7.4% 正信号但规则承重、廉价全局删证伪;进一步的 remove-global+re-add / 规则再工程判为**收益少、案例不多**,**本轮不投**。
- **附带澄清**:"扫 wp/simp 空间删**没用到**的声明"这条**本身无效**——discrimination net 让匹配不上的规则被廉价剪掉、留着近乎免费(simpset 大小亚线性);真正费时的是"**用到了但条件 discharge 失败**"的白试,那才是 B,而 B 承重不可廉价去除。

⇒ **引用轴无 LLM 可廉价推进的格子,关闭。** 至此两大方向(改 lemma 本身 / 改结构)全部闭环。

---

## 4. 结论

**按 §0.1 的三个子实验依次回应**,逻辑相扣:先排除"agent 太弱"这个干扰解释(4.1 控制变量)→ **A 消除:结构性错误任务(4.2)→ B 降级:前提被证伪(4.3)→ C 缩减:唯一为正、但不动 wall(4.4)**→ 全局测量边界与真正的去向(4.5)。

### 4.1 前提(控制变量):方法够强,不是 agent 太弱

同 12 个佐证案例(§3.1):**Blind 单跑干净 12/12、Stateful 累计 12/12、菜单 DFS 仅 3/12**(`corrob-{blind,dfs,stateful}-*/summary.tsv`;每轮 claude -p 交互见 `genstat-transcript-*.jsonl`)。⇒ 开放词表 LLM 完全有能力重建 reach-B 路径。**本节排除一个干扰解释**:后面"reach-B 不提速"不是 agent 太弱,而是任务本身的问题。

### 4.2 子实验 A(消除):结构性错误任务——能重建的无价值,有价值的重建不出

reach-B 重建与"提速"系统性错位,三块数据互锁:

- **可重建侧太便宜(无价值)**:10 个成功重建且 stock-build 通过的候选,golden `command_timings` 实测 **T_原始 < 100ms**(Access/CNode,strong)/ **< 171ms**(RWHelper,weak),**低于 Isabelle ~0.1s 逐命令记录地板**——便宜到本体都不计时(派生见下)。
- **有价值侧不可达**:秒级慢 lemma reach-B **三方 0/7**(§3.2,`wasted_dfs.log`),两种失败态——case-split 树太大(`SAC_partsSubjectAffects_exceptT` 换静态留 11 子目标)、goal 太大致 claude -p 自身超时(`requiv_user_mem_eq` 400s 空响应)。
- **机制(为什么注定)**:static 化只能 **re-derive**(重做归一化/合一/resolution),省的只是搜索/探索、**核心推理照跑**;小 lemma 搜索成本本就极小 → 净收益 ≈ 0 甚至为负(§3.3 实测 blast-static 为负,`ceiling.log`)。

⇒ **reach-B 重建在能成的频段无价值、在有价值的频段不可达**——对"提速"是个错的任务。

**可重建侧 < 100ms 的量化派生**(`C_i = T_原始 − T_确定路径 ≤ T_原始`,确定路径 = 移除搜索的极限;按 `SCREEN → VERIFY(stock)→ REPORT(golden)` 测):

| 步骤 | 工具 | 结果 | 溯源 |
|---|---|---|---|
| VERIFY(正确性) | `check_theory_selfqual.sh --patch`(stock build) | **9/11 reach-B 路径真 build**(12 候选中 `is_transferable_IRQ` 无 verify 输出,以产出结果的 11 为分母);**2 个 in-REPL 假阳性**(`pas_refined_sita_mem`/`sep_heap_domD'`,stock 失败)→ 早先 reach-B"成功"**~18% 假阳性** | `runs/ceiling-pilot-*/ceiling.log` |
| SCREEN(rough) | `time_rewrite.py`(in-REPL,drop-warmup/median7/A-A 地板/>2σ) | 天花板几十 ms;**blast 系为负**(static 比 blast 慢) | 同上 |
| REPORT(权威) | golden `command_timings` + `C_i ≤ T_原始`(`report_orig_timing.py` **v2**) | **T_原始 < 文件地板(严谨)**:候选 lemma 区间内 0 记录,但文件被密集计时 → 三支柱定界(见下)⇒ Access/CNode 候选 **< 100ms**,RWHelper 对 **< 171ms**(弱) | `report_orig_timing.py`(v2,BELOW-FLOOR verdict),`runs/report_ceiling_orig.json` |

**三支柱**(`report_orig_timing.py` v2,修正 v1 的"miss=tiny 钦定"):① 文件确被密集计时(Access 139 / CNode 221 / RWHelper 6 条记录);② 经验记录地板 ~100ms(三文件 min elapsed = 103 / 100 / 171ms,系 Isabelle ~0.1s 记录阈值指纹);③ offset→line 映射经验证精确(最重记录落在真重命令行,候选区间内 0 记录、候选行本身是 trivial 单行 `by`)。⇒ 候选 `by` 命令 **< 文件地板**(Access/CNode <100ms strong;RWHelper <171ms weak)。下三表为实测数据:

**重跑数据(`report_orig_timing.py` v2,golden `command_timings`,2026-06-27)**:

逐 lemma verdict(全部 BELOW-FLOOR,区间内 0 记录):

| lemma | thy | verify | 源码区间 | 区间内记录 | T_原始 上界 | 置信 |
|---|---|---|---|---|---|---|
| auth_graph_map_memI | Access_AC | BUILDS | 80–84 | 0 | **< 103ms** | strong |
| reply_masters_mdbD1 | CNode_AC | BUILDS | 470–474 | 0 | **< 100ms** | strong |
| sep_heap_domD | RWHelper_DP | BUILDS | 168–172 | 0 | < 171ms | weak |
| sep_irq_node_domD' | RWHelper_DP | BUILDS | 178–182 | 0 | < 171ms | weak |
| tcb_domain_map_wellformed_mono | Access_AC | BUILDS | 228–232 | 0 | **< 103ms** | strong |
| is_transferable_Endpoint | Access_AC | BUILDS/neg | 114–124 | 0 | **< 103ms** | strong |
| is_transferable_Untyped | Access_AC | BUILDS/neg | 106–107 | 0 | **< 103ms** | strong |
| is_transferable_Ntfn | Access_AC | BUILDS/neg | 112–113 | 0 | **< 103ms** | strong |
| is_transferable_IRQ | Access_AC | BUILDS/neg | 108–109 | 0 | **< 103ms** | strong |
| is_transferable_Zombie | Access_AC | BUILDS/neg | 110–111 | 0 | **< 103ms** | strong |

每文件 elapsed 分布(支柱 1+2:文件被计时 + 记录地板):

| 文件 | 记录数 | min(地板) | median | max |
|---|---|---|---|---|
| `proof/access-control/Access_AC.thy` | 139 | **103ms** | 355ms | 17925ms |
| `proof/access-control/CNode_AC.thy` | 221 | **100ms** | 364ms | 163780ms |
| `proof/capDL-api/RWHelper_DP.thy` | 6 | 171ms | 327ms | 811ms |

映射精确性校验(支柱 3:最重记录落在真重命令行,候选行是 trivial 单行):

| 类型 | 行 | golden elapsed | 源码 |
|---|---|---|---|
| 最重记录 | `Access_AC:647` | 17925ms | `apply (time_methods …)` |
| 次重 | `Access_AC:621` | 9194ms | `apply (all \<open>fails …)` |
| 次重 | `Access_AC:1530` | 8791ms | `apply (erule integrity_obj_atomic.cases …)` |
| 候选行 | `Access_AC:83` | (无记录) | `by (fastforce simp add: auth_graph_map_mem)` |
| 候选行 | `Access_AC:107` | (无记录) | `by (blast elim: is_transferable.cases)` |
| 候选行 | `Access_AC:231` | (无记录) | `by (auto simp: tcb_domain_map_wellformed_aux_def …)` |

即:**可重建侧搜索加速天花板 < 100ms**(低于 Isabelle 记录阈值,结构性可忽略);不可重建的慢 lemma 那里是真证明搜索,天花板收不紧——但那已超出"移除搜索"的范畴。这把 4.2 的"无收益"从粗筛升级为**严谨定界**。

### 4.3 子实验 B(降级):wasted-classical 前提被证伪——classical 在做真功

不重建、只把 search 降级成更便宜的 clarsimp:§3.4 行为式筛器(`pilot_ablate2.log` / `rescan_ablate.log`)上 **23/23 有效 swap clarsimp BUILD-FAIL**(留 11 / 12 / 143 子目标;3 个 blast 行空替换已剔除),困难臂补测亦 4 fail + 3 重 session 未决——**classical 搜索在做真功,不是"白费"**。⇒ search 的两个子角度(**移除** reach-B、**降级** clarsimp)都关上;"文本启发式高估搜索"被行为基准证伪。**范围限定**:此证伪严格成立于**可行为式验证的 Access/InfoFlow 子集**,非"全 seL4 clarsimp 降级普遍失败"(CRefine/Refine 重 session 未系统覆盖,见 §3.4 外部效度)。

### 4.4 子实验 C(缩减):唯一为正——per-lemma 真加速,但按数据集 + 并行注定不动 wall

与 A/B 相反,**保留 tactic、只缩小搜索空间是有效的 per-lemma 优化**(§3.5,本报告唯一为正的 search 子角度):

- **per-lemma 真加速(干净 own-session stock CPU 全扫,§3.5.3 更新块)**:**56 feasible 全扫 → 7 个稳健真加速(≥20%)**:is_stateAssert_gets 100% · in_whileLoop 97.9% · is_derived 56.5% · set_cap_valid_arch 54.9% · requiv_device 30.7% · empty_slot 22.7%(省 40s) · weak_derived 22.6%。手法多样(`simp only:` 前置 / `simp del:` / 定向 `simp:` / 换序 / clarsimp / 直接 `frule`)。**早稿基于 in-REPL 的"10 confirmed faster"虚高约 3×**(in-REPL 报 22 FASTER、含把更慢报成更快的假阳性),已被全扫 stock 铁数取代。
- **同一 lemma 上 A 败 C 胜**:消除集与缩减集 3 个共享 lemma 上,reach-B 全 NO-PATH,而 `requiv_device_mem_eq` 缩减拿到 52%(§3.5.3)——同一行两条路最干净的反差。
- **但 build-wall 不动,且是注定的(双重原因,都已实证)**:① **数据集层面**——feasible 45 行**按工具边界全在并行旁支**(关键路径 CRefine 的 48 条因 REPL init 墙被划为 infeasible,§3.5.1);② **并行层面**——端到端实测把 empty_slot+cap_insert 应用后重建 Access ×3,**914→902s,delta 13s < 组内噪声 ±32s**,省的 CPU 落进并行 slack。
- **关键路径已直测、且不可缩(§3.6,升级了旧 anecdote)**:`Invoke_C:3113` 旧 anecdote 当年只验正确性、未量化 timing。§3.6 用 **own-session 单 theory build** 攻克 CRefine 测量成本(单 theory 分钟级、绕开 REPL init 墙),再用 **goal-aware(simp_trace profiling → claude 据证据缩减)** 系统测了 CRefine simp tent-poles:**anon% 扫描显示 5 个干净 profile 里 4 个 ≥93.8% 是不可删的匿名 def-展开(含最大的 Fastpath_C:2052,408s/99.7%),唯一低匿名的 Invoke_C:1134(43.4%)删对规则也仅省 2.7%(噪声)**。⇒ 关键路径 CRefine simp **不可缩**(量化负结果),旧 anecdote 的"关键路径可能可缩"被否定。**⚠️ 更正(§3.7)**:此处 anon% 后被证明量的是 `Adding rewrite rule`(simpset 重建)、非 def 展开;`CSpace_C:2408` 实测删一条白试全局 `[simp]` 可缩 **7.4%(绿)**,故"不可缩"被推翻为"**局部可缩、但廉价全局删被承重规则挡死**"。

⇒ **C 是 search 角度唯一能产出真加速的操作,但仅在 per-lemma 层、且只在并行旁支**;它不矛盾于"build wall 不动",反而和 A/B 一起把"为什么 wall 不动"解释到底:**能动 wall 的关键路径,缩减(§3.6 直测)/消除(§4.2)/降级(§4.3)三条算子全部不可缩。**

### 4.5 全局测量边界,与真正的去向

- **边界**:本报告大部分测的是 **wall / elapsed @ 文件粒度**(`check-theory.sh` = `date` 墙钟、golden = `elapsed`,**均无 cpu**);per-lemma 的正确度量本应是 **CPU(lemma 串行)**,但干净 CPU 当前不可测(golden 无 cpu、IsarLite 坏、profiler 撞 future/setup 墙)。最接近的代理 = `threads=1` 串行 build 的 per-command elapsed(§3.5 的"铁数"即此)。故 A/B 的"无收益"是 **wall 结论**,C 的"真加速"是 **per-lemma(threads=1 elapsed 代理)结论**——两者口径不同、互不矛盾。
- **关键路径直测已闭合(旧版本节标的 open question)**:本报告早稿把"缩减在关键路径(CRefine)上动不动 wall"标成 open（需专门实验）。§3.6 做了那个实验——own-session 单 theory build 攻克测量成本、goal-aware + anon% 扫描系统测 CRefine simp tent-poles,结论是**不可缩**(4/5 ≥93.8% 匿名 def-展开、唯一可削的仅省 2.7% 噪声)。**⚠️ §3.7 更正**:§3.6 的 anon% 量的是 simpset 重建(`Adding rewrite rule`)、非 def 展开;关键路径行 `CSpace_C:2408` 实测删一条白试全局 `[simp]`(`ctes_of_not_0`)可缩 **7.4% 且绿** ⇒ 关键路径**局部可缩**,open question **重新打开**;但"删全局声明"因规则承重(整 theory 删断在 CSpace_C:1064)不成立,只剩两条贵路(去全局+局部补回 / 规则再工程)。
- **真正的 build-wall 杠杆**不在改 tactic:per-lemma 成本是文件 wall 的极小分母,关键路径的 simp 又是不可缩的 def-展开真功夫(§3.6)。杠杆在 **session DAG / 并行 / heap 缓存**这条**结构**轴上——本报告只走"改 tactic 内容"这条轴,**结构轴是另一份独立报告的主题**。
- **与结构优化报告的互补(两轴合起来才是完整回答)**:[`reports/proof-structural-optimization/REPORT-2026-06-26.md`](../2-modify-structure/REPORT-2026-06-26.md) 系统地查了**结构轴**(不改证明内容)的全部杠杆,结论同样收敛到"几乎无空间":① **并行已满**——`-j2×threads16` 是 29GB/16 核逼出的硬件上限(`-j×maxheap≤RAM`),DRefine 代理实验 factor 1.89→8.09 证明并行 headroom **本就全在、已压榨到硬件**,调旋钮不算优化;② **冗余只有一个**——全 l4v 仅 `CRefineSyscall` 一个 session 满足"重 + 无守卫 + 100% 重算",删它 −29% wall 但**上游已明确拒收**(仅本地可用);③ **唯一未证伪的真结构方向(theory-DAG flatten / 缩关键路径)被基建墙挡住**(CRefine 父 heap 被清理、`thm_deps` 多版本不稳),且上界有限;④ CRefine `command_timings` 拆解:**91.5% 是真 proof tactic(simp work + VCG),setup 仅 2.6%**——连"瓶颈在 crunch/locale setup"的假设都被证伪,结构上无杠杆可碰。
- **两份报告合起来的完整裁定**:**改 tactic 内容(本报告)** 只有"缩减"产出 per-lemma 真加速、但不动 wall;**改结构(那份报告)** 并行已满、冗余拒改、flatten 被基建挡 ⇒ **固定硬件 + 不改证明内容 + 不被上游拒,seL4 proof 墙几乎无真优化空间**;墙就是 refinement 数学本身,真正能 scale 它的是**更大硬件 / heap 缓存 / Docker 层缓存**(项目一贯结论)。

---

## 5. 溯源索引

| 结果 | 持久产物 | 提交 |
|---|---|---|
| 候选集(冻结+manifest) | `lemma-staticize/experiment-candidates/` | 主repo `313fbc6` 起 |
| 三方法 per-run 快照 | `lemma-staticize/runs/corrob-{dfs,stateful,blind}-*/`(`stdout.log`/`result.json`/`transcript.jsonl`) | submodule `fb69f1d` |
| claude I/O 审计 | `tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-*.jsonl`、`genstat-stateful-*.json` | — |
| ablation/筛器/计时日志 | `lemma-staticize/runs/_archived-logs/*.log` | (本报告归档) |
| Direction 4 ceiling 闭环 | `lemma-staticize/runs/ceiling-pilot-*/ceiling.log`、`{tools/seL4-proof-search/Isa-Repl,lemma-staticize}/runs/report_ceiling_orig.json`(v2,含 verdict/span/floor) | (本会话) |
| ceiling 工具 | `verify_path.py`(stock VERIFY)、`time_rewrite.py`(SCREEN,噪声纪律)、`report_orig_timing.py`(REPORT,golden,**v2**:lemma-名定位区间 + BELOW-FLOOR 三态 verdict + 文件 elapsed 分布) | (本会话) |
| 实测筛器工具 | `session_candidates.py`、`wasted_ablate.py`(frac_saved/non_overlap) | (本会话) |
| 候选源 | `runs/{db_candidates,wasted_candidates,session_candidates,reachb_paths}.json` | — |
| 方法论 | `tools/seL4-proof-search/Isa-Repl/GENSTAT.md` | submodule `72a66e1` |
| 代码(三方法+修复) | `react_agent.py` / `genstat_stateful.py` / `proposer_host.py` / `run_corroboration.sh` / `wasted_ablate.py` / `session_candidates.py` | submodule `0eb311e`→`9a929e4` |

每个 per-run 目录自洽(stdout + 结构化 result + 逐次 transcript,不互相覆盖),可独立复盘任一 case 的"被告知什么 → 提了什么 → REPL 裁决"。