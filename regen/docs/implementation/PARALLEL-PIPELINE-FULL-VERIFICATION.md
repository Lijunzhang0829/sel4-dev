# 修改后完整代码复验

> 历史记录：后续用户撤回环境内容冻结需求，运行时工具链扫描已移除。当前边界与工作记忆修复见 [需求复核](PARALLEL-PIPELINE-REQUIREMENTS-REVIEW.md)。

当前结论：**本轮发现的 1 项 P1 和 3 项 P2 已修复并通过复验**。完整回归 721 项通过（32.044s），原始故障复现已重新执行。尚未完成两个真实 File Planner 从零生成与最终全仓集成，不能据此宣称完整实验验收通过。

前一轮复验新增通用原生验证工具 `regen/tools/native_pipeline_validation.py`，没有修改 execution/planning/infrastructure 实现，没有调用生成模型，也未改写旧实验。

## 已通过的验证

- 完整回归：713 tests PASS，30.485s。
- 真实混合 proof/statement 冷检查：native=1 为 23.380s，native=2 为 12.688s，两组均通过，同名 lemma 的独立上下文无串扰。
- 真实当前 dataset 及现有 Astra run 的 checker 环境字段/路径与 `verify_environment` 匹配。
- 使用冻结 Semantic Base 的通用 helper/consumer：第一次 batch 13.889s，复用同一热前缀的第二次 batch 0.445s；两次均通过现有 batch evidence validator。
- 同一通用 theory 的完整检查 12.400s、原生依赖审计 64.305s、独立 generated session build 44.520s，全部通过；旧热 worker 已关闭且进程退出。
- 真实本地 Python 子进程取消测试：A 被取消并终止，独立 B 正常完成；不将其冒充 Isabelle 取消实验。

这些是通用声明/证明和基础设施验证，零 benchmark contracts、零模型调用，不等于 File Planner 自动生成或整个 Refine 仓库验收成功。此次 audit/build 的开销明显高于第二次热 batch；只有一组通用样本，不能推导所有 theory 的性能。

## 原始问题与复现（修复前）

### P1：最终全集依赖未绑定的派生 files

`shared_proof_workspace.py:74–79` 校验 `file_list` 的 hash，但 theory entries 来自另一个未对照验证的 `files` 字段。`repository_integration.py:97` 又把 `files` 中的 contracts 当作全仓目标。

复现保持冻结 file_list 的 Source/Target 不变，仅删除派生 files 中的 Target；manifest 静默缩为 Source。模拟构建成功后得到 repository_verified=True。调度器也可能把遗漏 theory 当作不存在。

必须在共享输入入口对照冻结 file_list 验证 files 的 theory/file_slot/contract_ids，最终全集以冻结目标为准。复现使用真实临时文件和 journal，native build 为 mock，不代表真实 Isabelle 接受了假证明。

### P2：成功读取的观察未完整进入后续模型输入

`parallel_file_planner.py:89` 的观察合并只保留 facts/async_queries/workspace，遗漏 read_facts/read_queries 等选择状态；`batch_file_planner.py:482` 的模型输入与查询缓存也未消费 async_queries。

两次查询共 30 条事实后，read_facts(fact-0) 与 read_queries 均返回 available，但 read_theory 的 query_index/query_memory/requested_queries 为空，选中的 fact-0 不在后续 recalled_facts。证明后与恢复后同样丢失选择；重复相同查询仍重新检索（两种唯一查询触发 3 次 batch_query）。

应统一观察 reducer、索引和缓存入口，维持独立观察记忆与选择，不能只保证即时读取接口返回成功。

### P2：旧观察迟到后回滚较新的工作区选择

`parallel_file_planner.py:101` 只排序当前尚未应用的 task，没有持久化全局选择顺序。A 先提交并挂起；B 后提交且完成，proof 接受后已保存 workspace=new；A 后来完成，状态又变成 workspace=old。

需要独立、稳定的观察顺序，并对每类选择比较新旧。旧结果仍可读取，但不能覆盖更新的选择；不增加数学决策计数。

### P2：淘汰关闭失败时丢失仍存活 worker 的登记

`check_executor.py:65` 先 pop victim 再 close_prefix。关闭因 Docker/进程超时失败，仍存活的 worker 已不在池中；再次申请可在 resident_limit=1 下得到两个存活实例。

关闭成功后才移除登记；失败时保留占用并阻止超额 admission。纯 Python 故障注入已复现，不是实际 Isabelle 内存峰值测量。

## 证据与边界

目录：`reports/analysis/parallel-pipeline-full-verification-20260924/`。

- `mixed-native/report.json`：真实不同上下文 proof/statement 混合并发检查。
- `native-lifecycle-final/report.json`：真实热 batch、complete、audit、build 的任务与阶段证据。
- `parallel-integration-fixed-list-repro.py`：全集缩小复现。
- `verify_parallel_observations.py`：选择/索引丢失、重复检索和迟到覆盖复现。
- `reverify-runtime-eviction-failure.py`：关闭失败后的登记丢失；当前故障断言失败符合预期。
- `reverify-runtime-python-cancellation.py`：真实本地子进程取消隔离。
- `regression-summary.txt`、`reviewed-code-sha256.json`：回归摘要与被验收实现身份。

首次 native 验证脚本误将实际 v2 worker 标识判为 fallback，检查本身已成功；修正验证脚本后以全新输出目录重跑，首轮不作为完整通过证据。历史结果均保留。

尚未运行两个真实 File Planner 从零生成及最终全仓集成；没有完整 RSS/CPU 校准。下述四项已修复；这些尚未执行的实验仍是完整验收的边界。

## 本轮修复与复验

- 冻结全集：共享入口逐项对照冻结 file_list 的 theory、file_slot、contract_ids，并核验绑定的 package；集成使用该入口导出的固定全集。省略 theory、改 slot、删改 contract 清单均在 native build 前拒绝。旧清单没有 contract_ids 时从已绑定 package 确定全集。
- 观察记忆：统一使用原观察 reducer，合并显式读取的事实、查询、诊断页、proof state、证明样例和符号观察；query_index、模型输入与恢复读取同一记忆。异步查询缓存核验结果摘要及原有查询上下文身份。内部缓存路径不进入模型摘要。
- 乱序处理：任务提交顺序持久化，每种选择保存自己的顺序。旧任务结果保持可读，但不能覆盖较新的选择；证明动作自己的 workspace 也记录顺序。观察不增加证明决策计数。
- 资源关闭失败：close_prefix 成功后才移除 resident；异常仍占用额度。回归同时检查重试阻塞与清理恢复后可继续调度。

新增 8 项测试；完整 721 项回归通过。原始复现结果：两种唯一查询加一次重复查询，仅调用检索 2 次；选中的旧事实及 query index 在证明后和恢复后仍存在；晚到 workspace 不回滚；删 theory 不会获得 repository_verified；关闭失败两次仍只有一个登记及存活实例。

当前代码复现与回归证据保存在上述证据目录的 `fixes/`：`observations.log`、`integration.log`、`eviction.log`、`regression.log`、`code-sha256.json`。历史复现输出保留，须区分修复前后。

本轮没有模型调用，也没有重新执行真实 Isabelle 生命周期；前面的 native 时间属于前一轮结果。本轮验证针对 Python 状态、覆盖约束、缓存及资源失败路径，没有新增探索次数或候选生成限制。
