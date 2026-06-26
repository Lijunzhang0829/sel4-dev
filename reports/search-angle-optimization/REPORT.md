# seL4 lemma 从 "search 角度" 优化的实验报告

_自动化 tactic → static tactic 改写:三方法对比与"提速空间"的实证_

日期:2026-06(本会话)。所有结果均可溯源到持久 run 目录 / 归档日志 / git 提交(见 §6)。

---

## 0. 问题与路线

**问题**:seL4 的 lemma 能否从 "search 角度" 优化——把昂贵的自动化 tactic(`auto/fastforce/force/blast/clarsimp`)改写成确定性 static tactic,从而**降低 proof build 的 wall time**?

**路线**:把 search tactic 改写为静态规则序列(`reach-B 重建`),用三种方法生成、统一判据对比,并实测改写前后的耗时。

**一句话结论(先给)**:在 "**reach-B 重建**" 这个目标下,开放词表 LLM 的覆盖率明显高于菜单搜索;**但 reach-B 重建本身不是一个能提速的任务**——可被改写的恰是快案例(无 wall 收益),慢案例改不动;且 tactic 层 static 化只能 re-derive、不能 replay 引擎的 proof term,**结构上就不提速**。**因此本实验验证的是一个对"提速"目标而言错误的任务。**

---

## 1. 候选集选择(及其演化)

| 候选集 | 选法 | 规模 | 产物 |
|---|---|---|---|
| DB 计时挖掘 | `scan_db_timings.py` 解 heap `command_timings` | 全量 | `tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json` |
| **DB-hottest 冻结集** | `curate_candidates.py`,≥10s 搜索主导 | 46 | `lemma-staticize/experiment-candidates/{candidates.json,manifest.md}` |
| **佐证集**(历史已证可解) | `build_corroboration_set.py` | 16(fast 12) | `experiment-candidates/{corroboration-set.json,.manifest.md}` |
| wasted-classical 高价值 | `wasted_classical_json.py`(精炼文本过滤) | 7(≥10s) | `runs/wasted_candidates.json`,`runs/wasted_hv_candidates.json` |
| 实测筛器候选 | `session_candidates.py`,≥2s 少过滤 | 1283 | `runs/session_candidates.json`,`runs/{pilot_ablate_set,rescan_set}.json` |

**贯穿教训(重要)**:
- **DB `command_timings` 的 elapsed 不可靠**——来自并行 build(j=2),含调度竞争噪声、且 elapsed≠cpu;实测 `rm_affects` DB 标 24.6s,串行 check-theory 里仅 ~4s(**高估 ~6×**)。⇒ 用它选"高价值"会**系统性误选**。
- **per-lemma 是文件 build 的极小分母**(PolicySystemSAC 整文件 ~66s,单条 tactic ~1–4s = <6%)⇒ 单条改写**结构上**就难移动文件/session wall。

---

## 2. 三种改写方法(统一判据:reach-B = 复现原 tactic 留下的目标状态)

| 方法 | 实现 | 机制 | 提交 |
|---|---|---|---|
| **DFS** | `react_agent.py`(NO_CLAUDE) | 结构菜单 + 回溯 + cost-aware 定位最贵行 | `0eb311e` |
| **GenStat-stateful** | `genstat_stateful.py` SHOW_STATE=1 | 逐步:看活 goal → claude 提下一块 → REPL 验证 → 失败反馈 | `72a66e1` `9a929e4` |
| **GenStat-blind** | `genstat_stateful.py` SHOW_STATE=0 | 一次性整段生成 → reach-B 验证 → 错误反馈重生成 | `72a66e1` |

桥接:容器内无 `claude` 二进制 → `proposer_host.py` mode=genstat 在 host 跑 claude -p,通道错误经 `err` 回传。

**关键 bug 链(均靠日志/transcript 逐行复盘定位)**:
1. `A_goal` 含 `True<SEP>` 协议前缀 → claude 幻觉 `apply TrueI`(修:剥前缀);
2. `apply_chunk` 从重 focus 探针读签名 → 重载步前快照 → 每步误判 "no progress"(修:从 step 返回读签名);
3. **`apply_chunk` 返回步前 checkpoint → `clean_goal` 喂给 claude 过期 goal**(churn 真因,Zombie 案例靠 transcript 逐行发现)(修:克隆步后态返回,`9a929e4`)。
   - 验证:`is_transferable_Zombie` NO-PATH(churn 20 步)→ **REACHED-B(3 步)**。溯源:`_archived-logs/zombie_refix.log`。

---

