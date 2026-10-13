# proof 层 tactic 改写:从"消除搜索"到"claude -p 自积累改写库"

> 本报告记录 2026-06-14 → 06-16 一次完整探索的**发展脉络与思考**:
> 我们想回答"能否通过改写 proof 里的 automation tactic 来加速 seL4 build",
> 一路做了三个方向(静态化 / fact 检索 / 换更快 tactic),用判别实验把结论钉死,
> 然后把方法从"启发式搜索"转向"claude -p 当改写 agent",最后给出一个
> **轻量、自积累的静态改写 + 参考库**方案设计。
>
> 一句话结论:**proof 层 tactic 改写不是 seL4 的 wall-time 杠杆**(慢在不可约的
> simp/classical 工作量);但"LLM 驱动 + 验证 + 积累"这条链路本身可行,价值在
> **可审计性 / 改写数据集 / few-shot 自改进**,而非速度。

---

## 0. 出发点与约束

- 目标(初):把热路径上 `auto`/`simp`/`fastforce`/`blast`/`clarsimp` 等"搜索型"
  tactic 改写成**确定性静态** tactic(`rule`/`erule`/`simp only:`/`unfold`…),
  以期减少 proof check 的 wall。
- 现有资产:`ab_agent.py`(A→B 静态化的深度受限 DFS,可选 LLM 排序菜单)、
  `react_agent.py`(ReAct 回路,经文件桥让 host claude 当 proposer)、Isa-REPL(py4j gateway)、
  29 个预建 session heap(注:**缺 AInvs / plain Refine**)、外部计时 gate(`gate.py`/`check-theory.sh`)。
- 计时纪律:in-JVM `time_run` 有 ~6–8ms IPC 底噪 + future 调度污染,只可粗筛;
  可信数字走外部 build 计时;同指纹、≥3 中位、报噪声带。

---

## 1. 方向一:静态化(完全消除搜索)

**做法**:`ab_agent` 在固定招式菜单上 DFS,目标是找到 audit 通过(无 automation)的静态路径到达原 tactic 的精确终态 B。

**fact 检索瓶颈定位**:`react_agent` 的 fact 只来自 hammer + 原 tactic 显式命名;`ab_agent` 的 `discover()`(find_theorems)**写了却从未被调用**(死代码)。于是接了两个 fact 接入点:
- **AP1**:把 `discover()`(find_theorems intro/elim/dest)接进搜索。
- **AP3**:replay 原证明到 done → `_extract_thm_deps(lemma)` 取证明项依赖 → 过滤基础设施噪声 → 域 lemma。机制经 HOL demo 验证(`by simp`→`List.rev_rev_ident`,`by auto`→`add.left_neutral`),并修了 locale 名解析(JAR 的 locale-aware 路径未经 gateway 暴露,在 Python 端复制其 fallback:bare 名失败则试 `Locale.lemma`)。

**消融结果(6 InfoFlow lemma / 8 target line,heuristic proposer)**:
baseline 1/8 → +AP1+AP3 **1/8,0 flip**。AP3 确实加了域 facts(+6、+21、+4…),AP1 加了 0(find_theorems 对这些 goal 无产出),但**多加 facts 没换来任何多解**。

→ **fact 检索不是主瓶颈。**

---

## 2. 判别实验:瓶颈到底是什么

对 `runs/` 现有 103 案例(85 个有搜索记录的失败)做归因:
- **70 / 85**:搜索**走了 6–18 步的长路径却收敛不到精确状态 B**(深例:17、18 步)。
- 15:连有效第一步都没有(多是 `by blast` 纯逻辑)。

**结论(决定性)**:静态化失败的主因是**本质难度**——`auto`/`fastforce` 的输出要用静态步骤精确重演,路径太长,确定性搜索走不到头;"必须精确等于 B_sig"对长 automation 极残酷。不是缺 fact、不是 LLM 不够、不是搜索预算。

---

## 3. 方向二:换更快的 tactic(退而求其次)

**想法**:不重构,只把整体方法换成到达同一 B 的更便宜方法(`fastforce`→`clarsimp`/`force`,`metis`→`meson`,`simp only:`…)。文献支撑:Sledgehammer preplay/`try0` 自带"返回最快 tactic";Munkres 自form-化 metis→meson 得 10×;我们自己 fastforce→clarsimp 曾得 −21%。搜索空间从"几十步序列"塌缩成"~6 候选方法小菜单"。

