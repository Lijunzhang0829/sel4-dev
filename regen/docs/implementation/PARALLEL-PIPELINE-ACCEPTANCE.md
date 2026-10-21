# 并行证明流水线代码验收

> 后续完整复验：正常 native 生命周期通过，但仍有 1 项 P1 与 3 项 P2，见 [完整复验报告](PARALLEL-PIPELINE-FULL-VERIFICATION.md)。

> 修复复验更新：以下 5 项已完成代码修复，并新增 `test_parallel_acceptance_fixes.py` 将缺陷转为回归用例。完整回归 **713 项通过（31.284s）**；`git diff --check` 通过。下文保留首次验收的问题及证据，不能再作为修复后缺陷仍存在的结论。完整模型生成与实际热进程资源实验仍待进行。

修复对应：共享读取不再二次读 HEAD；集成 manifest 绑定并前后复验工具链/context；并行入口拒绝旧共享目录输入；冷 check/build 启动前关闭同 checker 热前缀；完成的只读 task 进入可恢复观察记忆，支持 read_facts/read_queries/workspace，且同步工具返回 task ID。观察记忆仅在已有单写入控制器开始证明动作时作为非证明反馈合入 journal，不产生 GREEN，也不增加证明决策计数。


验收结论：**暂不通过**。范围为 `shared-proof-workspace` / `7c4b96b` 上当前未提交实现；仅验收，没有修改执行代码或开启生成实验。

重新运行 `python3 -m unittest discover -s regen/tests/current -q`：**707 项通过，30.481s**；`git diff --check` 通过。专项故障注入确认以下 5 项问题。单测通过不足以覆盖新增并发边界，设计清单的完成勾选不能替代这些验收。

## 确认的问题

### 1. P1：共享快照的旧路径/新 HEAD 竞争仍存在

位置：`regen/execution/shared_proof_workspace.py:163`（第 127 行已经捕获前缀，163–168 行又读取 HEAD）。

另一个 writer 在读者取到路径列表后追加合法提交，读者会把新 HEAD 与旧列表比较并抛出 `workspace committed history is missing`。这会使正常共享成果读取失败；调度器的 workspace 汇总同样使用此路径，可能中断运行。

复现注入的是合法 journal 追加：旧读取报错，而重新读取正常返回 2 条成果。应对同一个捕获前缀完成验证，不再次读取较新 HEAD。当前测试仅验证 `commit_paths`，遗漏其调用者重新读 HEAD 的行为。

### 2. P1：独立集成入口跳过冻结 Isabelle 身份检查

位置：`regen/execution/repository_integration.py:112`；对照 `regen/execution/repository_execution.py:382`。

seal 后 Isabelle 安装目录内容变化，旧 `checker_for()` 会拒绝，新 integration 直接构造 checker，继续 build。若 build 返回 green，仍写 `repository_verified=True`；`current_verification()` 也继续返回 True。应在开始构建和成功发布前复验冻结 checker 环境，并让验收记录绑定该环境。

复现使用真实临时目录和字节漂移，native build 结果为 mock：它证明校验被绕过，不表示真实 Isabelle 接受了错误证明。

### 3. P1：兼容输入仍共写 ROOT，却被并行入口接受

位置：`regen/planning/parallel_file_planner.py:312`；共享路径在 `regen/execution/repository_execution.py:385`，写 ROOT 在 `regen/execution/isabelle_node_checker.py:773`。

非 `refine-dataset` 输入沿用共享 `run_dir/generated` 与 heap，但新入口没有拒绝这种输入。并行 seal/build 会覆盖 ROOT 和 build.log。复现中两次调用分别报告检查 A、B，两个模拟进程实际读到的 ROOT 都只有 B。

当前 direct dataset 主路径已有 theory 独立目录，不受此兼容范围问题影响。首版可直接拒绝未隔离的输入；若继续支持，则所有输入都必须隔离生成和构建目录。

### 4. P2：resident-limit 低估真实进程数

位置：`regen/execution/check_executor.py:62`，冷进程启动在 `regen/execution/isabelle_node_checker.py:447`。

完成 batch 后保留热 BatchWorker，随后 seal 的完整检查又启动冷进程。池只记录一个 checker，未分别计入同 checker 的两个存活进程。配置 resident-limit=1 也会同时保留热进程和冷检查，不能作为实际驻留进程或内存上限。

复现模拟 native 启动与热进程存在状态，输出 registered_checker_count=1、idle_hot_worker_still_alive=True、cold_process_started=True。应按进程预留容量，或在启动冷 seal/build 前关闭相应闲置热前缀；补充热 batch→seal 的混合检查。

### 5. P2：查询结果与后续读取接口断链

位置：`regen/planning/parallel_file_planner.py:230`、`:265`，同步兼容返回在 `:137`。

query/observe 的结果仅存在 task，未进入观察记忆；继承的 prompt 却仍承诺 read_facts/read_queries/workspace.record_ids 可引用之前的查询与持久选择。同步包装还丢弃 task ID。复现 query 返回 fact-1 后，read_facts([fact-1]) 返回 unknown exact IDs，batch_facts 为空。

应将 task 中的只读结果接入独立观察记忆或统一基于 task 的读取接口，并同步修正 prompt；不要求把检索结果写为证明 journal 或 GREEN。现有测试仅验证检索路径，没有覆盖 query→读取→后续使用链。

## 通过项与边界

回归覆盖任务幂等、完成收据恢复、取消与接受互斥、独占 owner、异步 batch 基本接受、事件补位、源码漂移拒绝等。此前真实 native 冷进程隔离 smoke 通过的事实不变；本轮未重新启动 Isabelle 或模型实验。

尚未完成：两个真实 File Planner 从零生成并全部 seal/集成；热前缀混合任务的实际驻留量、RSS/CPU、超时/强杀恢复验收。应先修复以上 5 项，并把复现转换成回归测试，再进行小规模端到端验收。statement 对 notes/workspace 的规范化还存在较小接口不一致，可随接口修订处理。

## 复现材料

路径：`reports/analysis/parallel-pipeline-acceptance-20260924/`，含每项脚本和输出、回归摘要、被验收代码的 SHA256。

从仓库根运行，例如：

```bash
PYTHONPATH=. python3 reports/analysis/parallel-pipeline-acceptance-20260924/review-shared-snapshot-race.py
```

这些脚本复现缺陷时正常退出；mock/synthetic 使用范围见各项说明，不应计入 benchmark 证明成功率。