## 3. 改写结果(均可溯源到 run 目录)

### 3.1 佐证集(12 个可解案例,统一 reach-B)
| 方法 | REACHED-B | 溯源 |
|---|---|---|
| DFS | **3/12** | `corrob-dfs-20260622-111056/summary.tsv` + 每 case `stdout.log`/`result.json` |
| Stateful | **12/12**(修复后)| `corrob-stateful-20260622-112302` + `-051156`(重试) |
| Blind | **12/12** | `corrob-blind-20260623-020258` |

- **结论**:开放词表(stateful/blind)在 reach-B 覆盖上**碾压菜单 DFS**;DFS 卡在菜单封顶(够不着库规则/实例化)。
- **stateful ≈ blind**:活状态是**效率杠杆**,非能力天花板;churn 经 apply_chunk 修复后基本消除(单一案例 Zombie 即由此翻盘)。

### 3.2 DB-hottest(8 个慢案例)
- 三方 **0/8** —— 慢 = 复杂(多子目标/大项)= reach-B 够不着。

### 3.3 提速实测(改写成功的案例,串行 A/B)
- 改写成功的全部 **sub-60ms**,**无 wall 收益**(在噪声地板下)。溯源:`time_rewrite.py` + `runs/reachb_paths.json`,`_archived-logs/timing_rewrite.log`。
- **反相关**:可改写 = 快案例(无价值);有价值 = 慢案例(改不动)。

### 3.4 wasted-classical 高价值(7,三方 + clarsimp 消融)
- clarsimp 消融 + DFS + stateful + blind **全 0/7**:4 个真功(clarsimp 留子目标 / 探索 0)、3 个大文件 init 超时。溯源:`_archived-logs/{wasted_ablate,wasted_dfs,wasted_stateful}.log`。

### 3.5 实测式 clarsimp 筛器(Access + InfoFlow,26 个 ≥2s 候选)
- **0/26 真·可优化**:23 个 clarsimp BUILD-FAIL(真 case-work)、3 个 build 过但省 ≤6% 重叠(DB elapsed 高估)。溯源:`_archived-logs/{pilot_ablate2,rescan_ablate}.log`。

---

## 4. 结论

### 4.1 方法层(可信)
- **开放词表 > 菜单**:LLM(stateful/blind)在 reach-B 覆盖上 12 vs 3 胜 DFS。
- **stateful ≈ blind**:逐步 vs 一次性是 trade-off(活状态 = 就地纠偏效率;一次性免 churn);二者覆盖率相当。

### 4.2 任务层(核心,且是负结论)
- **reach-B 重建 ≠ 提速**。三条独立证据:
  1. 可改写的全 sub-60ms,无 wall 收益(§3.3);
  2. 慢/有价值的案例 reach-B 全失败(§3.2/3.4);
  3. **机制层**:tactic 层 static 化只能 **re-derive**(`simp only:`/`rule` 重做引擎已做的归一化/合一),**不能 replay** 引擎构造的 proof term;省掉的只是小探索,证明内容照跑甚至更慢。
- **⇒ 本实验把 LLM 验证在了一个"对提速目标而言错误的任务"上**:reach-B 重建在能成的频段无价值、在有价值的频段不可达,且与 Isabelle 执行模型逆行。

### 4.3 search 角度的优化空间(分层、诚实)
- **search 移除**(clarsimp / reach-B):**低收益**已较扎实(0/26 + 0/7 + 佐证集无提速)。
- **search 加速**(保留引擎、剪枝/换便宜搜索变体):**未测**——这才是"从 search 角度提速"的正确子任务;但它**不再是"路径→rule 全静态化"**,与本路线初衷相悖,故本实验止步于此。
- **真正的 build-wall 杠杆**不在改 tactic(per-lemma 是文件 wall 极小分母),与项目一贯结论一致(heap 缓存 / session 并行 / Docker 层)。

---

## 4.7 Direction 4 闭环:搜索加速天花板的严谨实证(新增)

按 `measurement-tools.md` 流水线 `FIND → SCREEN → VERIFY(stock,gates)→ REPORT(command_timings)` 做完整,**框架**:per-lemma 搜索加速天花板 `C_i = T_原始 − T_确定路径 ≤ T_原始`(确定路径 = reach-B 全静态重建 = 移除搜索的极限)。

