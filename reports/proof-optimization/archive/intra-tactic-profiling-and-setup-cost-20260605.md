# seL4 proof-lemma 优化路线复盘：从 search-elimination 到 setup 杠杆

**日期**：2026-06-05  
**分支**：`spec-strengthen`  
**作者**：lijun + Claude  
**容器**：`sel4-l4v`（`docker compose exec l4v`）

---

## 1. 我们一开始在做什么

这项工作的起点是沿着原来的 **proof staticization / search-elimination** 路线继续往前推。

原路线的核心想法是：

- 在 seL4 中先找出那些“很慢、而且看起来主要靠 classical search 的 proof 命令”；
- 再把这些 `blast/auto/fastforce` 一类 proof tactic 改写成更静态、更确定的 rule-chain 或更窄的 tactic；
- 最后看这种 proof-level 改写是否能带来 wall-time 收益。

与这条路线直接相关的原始材料包括：

- 原 loop 设计与审计：  
  [tools/seL4-proof-search/Isa-Repl/audit/README.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/README.md)  
  [tools/seL4-proof-search/Isa-Repl/audit/block4-loop-design.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/block4-loop-design.md)
- 原候选集与筛选脚本：  
  [tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json)  
  [tools/seL4-proof-search/Isa-Repl/scan_db_timings.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/scan_db_timings.py)
- 原实验和负结论上下文：  
  [tools/seL4-proof-search/Isa-Repl/audit/block2-scripts.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/block2-scripts.md)  
  [tools/seL4-proof-search/Isa-Repl/audit/block6-threats-and-verdict.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/block6-threats-and-verdict.md)

原方法大致是这样实现的：

1. 从 Isabelle build DB 中读取每条 command 的 timing；
2. 用 `search_frac = search_s / total_s` 之类的启发式，挑出“慢且像 search-heavy”的 lemma / proof line；
3. 对这些候选运行 A→B 搜索 loop，尝试把原 tactic 替换成静态 proof；
4. 如果找不到可行路径，就支持“search-elimination no payoff”这一方向性的判断。

也就是说，**原路线默认相信两件事**：

- 候选筛选确实找到了真正的 `search-dominated` 慢 lemma；
- DB 上看到的慢行时间，大致反映了 tactic 本身的计算成本。

后面的全部工作，都是在审这两个前提。

---

## 2. 第一个疑点：原候选到底是不是“真 search-heavy”

我们最先怀疑的是：**原来的 `search_frac` 只看命令文本，可能并没有真的识别出 `search-dominated` 的对象。**

原因很直接：

- `auto` / `fastforce` / `force` 这些方法，不是“纯 classical search”；
- 它们内部会跑完整 simpset；
- 所以文本里出现这些名字，不等于这条命令的主要成本一定来自 search。

如果这一点不成立，那么原 loop 从一开始就可能是在优化“挑错了的对象”。

于是我们先做了**实验一：审计原分类器本身**。

---

## 3. 实验一：审计原分类器是否系统性误判

### 3.1 原方法怎么做

原分类器见：

- [tools/seL4-proof-search/Isa-Repl/classify_db_candidates.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/classify_db_candidates.py)
- 输出：  
  [tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified.json](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified.json)

它基本按 proof 命令文本归桶：

- 若出现 `auto|blast|fastforce|force|metis|fast|safe`，且没有明显 `simp:` 迹象，就倾向把该命令算作 `search-pure` / `search-dominated`；
- 再按各类命令累计时间给 lemma 打标签。

这个方法的优点是快，缺点是：**它完全不看 tactic 内部。**

### 3.2 我们发现了什么问题

抽查原输出后，问题非常明显。

原分类器给出的分布是：

- `search-dominated = 15`
- `simp-dominated = 91`
- `mixed = 16`
- `context/setup-dominated = 0`

但这 15 个所谓 `search-dominated`，**15/15 都不是纯 classical proof**。它们全部是：

```text
by (fastforce dest: all_childrenD ...)      147.6s   <- empty_slot_pas_refined
by (fastforce elim: cte_wp_atE intro: ...)   95.0s
apply (auto dest!: isCapDs)[1]               33.5s
apply (auto split: if_split)[1]              24.4s
by auto                                      15.7s / 12.6s ...
```

也就是说：