**实测(`ab_agent` SWAP 模式,10 失败案例 / 13 行;后又在 7 个慢行上扩大菜单 + 仪表)**:
- 名义提速 >10%:2/13,但**都在 8–10ms 微行**(spread 8.5–10.8ms ≈ IPC 底,省 1–3ms 是噪声)。
- **慢行(≥50ms,真正值得优化的)真实提速:0/8**。铁证:一条 12 秒的 `fastforce`(`dmo_bind_ev'`)——**18 个候选全失败**,无任何更快的等价物。cheaper 方法(clarsimp/simp)到达的是**不同的不完整态**,B 不匹配。

→ **换方法在 seL4 慢行上同样无收益**,根因与静态化相同:运行时间**是** simp 重写/classical 搜索的不可约工作量,任何复现同一 B 的方法都得重做。

---

## 4. 闭环结论:proof 层改写不是 wall-time 杠杆

| 假设 | 结论 |
|---|---|
| 缺 facts? | 否(AP3 验证) |
| LLM 能力不够? | 不是卡点(失败是长路径不收敛) |
| 静态化(消除搜索)? | 路径太长,70/85 搜不到 |
| 换更快 tactic? | 慢行 0/8 有效 |

**根因统一:不可约工作量。** 这强化了项目最初判断——真正的 wall 杠杆在 **build 基础设施**(heap 缓存、session 并行),不在改 proof 内容。

并给出**系统性证明协议**(`tools/seL4-proof-search/PROVE-no-tactic-lever.md`):预注册无偏抽样(`sample_slow_lines.py`)→ 两机制同样本 → 可信外部计时 → 可证伪线(≥20% 慢行提速则推翻)→ 因果子实验。供日后把"观察"升级为"可复现的否定证据"。

---

## 5. 转向:不为速度,而为"能力 / 可审计 / 数据集"——claude -p 当改写 agent

既然速度是死路,改写这件事的价值重定位为:① 可审计性/结构恢复(Apply2Isar 那条线,但它是符号工具、做结构轴,"LLM 做去自动化改写"是空白);② 改写数据集;③ 未验证的 **LLM-proposer 臂**(LLM 是否比启发式搜索强)。

**PoC:claude -p 驱动改写,transcript 即记录。** 架构(全部在 `lemma-staticize/isa-repl/`):
```
run_claude_rewrite.sh → claude -p "<prompt>" --output-format stream-json → transcript.jsonl
   └ claude 经 Bash 调 isa_tool_host.sh {state|try|commit|record|stop}
        └ docker exec → isa_tool.py(薄 CLI,文件桥)
             └ poc_repl_server.py(容器内常驻 REPL,一次性 reach lemma,
                持检查点 A + B_sig,try=克隆 A 跑 tactic(非消耗),record=重跑校验)
```
- **claude CLI 定位**:不在 PATH;是 VS Code 扩展自带 native binary
  (`~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude`),全路径可跑,auth 继承订阅。
- **稳健化(逐项修复)**:① `A_goal` 用 `sr()` 取干净 payload;② `commit` bug(`t` 未定义)修复;
  ③ `state` 充实(statement + named_facts + const_hints + hints,definitions 因 REPL diagnostic 通道取不到 def 体而改为常量名 + grep 提示);④ 裸方法自动包 `apply(...)`;⑤ `record` **重跑校验**(写 `verified_reached_B`/`verified_static`)。
- **效果(equiv_forD A/B)**:20→**5** turns、$0.75→**$0.18**、140→**45s**、源码 grep 7→**0**;找到 `by (erule equiv_forE)` 并校验落盘。

---

## 6. claude -p vs 启发式 DFS:首个对比(4 案例)

react_agent 仅 2 个 run 且都在无 heap session,故用 `ab_agent` DFS 的 solved/failed(InfoFlow)做基线。**注意基线是 DFS + LLM-菜单排序(PROPOSER=llm)**,claude -p 则是 LLM **开放提议 + 自取上下文**——区别在 LLM 是"排固定菜单"还是"开放提议"。

