# 用 LLM 优化 seL4 proof —— 整体报告

**问题**:固定硬件、不改证明的数学内容、不被上游拒收的前提下,**能不能用 LLM 让 seL4 proof build 更快(wall / CPU)?**

**一句话结论**:能改的 LLM 都验证过了,但**两个方向都基本走到头**——方向一(改 lemma)LLM 有能力、对 wall 无效;方向二(改结构)能动 wall 的杠杆全被物理/上游/基建挡死。**墙就是 refinement 数学本身**,真正的加速杠杆在更大硬件 / heap 缓存 / Docker 层缓存,与 LLM 无关。LLM 在 seL4 上真正打得动的是**别的目标**(spec 强化 / proof 修复协同演化),不是 wall 加速。

> 本目录把原先分散的三份工作(`search-angle-optimization` / `proof-structural-optimization` / `proof-staticize-archive`)合并,统一按下面**两个方向**组织。`spec-strengthen/`、`repair/` 是**正交的其它 LLM-for-proof 目标**(非 wall 加速),不在本目录。

---

## 目录结构

| 路径 | 内容 |
|---|---|
| [`1-modify-lemma/REPORT.md`](1-modify-lemma/REPORT.md) | **方向一:改 lemma 本身**——search 消除/降级/缩减、关键路径直测(§3.6)、引用-B 微发现(§3.7)。权威详报 |
| [`2-modify-structure/REPORT-2026-06-26.md`](2-modify-structure/REPORT-2026-06-26.md) | **方向二:改 proof 结构**——lemma/session 并行、冗余 session、theory-DAG flatten(=引用-A)。权威详报 |
| [`writing-notes.md`](writing-notes.md) | 论文写作方向备忘:主线怎么讲、预判审稿挑刺与答法、proof 优化其它角度。已用 §3.7 更新 §5.3/§7.3/§10.1 |
| [`archive/`](archive/) | 早期 staticize 工作(setup-杠杆探索、DB-elapsed 依赖等)。**已被上面两份综合取代、部分结论已推翻**,仅留作溯源 |
| [`../golden-baseline/`](../golden-baseline/) | 两方向共用的测量基线(墙钟/heap/lemma inventory,2026-05-29) |

---

## 方向一:改 lemma 本身(LLM 改证明内容)

详报见 [`1-modify-lemma/REPORT.md`](1-modify-lemma/REPORT.md)。

| 子实验 | 做了什么 | 结论 | 状态 |
|---|---|---|---|
| **消除 search**(reach-B 静态重建) | 三改写法(DFS 菜单 / claude 逐步 / claude 整段),统一 reach-B 判据 | LLM 能重建,但能成的全是 <100ms 便宜行(无价值),秒级慢行三方 **0/7** 够不着 | **闭(负)** |
| **降级 search**(`auto/ff→clarsimp`) | 行为式筛器真换+build+A/B,26 候选 | **23/23 有效 swap BUILD-FAIL**,classical 在做真 case-work | **闭(负)** |
| **缩减 search**(`simp only/del`、换序、喂事实) | `reduce_agent`+双闸门,45–56 feasible 全扫 | **7–10 个 per-lemma 真加速(20%–94%)**,但全在并行旁支,端到端墙钟不动 | **per-lemma 正 / wall 负** |
| **Isar 改写 / 减 step** | 参考既有写作方向结论 | **不保证加速**(改写后可能更慢/持平) | **闭(负)** |

**方向一定论**:LLM 完全能改写 lemma、也确实产 per-lemma 加速,但 **per-lemma 加速不反映到 build wall**(并行 slack + 关键路径绑定)。

---

## 方向二:改 proof 结构(并行 / 引用)

详报见 [`2-modify-structure/REPORT-2026-06-26.md`](2-modify-structure/REPORT-2026-06-26.md);引用-B 部分见方向一详报 §3.7。