- **没有一个** 是文本上可确信的纯 `blast/metis/meson` 风格目标；
- 旗舰样本 `empty_slot_pas_refined` 也只是 `fastforce`，而不是纯 search tactic。

更糟的是，头号慢 lemma `map_to_ctes_kh0H_SomeD` 里那条约 692s 的大命令：

```isabelle
by (simp | erule disjE | clarsimp simp: … | fastforce simp: …)+
```

它显然是一个多 family alternation，且强烈带 `simp`，却因为里面出现了一个 `fastforce` token，就被整条塞进了“search”相关桶。

### 3.3 实验一的结论

实验一没有直接告诉我们“慢 lemma 真正慢在哪里”，但它已经足够推出一个重要结论：

**原分类器系统性高估了 search 成分。**

于是接下来的问题就变成了：

**如果我们把文本分类改得更诚实、更保守，那么原来那批“慢 search lemma”还剩多少？**

这就进入实验二。

---

## 4. 实验二：重写分类器，先只保留文本上真正能确信的标签

### 4.1 为什么需要新分类器

实验一证明了：原来的文本规则过于激进。  
所以实验二的目标不是“更聪明地猜”，而是：

**只在文本证据真的足够强时下结论；否则明确承认判不出来。**

### 4.2 新方法怎么做

新分类器见：

- [tools/seL4-proof-search/Isa-Repl/classify_db_candidates_v2.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/classify_db_candidates_v2.py)
- 输出：  
  [tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified_v2.json](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified_v2.json)

`v2` 的核心原则是：

- **纯 classical** 才判 `search-pure`
- **纯 simp** 才判 `simp-pure`
- `auto` / `fastforce` / `force` 一律放进 `ambiguous + needs_profiling`
- 多 family alternation 单列为 `structured`

也就是说，`v2` 明确承认：

**很多慢 lemma 仅凭文本根本无法判断是 search-heavy 还是 simp-heavy。**

### 4.3 实验二的结果

`v2` 跑完后的 122 个候选分布如下：

| class | count | total_s |
|---|---:|---:|
| `search-pure` | **0** | 0 |
| `simp-pure` | 29 | 3162 |
| `ambiguous-search-lean` | 6 | 365 |
| `ambiguous-simp-lean` | 42 | 1059 |
| `ambiguous-unknown` | 31 | 688 |
| `structured` | 14 | 1251 |

合起来看：

- `search-pure = 0`
- `simp-pure = 29`
- `ambiguous = 79`
- `structured = 14`

### 4.4 实验二的结论

这一步把问题又往前推了一层：

- 我们还**不能**说 seL4 中不存在 search-heavy 的慢 lemma；
- 但我们已经可以说：**原路线拿来做 search-elimination 的候选集，并没有可靠识别出这类对象。**

因为在现有 122 个候选里，**文本上可确信的 `search-pure` 是 0**。

这带来一个新的问题：

**既然文本判不出来，那我们能不能直接测 tactic 内部，拿到真实标签？**

于是进入实验三：做 intra-tactic profiler。

---

## 5. 实验三：搭建 intra-tactic profiler，尝试拿到“真实标签”

### 5.1 为什么要做 profiler

实验二之后，事情已经很清楚了：

- 文本分类只能给出保守标签；
- 它可以排除误判，但不能告诉我们“真实慢因”；
- 如果要继续沿着 proof-level 优化这条路走，就必须拿到更接近 ground truth 的东西。

因此实验三的目标是：

**直接测一条 tactic 内部的 CPU 时间拆分，看它到底花在 search、simp、unify、locale/setup、还是别的 ML 子系统上。**

### 5.2 工具怎么实现

相关工具在：

- [tools/seL4-proof-search/Isa-Repl/profiler/time_profile.snippet.thy](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/time_profile.snippet.thy)
- [tools/seL4-proof-search/Isa-Repl/profiler/parse_profile.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/parse_profile.py)
- [tools/seL4-proof-search/Isa-Repl/profiler/profile_lemma.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/profile_lemma.py)
- [tools/seL4-proof-search/Isa-Repl/profiler/run_profile.sh](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/run_profile.sh)

思路是：

1. 用 `ML_Profiling.profile_time` 包住任意 method；
2. 抓 Isabelle 发出来的 profiling markup；
3. 解析出 ML 函数级采样；
4. 再把函数名映射回若干子系统桶：
   - `simp`
   - `classical`
   - `unify`
   - `locale/setup`
   - `GC`
   - `other`

