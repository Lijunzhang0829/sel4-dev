# Isabelle 检查接口与并发边界

> 2026-09-25：当前仅在不同 theory 之间并行，单 File Planner 的读取/检索/检查均串行等待，接口功能保留。本文实验与基线分析保留历史内容；当前行为以 [整体流程](../design/PARALLEL-PROOF-PIPELINE.md) 和 [实现记录](PARALLEL-PIPELINE-IMPLEMENTATION.md) 为准。

2026-09-24，核对 shared-proof-workspace / 7c4b96b 工作区。本文描述当前代码；并行改造方案已确认但尚未实现，未进行新的 native 性能测试。
模块 TODO、任务状态与验收标准统一见 [端到端流程 M6](../design/PARALLEL-PROOF-PIPELINE.md#m6)。

## Agent 到 Isabelle

当前持续工作区 agent 通过 `proof_action(theory, proposal)` 调用 batch 控制器，而非直接获得任意 Isabelle shell/ML 执行接口。

```
WorkspaceController.proof_action
  → batch_file_planner._run_batch_file
  → proof_batches.prepare_batch / check_batch
  → BatchWorker（Docker 中的持久 isabelle process）
  → batch_checker.ML：执行候选、审计、保存诊断
  → 验证结果与提交证据 → 接受整个 batch 或返回失败
```

入口见 `regen/planning/proof_workspace_agent.py`、`regen/planning/batch_file_planner.py`。

## Agent 可用的动作

| 动作 | 实际作用 | 是否执行 Isabelle |
|---|---|---|
| `submit_batch` | 图修订与多个节点候选；按合法顺序验证，整个 batch 成功才提交 | 是；需要时还做声明检查 |
| `resolve_symbols` | 在声明上下文解析常量、缩写、类型等名称；不是 theorem 检索 | 缓存未命中时执行专用检查 |
| `query` | 前提 exact/name/search/goal/proof-example 检索 | 主要查数据库；部分定义解析可转到符号检查 |
| `read_proof_state` | 读取指定失败尝试保存的目标、假设和诊断 | 否，不是任意交互式 proof state 执行器 |
| `read_diagnostic` / `read_attempts` / `read_facts` / `read_proofs` / `read_queries` | 读取已保存上下文、结果和证据 | 否 |
| `observe` | 对同一快照一次读取 1–8 项 | 当前 Python 循环顺序执行；具体项可能触发符号查询 |
| `finish` | 完整证明检查、审计、生成 session 构建、seal | 是 |

这些主要是 `proof_action` 的 proposal 动作，不是全部独立顶层工具。顶层另提供工作区/theory/共享成果读取及 `prepare_reuse`。当前仍有 `pause` 动作；新设计取消主动跨 theory 让出尚未修改生产实现。

## 内部封装与实际检查范围

`regen/execution/isabelle_node_checker.py` 的 `IsabelleChecker` 提供：

- `check_graph`：声明检查，保留各节点 context，以 `oops` 结束声明，不证明节点；有缓存。batch 对声明覆盖充分时可省去单独检查。
- `check_node`：旧单节点路径，目标及合法已接受上下文；不是当前 batch 主路径。
- `check_complete`：完整已生成证明源码检查。
- `audit_complete_theory`：完整 theory 的依赖、自动化上下文与语义证据审计。
- `build_generated_session`：生成 ROOT 并运行 `isabelle build`。当前 seal 依次调用完整检查、审计与 build，并非只有一次热增量检查。
- `resolve_symbols`：转到 `symbol_resolution.py`，冷检查与结果缓存。

冷检查由 Docker 调用 `isabelle process`，加载父 session heap，挂载冻结来源、候选目录与生成目录。seal 使用 `isabelle build`。此证明执行封装的命令组装没有显式设置 `-j` 或 `threads`；不能由此推断实际只有一个 CPU 线程。另一个入口 `planning/workspace_preflight.py` 已显式设置 `threads=4`；新资源配置应覆盖两条路径。

默认 `batch_incremental=True`。`regen/execution/batch_checker.py` 启动专属持久进程，加载一次 accepted prefix，之后提交新增源码后缀。成功候选经 harness 接受后，在下一次 align 时提升为 base；失败不推进 base。imports 或前缀不兼容时重建，部分进程/协议异常回退冷检查。

完整候选包含 accepted proofs 与本次候选，未提交且未提供证明的节点不进入证明检查；不是把全部其他节点设为 `sorry`。ML 审计使用 `Global_Theory.get_thms` 获取事实，并检查新增公理、oracle、skip_proof 等证据。batch 中前面完成而后面失败的节点不会自动成为 GREEN。

## 并发边界

| 层次 | 当前情况 |
|---|---|
| 一个 batch 的多个证明 | 支持批量提交；`batch_checker.ML` 按 transition 顺序 fold 执行、按编号串行处理请求；不是多个候选检查任务并发 |
| 同一 BatchWorker 同时检查两个候选 | 不支持。Python request index 与 ML base/pending 是可变状态，没有并发请求隔离 |
| 不同 theory、不同 checker 进程 | 底层具备隔离基础。`repository_execution.setup` 隔离生成目录和可写 heaps；旧 `run_session` 有线程池，但调用旧 run_file 路径 |
| 新版持续 File Planner 多实例 | 尚未接通：当前 worker_count=1，持有全 run mutation lock；切换 theory 还会关闭其他 theory 的热前缀 |
| 同 theory、冻结前缀上的独立候选 | 可以设计为多个只读验证 worker，但当前没有实现；不得共享同一个可变 BatchWorker |

Isabelle 自身支持并行 build jobs，以及 prover 内部的多线程 theory/proof checking，参见[官方 session 文档](https://www.cl.cam.ac.uk/research/hvg/Isabelle/dist/library/Doc/System/Sessions.html)。这不等于本 harness 的顺序 ML 请求循环会自动把独立节点分派到多个 worker；`build -j` 也不是一个 batch 的节点并发开关。

单 theory 单写入者与多个只读验证进程并不矛盾，但后者需要固定来源快照、隔离工作目录/进程、串行提交和合并上下文复验。若 B 依赖本次新增 A，须按依赖顺序验证或留在同一顺序 batch；不能把未证明 A 当可信前提。独立候选分别成功也不保证合并后的名称、属性和自动化上下文一致，不能直接拼接 GREEN。

首版优先不同 theory 的检查并行，保留各自内部顺序 batch；先实测 native=1/2 的冷启动、热检查、seal、CPU/RSS 与墙钟，再决定资源池大小。同 theory 多分支检查另行评估，避免重复前缀加载抵消收益。
