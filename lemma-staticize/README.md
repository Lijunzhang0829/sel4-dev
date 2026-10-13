# lemma-staticize — heavy-automation lemma 静态化改进的全过程归档

本目录单独存放"把 seL4 proof 里 heavy-automation lemma 改写成静态 tactic"这次改进的
**完整可复现资产**:驱动脚本、已验证的底层脚本、lemma 候选集。目的是让审稿人/复现者
能看到"我们试了哪些 lemma、怎么改、改写 agent 每一步试了什么、为什么可行/不可行"。

## 目录结构

```
lemma-staticize/
├── bench.sh                 # 本次新增的主驱动:选样本→改写→计时对照→收拢全部 trace
├── scripts/                 # bench.sh 复用的、此前已验证过的底层脚本
│   ├── ab_agent.py          #   改写引擎:A→B 重构,只输出 audit 通过的静态 tactic
│   │                        #   (rule/erule/drule/`simp only:`;强制拒绝 auto/blast/metis/...)
│   ├── gate.py              #   裁决:check-theory.sh 验正确性 + isar timing 测 before/after
│   ├── check-theory.sh      #   gate 的正确性闸:容器内 isabelle process build 改写后的 .thy
│   ├── proposer_host.py     #   可选 LLM 提议器(host 上跑 claude -p,容器内无 OAuth 时用)
│   └── run_loop.sh          #   单 lemma 两阶段编排的原型(bench.sh 复用其 docker-exec 调用)
├── candidates/              # lemma 候选集 + 计时数据(选样本的输入)
│   ├── ranking.json         #   461 个已计时 lemma,按 elapsed 排序(bench.sh 选样本的源)
│   ├── top30.json           #   top-30(两视图:by_elapsed / by_recoverable)
│   ├── db_candidates_classified_v2.json  # 122 个 lemma 的 search-vs-work 分类
│   ├── pool_arm.json        #   ARM 架构下扫到的 single-classical 候选池
│   └── l4v_keywords.json    #   l4v 自定义 outer-syntax 关键字(isar timing --l4v 需要)
└── runs/                    # ★每次 bench 的全部 trace(默认输出位置,见下)
    └── bench-<时间戳>/
        ├── candidates.json  #   本次选了哪些 lemma、为何入选
        ├── command.sh       #   可复现本次运行
        ├── summary.md/csv   #   成功率 / 加速比 / 失败-timeout 汇总
        └── <NN>-<lemma>/    #   每个 lemma 一个子目录
            ├── before.txt    #     原始 proof 源码片段
            ├── search.log    #     agent 实时高层日志([init ok]/reached/PATH)
            ├── ab.json/md    #     ★每一次 attempt 的 tactic+结果(全保真)
            ├── events.jsonl  #     ★逐行事件流:每步带完整 fail_full + goal_sig + cp→child 血缘
            ├── tree.txt      #     ★从 events 渲染的搜索树(按递归深度缩进,含剪枝/分支)
            ├── gate.log      #     build + 计时 stdout(NO-PATH 时跳过)
            └── result.json   #     该 lemma 裁决

全保真 instrumentation(ab_agent.py)对每次 attempt(fail 与 progress 均如此)记录:
完整未截断的 prover 失败消息 `fail_full`(含失败时 goal 状态)、完整结果 goal signature
`goal_sig`(非仅 `ngoals` 计数)、checkpoint 血缘 `cp`/`child`,并旁路成 `events.jsonl` 事件流
——使搜索树与每步内部状态在「未找到路径」时也能完整回放。
```

> 注:`scripts/check-theory.sh` 是**容器内**执行的脚本(gate.py 通过 docker-exec 在
> `/workspace/.claude/skills/.../check-theory.sh` 调用它),这里放一份只为归档完整,不从本目录直接跑。

## 四个环节(对应优化方法论)