### 5.3 先验证工具本身：它会不会改 proof 语义？

这一步在两轮审计后重新落盘，材料在：

- 生成器：  
  [reports/proof-staticize/evidence/profiler-validation/gen_iso.py](evidence/profiler-validation/gen_iso.py)
- 隔离 raw：  
  [reports/proof-staticize/evidence/profiler-validation/](evidence/profiler-validation/)

我们最后采用的是严格隔离设计：

- 每个 theory 只有一条 lemma；
- 每个目标分别跑：
  - plain
  - 旧 snippet
  - Fix B

12 个落盘 raw 汇总成四组正反例：

| 目标 | plain | 旧 snippet | Fix B |
|---|---|---|---|
| `(P::bool) ∧ Q`（不可证） | FAIL | FAIL | FAIL |
| `sum_list [0..<700] = 244650`（plain `simp` 不闭合） | FAIL | FAIL | FAIL |
| `rev (rev xs) = xs`（plain `simp` 成功） | PASS | PASS | PASS |
| FOL 链（plain `blast` 成功） | PASS | PASS | PASS |

这一步的含义是：

- 我们**撤回**了之前一度声称“snippet 有语义 bug”的说法；
- 现在能支持的说法是：**profiler wrapper 在这些隔离 HOL 用例上是忠实的**；
- `Fix B` 是采样完整性的细化，不是语义修 bug。

### 5.4 实验三的结论

实验三完成后，我们拥有了一个**在隔离小例子上可用、可信**的 profiler。  
但它还没有回答最关键的问题：

**它能不能打到真实 seL4 的重型 mid-file tactic？**

于是接下来就用它去打最像 search-heavy 的旗舰样本：`empty_slot_pas_refined`。

---

## 6. 实验四：把 profiler 打到真实 seL4 慢 lemma 上

### 6.1 为什么选 `empty_slot_pas_refined`

目标样本是：

- lemma：`empty_slot_pas_refined`
- 文件：`proof/access-control/CNode_AC.thy`
- 行：1103
- DB elapsed：147.6s
- `v2` 标签：`ambiguous-search-lean`

它被选中，是因为在 `v2` 之后剩下的对象里，它仍然是**最像“也许真有 search 成分”的旗舰候选**。

### 6.2 实验四的几轮尝试

#### 尝试 A：直接 import 后复现

相关 raw：

- [reports/proof-staticize/evidence/profiler-validation/raw-plain.out](evidence/profiler-validation/raw-plain.out)
- [reports/proof-staticize/evidence/profiler-validation/raw-import-fixB.out](evidence/profiler-validation/raw-import-fixB.out)

做法是：

- 先 `import Access.CNode_AC`
- 再在导入后的单独 theory 里重放目标 proof

结果是 plain 版本整个 theory **0.457s** 就完成了。

这说明一件很重要的事：

**import 上下文已经不忠实。**

导入整个 `CNode_AC` 之后，目标 proof 所依赖的大量后续上下文、`[wp]` 事实等已经在 scope 里，原本在 mid-file 位置昂贵的 proof，此时会变得非常便宜。

所以这条路证明不了原始 147.6s 的来源。

#### 尝试 B：在原文件里并行 profile

相关背景与日志：

- [reports/proof-staticize/evidence/task-logs/bwm0p9up1.output](evidence/task-logs/bwm0p9up1.output)

做法是：

- 保持 Isabelle 默认并行 proof 行为；
- 在整文件执行中，只包裹目标 tactic；
- 看 profile consolidation 里目标 tactic 得到多少采样。

结果是：

- 整个文件 only **9.7s wall / 52.6s cpu**；
- 目标 fastforce 只对应到非常少的采样（之前旧记法里提过约 16 个，现只能诚实地说“极少采样，不足以下定论”）。

这里出现了第一个结构性难题：

- 这些很少的采样，可能说明目标 tactic 本来就不重；
- 也可能说明真正工作被 Isabelle future 延后到了 profiled 区域外。

也就是说，**并行 profile 拿不到决定性解释。**

#### 尝试 C：强制同步执行

相关材料：

- [reports/proof-staticize/evidence/task-logs/bc8dbvfmw.output](evidence/task-logs/bc8dbvfmw.output)
- [reports/proof-staticize/evidence/profiler-validation/raw-exp3-threads1-stuck-1089.out](evidence/profiler-validation/raw-exp3-threads1-stuck-1089.out)