| 步骤 | 工具 | 结果 | 溯源 |
|---|---|---|---|
| VERIFY(正确性) | `check_theory_selfqual.sh --patch`(stock build) | **9/11 reach-B 路径真 build**;**2 个 in-REPL 假阳性**(`pas_refined_sita_mem`/`sep_heap_domD'`,stock 失败)→ 早先 reach-B"成功"**~18% 假阳性** | `runs/ceiling-pilot-*/ceiling.log` |
| SCREEN(rough) | `time_rewrite.py`(in-REPL,drop-warmup/median7/A-A 地板/>2σ) | 天花板几十 ms;**blast 系为负**(static 比 blast 慢) | 同上 |
| REPORT(权威) | golden `command_timings` + `C_i ≤ T_原始` | **T_原始 < 100ms**(候选行在 golden 全 0 记录;文件记录地板 100–171ms)→ **天花板 < 100ms,严谨** | `report_orig_timing.py`,`runs/report_ceiling_orig.json` |

**严谨结论**:
- **可测频段(reach-B 能重建)**:搜索加速天花板 **< 100ms,低于 Isabelle 自身逐命令计时地板**——Isabelle 本体都嫌这些命令太便宜不计时。**结构性可忽略,实证锁死。**
- **不可测频段(慢 lemma,reach-B 失败)**:`T_原始 ≥` 秒级但 reach-B 重建不出 → 天花板只能松绑 `C_i ≤ T_原始`、收不紧;按框架那里是**真证明搜索**,"加速"= reach-B 难问题本身。
- **方法学要点**:用 `C_i ≤ T_原始` 松上界 + golden 记录地板即可严谨定界,**无需建 variant、不污染 golden heap**(variant build 既不必要、又有污染风险,正确跳过)。

## 5. 方法论资产(可复用)

1. **可复现 per-lemma 成本**:必须**串行**(`parallel_proofs=0`)+ prover 内计时(IsarLite / check-theory wall)+ 多次中位;DB elapsed 不可用。
2. **search 内部耗时拆不开**(intra-tactic profiler 撞 future/setup 墙)→ 改用 **"干预 + 总时间差"外部测量**;`T_原始 − T_确定路径` 是 search 加速的**严谨上界**(实测 ~0)。
3. **实测式筛器** > 文本启发式:`clarsimp build + frac_saved + 非重叠` 行为式判定,自带正确性 + 提速证据(`wasted_ablate.py`)。
4. **bug 多靠日志/transcript 逐行复盘**(stale-goal 即如此发现)。

> **效度保留(本报告不展开,但存档备查)**:佐证集与 few-shot 有重叠、reach-B 未端到端 check-theory build 验证、通道波动需重跑取干净——这些 hygiene 问题不影响 §4.2 的实质结论(任务方向错误),但会**削弱"12/12 验证了 LLM 能力"这类定量声称**。

---

## 6. 溯源索引

| 结果 | 持久产物 | 提交 |
|---|---|---|
| 候选集(冻结+manifest) | `lemma-staticize/experiment-candidates/` | 主repo `313fbc6` 起 |
| 三方法 per-run 快照 | `lemma-staticize/runs/corrob-{dfs,stateful,blind}-*/`(`stdout.log`/`result.json`/`transcript.jsonl`) | submodule `fb69f1d` |
| claude I/O 审计 | `tools/seL4-proof-search/Isa-Repl/runs/genstat-transcript-*.jsonl`、`genstat-stateful-*.json` | — |
| ablation/筛器/计时日志 | `lemma-staticize/runs/_archived-logs/*.log` | (本报告归档) |
| Direction 4 ceiling 闭环 | `lemma-staticize/runs/ceiling-pilot-*/ceiling.log`、`runs/report_ceiling_orig.json` | (本会话) |
| ceiling 工具 | `verify_path.py`(stock VERIFY)、`time_rewrite.py`(SCREEN,噪声纪律)、`report_orig_timing.py`(REPORT,golden) | (本会话) |
| 实测筛器工具 | `session_candidates.py`、`wasted_ablate.py`(frac_saved/non_overlap) | (本会话) |
| 候选源 | `runs/{db_candidates,wasted_candidates,session_candidates,reachb_paths}.json` | — |
| 方法论 | `tools/seL4-proof-search/Isa-Repl/GENSTAT.md` | submodule `72a66e1` |
| 代码(三方法+修复) | `react_agent.py` / `genstat_stateful.py` / `proposer_host.py` / `run_corroboration.sh` / `wasted_ablate.py` / `session_candidates.py` | submodule `0eb311e`→`9a929e4` |

每个 per-run 目录自洽(stdout + 结构化 result + 逐次 transcript,不互相覆盖),可独立复盘任一 case 的"被告知什么 → 提了什么 → REPL 裁决"。