| 案例 | DFS | claude -p |
|---|---|---|
| `states_equiv_forI` | ✅ | ✅ `simp only: states_equiv_for_def` |
| `rel_terminate_weaken` | ✅ | ❌(8 trys 没闭合) |
| `affects_equiv_def2` | ✗ | ❌(3 trys,内在难) |
| `Run_app` | ✗ | ✅ `apply (rule subrelI, drule Run_mid, elim exE conjE, erule relcompI, assumption)` |

**结论:两法互补、无一支配。** claude 找到 DFS 漏掉的(Run_app,多步组装=LLM 强项),也漏掉 DFS 找到的(rel_terminate_weaken);内在难的两者都败。
**副产物**:实验抓出 `record` 盲信 agent 的 bug(2/4 记录了未达 B 的无效 tactic)→ 已修为重跑校验。**批量统计必须看 `verified_*`,不能只看 final_rewrite 存在与否。**

---

## 7. 提议的轻量方案:claude -p + 自积累参考库

**定位(再强调):为可审计/数据集/few-shot 效率,不为速度。**

```
claude -p 改写 → record(已校验)→ poc-result.json
   └[harvest] 仅 verified=true → 追加 corpus.jsonl:
        {session, lemma, original_tactic, static_rewrite, goal_skeleton, named_facts, defs_used}
新 lemma:
   [retrieve] 按 (session/共享常量/原 method/goal 形状) 取 top-k 相似历史条目
   [few-shot] 注入 prompt("以下相似引理这样改写过:…")→ claude 套用模式(更少 turn/更高命中)
```
- 这是 PROMISE"结构模仿"的轻量版;不需向量库,简单相似度即可。
- **自改进只在复发模式上有效**(seL4 里 `auto simp: <def>`→`simp only: <def>` 极高频);内在难的案例语料帮不上。
- corpus 只收 `verified_reached_B=true`(record 校验是前提,否则污染)。
- **不建议 commit 进 l4v**:改 .thy 要重验下游(CRefine 等)、无收益、upstream 历史保守(cstr-2graph 全被拒)。"commit 记录"落地为**独立参考语料**。

---

## 8. 工具沉淀(本次产出)

`tools/seL4-proof-search/`:
- `Isa-Repl/ab_agent.py` — DFS 静态化 + AP1/AP3 fact 接入 + SWAP 模式(换 tactic + 仪表)。
- `Isa-Repl/poc_repl_server.py`、`isa_tool.py`、`isa_tool_host.sh`、`run_claude_rewrite.sh` — claude -p 改写链路(稳健版)。
- `Isa-Repl/batch_claude_cases.sh` — 多案例批量(server→claude-p→收 result/transcript)。
- `Isa-Repl/ablation.sh`、`ablation_run.sh`、`swap_batch.sh` — 消融/SWAP 批跑。
- `sample_slow_lines.py` — 预注册无偏抽样(源码枚举 automation lemma + seeded 随机)。
- `compare_ablation.py`、`build_staticize_bench.py`、`build_staticize_testset.py`、`theory_parallelism_scan.py` — 分析/采样。
- `PROVE-no-tactic-lever.md` — 系统性证明协议。
工件:`Isa-Repl/runs/`(ab-*.json、abl_*、swap_*、batch_claude_*、rewrite-*.jsonl 等)。

相关 memory:`staticize-failure-taxonomy`、`staticize-factretrieval-and-fveler-bench`、`claude-p-rewrite-poc`、`sel4-llm-proof-synthesis-landscape`、`search-elimination-no-payoff`、`timing-measurement-discipline`、`project_optimization_priority`。

---

## 9. 待决与下一步

- **公平性框架**:claude -p 有完整 Bash(能 grep 源码),DFS 没有。要做严谨"LLM vs 启发式"对比,需三方:heuristic-DFS / DFS+LLM-rank / claude-p-open,厘清"LLM 在哪一层、值多少"。
- **若要建第 7 节方案**:加 harvest / retrieve.py / prompt 注入三小件(均为现有件的小延伸)。
- **若回到速度目标**:放弃 proof 层改写,回 build 基础设施(heap 缓存 / session 并行,即 36GB/j=2 那条线)。
- **运行约束记录**:缺 AInvs/Refine heap;reach 墙(分钟级/lemma);孤儿 poly 进程须显式清(`pkill -9 -f "poly|Isabelle_Tool|bash_process"`);单 agent 跑可把 `--maxheap` 调到 24GB(j=2 才需 12GB)。