做法是：

- 关闭 `parallel_proofs`
- 尽量让目标 tactic 的真实工作同步落在 profiled 区域内

结果是：

- 全文件跑到 1500s 还没结束；
- 卡在目标前的 line 1089。

这说明：

**同步模式理论上更忠实，但工程上几乎跑不动。**

#### 尝试 D：对比 DB elapsed 与真实 process wall/cpu

相关工具：

- [tools/seL4-proof-search/Isa-Repl/profiler/db_cpu_vs_elapsed.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/db_cpu_vs_elapsed.py)

我们进一步比对了：

- DB 中 line 1103 的 elapsed = **147.6s**
- 整个 `CNode_AC.thy` 的 timed-command elapsed 合计 = **402.7s**
- 但干净的并行 process 整文件 only **9.7s wall / 52.6s cpu**
- DB 中 `cpu/gc` 字段为空

这一步没有告诉我们“真正谁对”，但它明确说明：

**DB elapsed 绝不能直接解释成 tactic 本身的 CPU 计算量。**

#### 尝试 E：用 `sorry-prefix` 缩短前缀 proof

相关材料：

- [reports/proof-staticize/evidence/task-logs/bhk807037.output](evidence/task-logs/bhk807037.output)
- [reports/proof-staticize/evidence/task-logs/bykhisw2p.output](evidence/task-logs/bykhisw2p.output)
- [reports/proof-staticize/evidence/profiler-validation/raw-exp5-sorryprefix-stuck-crunch.out](evidence/profiler-validation/raw-exp5-sorryprefix-stuck-crunch.out)

做法是：

- 把目标前的 76 个 lemma proof 尽量 `sorry` 掉；
- 希望更快 reach 到 `empty_slot`。

结果却是：

- 即使这样，`parallel_proofs=0` 下仍然 **>23 分钟** 没有 reach 到目标；
- 卡住的是 `crunches set_original, set_cdt` 一类 setup 命令。

这一步非常关键，因为它说明：

**阻塞 reach 的主因不是前缀 lemma proof，而是 setup。**

### 6.3 实验四的结论

实验四最终没有测到 `empty_slot_pas_refined` 的真实 tactic 内部成本。  
但它把问题推到了一个更深的位置：

1. import 复现不忠实；
2. 并行 profile 不决定性；
3. 同步 profile 跑不动；
4. `sorry-prefix` 也过不了 setup 墙；
5. DB elapsed 不能直接当 tactic CPU。

因此，到这一步我们已经不只是“没测到一个 lemma”，而是发现：

**对真实 seL4 中段重 tactic 做忠实 intra-tactic profiling，本身就受到 Isabelle future 和前缀 setup 的结构性阻碍。**

这个发现自然把问题引向下一步：

**如果 reach 墙主要卡在 setup，那 build 的主要成本是不是本来就不在单条 tactic 上？**

这就进入实验五。

---

## 7. 实验五：量化 build 成本，确认真正的全局杠杆在哪里

### 7.1 为什么做这一步

在实验四里，我们本来是想解释一条 lemma 为什么慢；  
结果却不断撞到 `crunch`、`locale interpretation`、future 调度这些问题。

所以实验五换了一个角度：

**不再死盯某条 lemma，而是看整个 build 的 timed commands 到底把时间花在哪。**

### 7.2 方法

使用工具：

- [tools/seL4-proof-search/Isa-Repl/profiler/quantify_setup.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/quantify_setup.py)

输入是全部 session build DB 里的 timed commands。  
输出是按命令类别和关键字的 elapsed 汇总。

### 7.3 结果

总计：

- `30141` 条 timed commands
- 合计 `52095s`

按大类分布：

| 类别 | sum_s | 占比 |
|---|---:|---:|
| `proof`（`apply/by`） | 34048 | 65.4% |
| `other`（`ML/autocorres/install_C_file/...`） | 12108 | 23.2% |
| `setup`（`interpretation/context/locale/crunch/...`） | 5540 | 10.6% |
| `statement` | 398 | 0.8% |

按关键字看最重命令：