| 子路 | 做了什么 | 结论 | 状态 |
|---|---|---|---|
| **lemma/session 并行** | DRefine 代理实验标定 headroom(factor 1.89→8.09) | `-j2×threads16` 已是 29GB/16 核硬上限,调旋钮不算优化 | **闭(满)** |
| **删冗余 session** | re-elaboration 扫描器全 l4v 扫 | 仅 `CRefineSyscall` 一个满足,删 −29% 但**上游拒收** | **闭(上游)** |
| **调整引用-A(import/flatten)** | 想在 CRefine 跑 `thm_deps` 判 import 必需性 | 唯一未证伪的真结构方向,但父 heap 被清 + `thm_deps` 不稳,验证做不动;上界又被 arch_split 锯齿卡死 | **闭(基建)** |
| **调整引用-B(规则属性 `[simp]/[wp]`)** | §3.7:`trial_probe`/`mutation_test`/`theory_simpdel` 在关键路径 tent-pole 上实测 | 见下"本轮新发现" | **闭(收益少)** |

### 本轮新发现(引用-B,写在方向一详报 §3.7)

1. **纠正了方向一 §3.6 的一个错误结论**:§3.6 用 anon% 判"关键路径 simp 不可缩",但更细的 trace 解析器([`trial_probe.py`](../../tools/seL4-proof-search/Isa-Repl/trial_probe.py))实测那 93.8%–99.7% 是 **`Adding rewrite rule`(simpset 重复构造)**、不是 def 展开。**旧证据失效。**
2. **关键路径行实测可缩**:`CSpace_C:2408` 删一条白试全局 `[simp]`(`ctes_of_not_0`,试了又失败 96–107 次)→ **−7.4% 且绿**。**推翻 §3.6 的"不可缩"。**
3. **但廉价全局删证伪**:该规则**承重**,整 theory 删即断(CSpace_C:1064)。删"没用到"的声明又**本身无效**(discrimination net 让匹配不上的规则近乎免费)。→ 引用轴**无 LLM 可廉价推进的格子,关闭**。

---

## 横切:测量方法学(踩过的墙 + 造的资产)

真正难的不是"改",是"**测**"。沉淀的硬知识:

- per-lemma 正确度量是 **CPU(串行)**,但干净 CPU 当前不可测(golden 只有 elapsed 无 cpu、IsarLite 坏、intra-tactic profiler 撞 parallel-futures/setup 墙)。最接近代理 = `threads=1` 串行 build 读 `command_timings`。
- in-REPL 墙钟有 proof-cache + 冷启动污染(虚高 3–4.5×),只能粗筛。
- **own-session 单 theory build**:攻克 CRefine 测量成本(分钟级、绕开 REPL init 墙)的关键技巧,§3.6/§3.7 都靠它。它能测规则属性(`simp del:` 局部即时生效),但**测不了 import 移除**(父 heap 仍含被删 theory)。
- 可复用工具:`trial_probe.py` / `mutation_test.py` / `theory_simpdel_test.py`(+`SIMP_TRACE_DEPTH`)、own-session 探针、re-elaboration 扫描器、`command_timings` 从被剥 heap 的 DB zstd 直读。

---

## 最终裁定

- **方向一(改 lemma)**:LLM 能做,**对 wall 无效**(per-lemma 加速落进并行 slack / 不在关键路径)。
- **方向二(改结构)**:能动 wall 的(并行/冗余/flatten/引用-A)全被**物理/上游/基建**挡死;引用-B 有 7.4% 局部正信号但规则承重不可廉价去、案例不多、**不投**。

⇒ **固定硬件 + 不改证明内容 + 不被上游拒 ⇒ seL4 proof 墙几乎无实质加速空间。** 墙就是 refinement 数学本身;真正能 scale 它的是更大硬件 / heap 缓存 / Docker 层缓存(项目一贯结论)。**LLM 在 seL4 上真正的价值不在 wall 加速,而在其它目标**——spec 强化([`../spec-strengthen/`](../spec-strengthen/))、proof 修复/协同演化([`../repair/`](../repair/))。