1. **挑样本** — `bench.sh` phase 0 从 `candidates/ranking.json` 筛 classical-search tactic
   (auto/force/fastforce/blast/metis/...),按 `elapsed × search_weight` 排序取 top-N。
   `search_weight`:metis/blast/fast=1.0,force/fastforce=0.55,auto/safe=0.30,
   并按 `simp:`/`add:` 显式列表数下调(这些时间是 rewriting 不是 search)。
2. **改写对照版** — 每个 lemma 跑 `scripts/ab_agent.py` 搜一条 audit 通过的静态路径。
   保留 before 源码片段(`before.txt`)和 after 静态 path。
3. **跑时间对照** — `scripts/gate.py`:先 `check-theory.sh` 验改写后能 build(正确性),
   再 `isar timing --lemma` 测 before/after 单 lemma 耗时,**默认 REPS=3 遍**。
   裁决:`ACCEPT`(正确且更快>MARGIN)/ `REJECT-no-speedup` / `REJECT-incorrect` /
   `INCONCLUSIVE-*`(低于噪声地板或计时失败)/ `NO-PATH`(没找到静态路径)。
4. **存完整 Trace** — 全部收进本归档的 `lemma-staticize/runs/bench-<时间戳>/`
   (由 `bench.sh` 的 `OUTROOT` 默认指向,可用 `OUTROOT=... ./bench.sh` 覆盖):
   - `candidates.json` — 选了哪些、为何入选(elapsed/recoverable)
   - `<NN>-<lemma>/before.txt` — 原始 proof 源码片段
   - `<NN>-<lemma>/search.log` + `ab.{json,md}` — **agent 每一次 attempt 的 tactic/结果/原因**
   - `<NN>-<lemma>/gate.log` — build + 计时的完整 stdout/stderr
   - `<NN>-<lemma>/result.json` — 该 lemma 裁决
   - `summary.md` / `summary.csv` — 成功率、加速比、失败/timeout 汇总表
   - `command.sh` — 可复现本次运行的精确命令

## 怎么运行

> ⚠️ 本目录的 `bench.sh` 是**归档副本**。脚本内部工作目录(`DIR`)仍指向原始位置
> `tools/seL4-proof-search/Isa-Repl/`(它要 docker-exec 进 `sel4-l4v` 容器、读那里的
> `runs/ab-*.json`),但**运行结果默认写到本归档的 `lemma-staticize/runs/`**(`OUTROOT`)。
> **运行请用原始路径的脚本**:

```bash
cd /home/lijun/seL4-docker-main/tools/seL4-proof-search/Isa-Repl
./bench.sh                          # 默认:top 3 by recoverable,heuristic 提议器,3 reps
N=10 VIEW=by_elapsed ./bench.sh     # top 10 按原始耗时
N=5 PROPOSER=llm REPS=2 ./bench.sh  # 切 LLM 提议器(需 proposer_host.py 在 host 有凭证)
```

环境旋钮:`N`(候选数)、`VIEW`(by_recoverable / by_elapsed)、`MIN_MS`(噪声地板,默认 100)、
`PROPOSER`(heuristic / llm)、`REPS`(计时重复遍数,默认 3)。

## 设计取舍

- **默认 `PROPOSER=heuristic`**:纯规则、可复现、不依赖 host 上 claude OAuth。
  `PROPOSER=llm` 历史上找到的静态路径更多,但需要 `proposer_host.py` 的凭证,最小版未默认开。
- **bench.sh 复用 `run_loop.sh` 已验证的两阶段 docker-exec**(phase 1 ab_agent 搜索 / phase 2
  gate 裁决,顺序执行避免 8GB JVM 与 12GB 计时服务器抢内存),没另起炉灶;区别只是把散在
  `/tmp` 和全局 `runs/` 的输出收拢成一个可审计的 bench 目录。
- **裁决用 gate.py 而非 in-JVM 计时**:后者每步有 6–8ms IPC 偏置;gate 用容器内
  `isar timing --lemma` 单 lemma 整体计时 + `check-theory.sh` 真实 build,带 50ms 噪声地板
  和 20ms 胜出余量,避免 sub-floor lemma 的裁决在多次运行间翻转。