| name | sum_s | count | 均/条 |
|---|---:|---:|---:|
| `apply` | 26470 | 23019 | 1.15s |
| `by` | 7519 | 2294 | 3.3s |
| `ML` | 6908 | 23 | 300s |
| `autocorres` | 3660 | 1 | 3660s |
| `interpretation` | 2483 | 295 | 8.4s |
| `context` | 1510 | 344 | 4.4s |
| `install_C_file` | 821 | 1 | 821s |

这些数字背后的意义很清楚：

- `proof` 占比虽然高，但它是 **23019 条小 `apply`** 摊出来的；
- 真正高杠杆的，是少数巨型命令：
  - `ML`
  - `autocorres`
  - `install_C_file`
  - 重 `interpretation` / `locale`

尤其把 C-infra 相关命令合起来看：

- `ML 6908s`
- `autocorres 3660s`
- `install_C_file 821s`

约 **11389s ≈ 22%**，而且只藏在十几条命令里。

### 7.4 实验五的结论

这一步第一次把全局杠杆讲清楚了：

**proof tactic 不是不能优化，但它不是 seL4 build-wall 的主杠杆。**

原因不是 proof 累计时间不高，而是：

- proof 成本高度分散；
- 单条平均收益太小；
- 而 setup / C-infra 成本高度集中在少数大命令上，更像真正值得打的对象。

到这里，整个调查链条已经闭合：

- 原搜索路线挑错了对象；
- 真对象又很难忠实测；
- 而从全局看，真正的成本杠杆本来就主要不在 proof tactic 上。

---

## 8. 到这一步，我们最终能得出什么结论

顺着上面的实验链条，最终能成立的不是一句简单的“proof lemma 优化无效”，而是更具体的几条判断。

### 8.1 关于原 proof-staticization 路线

我们现在可以明确说：

1. 原路线的候选选择方法不可靠。  
   `search_frac` 和原分类器系统性高估了 `search-dominated` 样本。

2. 在现有 122 个慢 lemma 候选中，文本上可确信的 `search-pure` 样本是 0。  
   所以原路线并没有真正把“最该做 search-elimination 的对象”挑出来。

3. 对真实 seL4 中段重 tactic 的忠实 profiling，在工程上非常困难。  
   我们并没有拿到 `empty_slot_pas_refined` 这类旗舰样本的决定性 search/simp 拆分。

### 8.2 关于 build-level 优化方向

我们也可以明确说：

1. proof-lemma 优化不是当前 seL4 build-wall 的主优化路线；
2. 主要杠杆在 setup / C-infra；
3. 真正更值得投入的是：
   - `autocorres`
   - C 相关 `ML`
   - `install_C_file`
   - CRefine / Refine 中重 `locale/interpretation`

### 8.3 关于 proof lemma 优化这条路本身

最重要的是，这里不能跳太大。

我们**不能**说：

- “proof lemma 优化走不通”
- “search-elimination 没有任何收益”
- “不存在局部成功案例”

当前证据只能支持到：

**proof lemma 优化在 seL4 中更像一条寻找少数局部正例的路线，而不是主优化路线。**

---

## 9. proof lemma 优化的正面案例：目前已经拿到的两个局部成功样本

前面的实验主要是在解释“为什么原 search-elimination 路线站不稳、为什么 proof-level 不是 build-level 主杠杆”。  
但这并不等于“proof lemma 优化没有任何成功案例”。截至目前，我们已经核实了**两个局部正例**：它们都把 `fastforce` 改成了 `clarsimp`，并在保留证明正确性的前提下获得了稳定的单 lemma 加速。

相关原始材料在：

- diff：  
  [reports/proof-staticize/evidence/local-positives/both-diffs.patch](evidence/local-positives/both-diffs.patch)
- VSpacePre_AI 日志：  
  [reports/proof-staticize/evidence/local-positives/VSpacePre_AI-162_probe-baseline-clarsimp-simp.log](evidence/local-positives/VSpacePre_AI-162_probe-baseline-clarsimp-simp.log)  
  [reports/proof-staticize/evidence/local-positives/VSpacePre_AI-162_reproduce-x2.log](evidence/local-positives/VSpacePre_AI-162_reproduce-x2.log)
- Decode_IF 复测日志：  
  [reports/proof-staticize/evidence/local-positives/Decode_IF-137_my-remeasure-before-after.log](evidence/local-positives/Decode_IF-137_my-remeasure-before-after.log)

### 9.1 正例一：`VSpacePre_AI.thy:162`，`arch_update_cap_valid_mdb`

当前源码位置：  
[verification/l4v/proof/invariant-abstract/VSpacePre_AI.thy](/home/lijun/seL4-docker-main/verification/l4v/proof/invariant-abstract/VSpacePre_AI.thy:162)

改写如下：

```diff
-   subgoal by (fastforce simp: is_cap_simps)
+   subgoal by (clarsimp simp: is_cap_simps)
```

这是一个非常小的 proof-level 改动：没有改 lemma statement，没有改上下文，也没有引入新辅助引理，只是把最后一个子目标从 `fastforce` 收窄到 `clarsimp`。

现有证据显示：

- baseline 探针日志给出：  
  - `fastforce`：`23398ms`  
  - `clarsimp`：`18288ms`
- x2 复测给出：  
  - baseline：`23120ms`, `23854ms`，平均 `23487.0ms`  
  - `clarsimp`：`18367ms`, `18528ms`，平均 `18447.5ms`

按复测均值计算，`clarsimp` 相比 `fastforce` 快了约：

- `5039.5ms`
- **21.5%**

此外，同一探针日志还记录了一个更激进的 `simp` 版本，它直接失败；因此这个案例的意义不是“任何更轻方法都行”，而是：

**这里确实存在一个比 `fastforce` 更窄、但仍能闭合目标的 tactic 选择。**

### 9.2 正例二：`Decode_IF.thy:137`，`OR_choice_def2`

当前源码位置：  
[verification/l4v/proof/infoflow/Decode_IF.thy](/home/lijun/seL4-docker-main/verification/l4v/proof/infoflow/Decode_IF.thy:137)

改写如下：

```diff
-  by (subst no_state_changes[where f=c], simp, fastforce simp: bind_assoc split_def)
+  by (subst no_state_changes[where f=c], simp, clarsimp simp: bind_assoc split_def)
```

这个案例同样是纯 proof-level 改写：

- 保留原 lemma statement；
- 保留前半段 `subst` 与 `simp`；
- 只把最后闭合步骤从 `fastforce` 改成 `clarsimp`。

独立复测日志给出：

- AFTER（`clarsimp`，当前版本）：`26078ms`, `26605ms`，平均 `26341.5ms`
- BEFORE（回退到 `fastforce`）：`29717ms`, `29697ms`，平均 `29707.0ms`

按均值计算，改写后快了约：

- `3365.5ms`
- **11.3%**

这条案例的重要性在于：

- 它不是同一文件里的重复样本；
- 它由 agent 先提出，你又做了独立复测；
- 说明“把 `fastforce` 换成更窄的 `clarsimp`”在某些 lemma 上确实可能带来稳定收益。

### 9.3 这两个正例说明了什么

这两个样本共同说明：

1. **proof lemma 优化不是完全没有成功案例。**
2. **局部 proof-level 改写确实可能产生可复现收益。**
3. **收益形式更像“收窄 tactic”而不是“复杂静态 rule-chain 搜索”。**

也就是说，至少在一部分目标上，真正有效的动作并不是把 proof 改写成很长的静态脚本，而是把原来偏重的 `fastforce` 换成更窄、更直接的 `clarsimp`。

### 9.4 这两个正例还不能说明什么

虽然这两个案例都是真的正例，但它们仍然只是**局部成功样本**，还不能推出更强结论：

1. 它们还不能说明这类改写在 seL4 中很普遍。  
2. 它们还不能说明局部 per-lemma 收益会转化成 whole-theory 或 whole-session 的明显 wall-time 收益。  
3. 它们还不能反驳前面的大结论：**proof-level 不是 build-level 主杠杆。**

因此，这两个案例在本报告中的定位应当是：

**proof lemma 优化存在局部成功样本，但这些样本目前更适合作为“副线正例”，而不是主优化路线的依据。**

---

## 10. 当前实验的不足

到目前为止，这项工作的不足主要有四类。

### 10.1 还没有拿到真实重型 tactic 的决定性内部归因

我们知道：

- 文本分类不够；
- profiler 在隔离 HOL 用例上可信；

但我们仍然**不知道**像 `empty_slot_pas_refined` 这样的真实重型目标，其 tactic 内部究竟是：

- search 为主
- simp 为主
- setup / context reach 为主
- 还是几者混合

### 10.2 还没有形成成熟正反案例集

目前的证据更强在“否定原筛选方法”和“重新定位主杠杆”，而不是在系统展示：

- 哪些 lemma 可成功优化
- 哪些 lemma 不可优化
- 为什么它们分别成功 / 失败

### 10.3 DB elapsed 的解释仍带并行调度 caveat

我们已经知道 DB elapsed 不能直接当 tactic CPU；  
但对于某些单条重命令，它到底混入了多少 future / 调度 / 等待效应，仍没有完全拆清。

### 10.4 报告当前仍偏“负筛查”，而非“正例工程”

这份报告已经能很好解释：

- 原路线为什么站不稳；
- 为什么 proof-level 不是主杠杆；

但它还不能独立支撑“lemma 优化正面路线图”，因为正例部分还没成体系。

---

## 11. 总结

把整个过程按顺序串起来，结论就比较清楚了。

1. 我们原本沿着 proof staticization / search-elimination 路线工作；
2. 先发现原候选选择方法可能挑错对象；
3. 实验一证实原分类器系统性高估了 search 成分；
4. 实验二用更诚实的 `v2` 分类器重跑，发现现有 122 个慢候选里 `search-pure = 0`；
5. 为了拿真实标签，实验三搭建并验证了 intra-tactic profiler；
6. 实验四尝试把 profiler 打到真实 seL4 重 lemma 上，但被 future 与 setup 墙挡住，没有测得决定性 tactic 内部成本；
7. 实验五转向全局 build 成本量化，发现真正高杠杆成本集中在 setup / C-infra，而不是单条 proof tactic。

因此，本报告最终支持的判断是：

**在 seL4 中，proof lemma 优化不是当前 build-wall 的主优化路线。**  
它没有被证明“绝对走不通”，但现阶段更合理的定位是：

**作为一条寻找少数局部正例的副线继续，而不是承担主优化路线。**

如果后续要把这条副线继续推进，下一步最重要的不是再重复原来的 loop，而是：

1. 先找到更可信的 search-heavy 样本；
2. 再补齐真正完整的 lemma 优化正例；
3. 同时把主要优化精力转向 setup / C-infra。

---

## 12. 相关脚本、日志与报告索引

### 12.1 原路线与审计

- [tools/seL4-proof-search/Isa-Repl/audit/README.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/README.md)
- [tools/seL4-proof-search/Isa-Repl/audit/block2-scripts.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/block2-scripts.md)
- [tools/seL4-proof-search/Isa-Repl/audit/block4-loop-design.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/block4-loop-design.md)
- [tools/seL4-proof-search/Isa-Repl/audit/block6-threats-and-verdict.md](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/audit/block6-threats-and-verdict.md)

### 12.2 分类器与候选集

- [tools/seL4-proof-search/Isa-Repl/scan_db_timings.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/scan_db_timings.py)
- [tools/seL4-proof-search/Isa-Repl/classify_db_candidates.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/classify_db_candidates.py)
- [tools/seL4-proof-search/Isa-Repl/classify_db_candidates_v2.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/classify_db_candidates_v2.py)
- [tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/runs/db_candidates.json)
- [tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified.json](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified.json)
- [tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified_v2.json](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/runs/db_candidates_classified_v2.json)

### 12.3 profiler 工具链

- [tools/seL4-proof-search/Isa-Repl/profiler/time_profile.snippet.thy](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/time_profile.snippet.thy)
- [tools/seL4-proof-search/Isa-Repl/profiler/parse_profile.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/parse_profile.py)
- [tools/seL4-proof-search/Isa-Repl/profiler/profile_lemma.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/profile_lemma.py)
- [tools/seL4-proof-search/Isa-Repl/profiler/run_profile.sh](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/run_profile.sh)
- [tools/seL4-proof-search/Isa-Repl/profiler/db_cpu_vs_elapsed.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/db_cpu_vs_elapsed.py)
- [tools/seL4-proof-search/Isa-Repl/profiler/quantify_setup.py](/home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl/profiler/quantify_setup.py)

### 12.4 关键证据目录

- [reports/proof-staticize/evidence/profiler-validation/](evidence/profiler-validation/)
- [reports/proof-staticize/evidence/task-logs/](evidence/task-logs/)
- [reports/proof-staticize/evidence/code-snapshot/](evidence/code-snapshot/)
