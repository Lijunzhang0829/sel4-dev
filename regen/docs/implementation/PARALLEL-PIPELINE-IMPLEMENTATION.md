# 跨 theory 并行、File Planner 内部串行：实现与验证

> 2026-09-25 更新：单 File Planner 内部已恢复串行；不同 theory 继续并行。完整流程、模块产出和验证责任见 [流程主文档](../design/PARALLEL-PROOF-PIPELINE.md)。环境由人工保证不变，强杀等外部干扰暂不作为本阶段验收要求。

对应 [设计与模块 TODO](../design/PARALLEL-PROOF-PIPELINE.md)。实现基于 `shared-proof-workspace` / `7c4b96b` 工作区；旧 `workspace` 入口保留，新入口为 `parallel-workspace`，只接受拥有 theory 独立构建目录的 `refine-dataset` 输入。本轮没有调用生成模型、没有向新运行注入旧 GREEN，也没有完成新的 benchmark theory 实验。

## 已接通的流程

1. 环境与仓库规划沿用现有入口、来源验证、结构校验和原生声明预检。修正 source-visible/blind 的 prompt 自相矛盾；preflight 使用统一任务执行器，声明 PASS 不产生 proved contracts。
2. `init` 绑定现有计划、固定 contracts、环境配置与实现身份；环境内容由人工维护不变。新 runner 再冻结所选 theories、模型、超时、native 资源和集成政策。修改执行代码或资源政策后创建新 run，不改写旧绑定。
3. 程序按硬 `required_imports` 与既有 ready 排序分配 theory；每个活跃 theory 一个持续 Codex 工具会话和控制器。一个完成后立即补位，不等待同批其他 theory。协作组不形成 barrier，不增加每步总控 LLM。
4. File Planner 可自由修改未证明 helper、探索候选及提交有序 batch；固定 contracts 与已接受证明仍由现有 validator 保护。新的 prompt 不再列出数学停滞时的 `pause` 动作；确实无法完成时允许如实结束为未完成，不把它当成功，也不自动给它增加重试次数。
5. `submit_check` 与 `proof_action` 都执行完当前操作才返回。同 theory 的检索、分组读取、声明、符号、proof batch、seal 及直接读取共用工具串行锁；一个完成后才开始下一项。任务均设置 theory owner，执行池 workers 等于最大活跃 theory 数。正常返回包含终态、结果和 task ID，agent 不需继续轮询；`get_check_result/wait_checks/cancel_check` 保留作结果读取及兼容控制。
6. 原有 batch controller 仍验证图、上下文、证据、完整 batch 和提交身份。任务执行器仍持久化请求和结果，但后台线程不再让同一 File Planner 重叠开展其他操作。观察收据在读取时投影为工作记忆，下次证明动作合入非证明 journal 反馈；不伪造证明计数或 GREEN。旧观察顺序字段及 reducer 保留作恢复兼容。
7. 原完整检查、语义/依赖审计和 session build 构成 seal；仅 seal 才解锁硬依赖。最后独立 integration gate 验证全集 exact-once 覆盖、精确渲染源码、实际 imports、seal 证据，并在隔离输出中统一 build。进度中的 `repository_verified` 必须匹配当前 manifest，不能只看局部终态。

## 代码对应

| 模块 | 实现入口 | 核心变化 |
|---|---|---|
| M0–M2 | `planning/workspace_planner.py`、`workspace_preflight.py` | visibility 一致；复用冻结输入与验证；原生预检任务化 |
| M3–M4 | `planning/parallel_file_planner.py` | theory 持续所有权、事件补位、全部工具串行等待、取消兼容 |
| M5 | `execution/shared_proof_workspace.py`、`planning/batch_file_planner.py` | 固定提交快照、按提交缓存索引；分离检索上下文与日志目录 |
| M6 | `execution/check_tasks.py`、`check_executor.py` | durable 任务、唯一任务目录 owner、native FIFO 槽、显式线程、热进程数量上限 |
| M7 | `execution/proof_commits.py`、`repository_execution.py` | 只读 replay、短 journal 事务、取消/接受互斥、完整发布投影 |
| M8–M9 | `execution/repository_integration.py`、既有 seal | 保留 seal 标准；新增独立全集构建及当前验收凭据验证 |
| M10 | `infrastructure/progress_events.py`、`workspace_transcript.py`、`codex_workspace_session.py` | theory/task 事件隔离、任务请求 provenance、协作取消远程等待 |

## 资源、所有权与恢复

- `TheoryController._tool_lock` 仅属于一个 theory，从输入快照读取到操作完成返回持续持有；不同 theory 不争用此锁。task 查询/wait/cancel 作为兼容控制可由外层访问，不开启第二项数学操作。
- run mutation lock 由协调入口持有，以排除外部重复 runner/初始化；内部 agents 不争用此锁执行模型或 Isabelle。每个 theory 控制器和任务 owner 保证单写入，journal/cache/publish 使用短锁。
- `max-parallel-theories` 同时限制持续模型会话数；`native-slots` 单独限制活跃 native 检查；`native-threads` 明确传给 Isabelle；`resident-limit` 限制活跃及闲置热 checker 数，达到上限时关闭闲置前缀。同 checker 启动冷 check/build 前会关闭原热前缀，避免一次计数包含两个进程；它仍不是按实际 RSS 实现的内存预算。
- 独立 task 输出不覆盖其他任务；同 checker 的可变前缀独占。任务超时不修改共享 `checker.timeout`。嵌套 native 调用只在同线程内复用资源 lease。
- 任务保存请求后才派发，结果先落盘再返回。完成收据可修复“result 已存但 task 状态未更新”的崩溃窗口。其他非终态任务在重启时标记 `interrupted`，不盲目重跑。
- 接受 GREEN/seal 与取消在同一 guard 上排序；已经接受的证明不撤销，已取消的未提交结果不能进入 cold fallback 或变 GREEN。恢复以 journal 为准；不保证物理检查恰好一次。
- Ctrl-C/SIGTERM 使协调器停止接收任务、取消在途任务，并通知模型会话退出。底层强杀/主机崩溃的自动进程回收暂不实现，恢复前人工处理残留资源；不假定进程中断等于模型已经收到结果。
- 同一 run 再执行同一命令会开新会话读取 durable 状态、已提交成果和 task 引用；这不是恢复之前 Codex 会话的聊天上下文。从零对照必须创建新 run。

## 使用入口

仓库计划兼容时可以沿用，无需因为更换执行器重做分组。以下路径为占位，使用已接受的 dataset/计划目录；初始化不会复制旧运行的证明历史。

```bash
python3 -m regen.execution.proof_session_runner init \
  --dataset-root /path/to/accepted-dataset \
  --run-dir /path/to/new-run --run-id new-parallel-run

python3 -m regen.execution.proof_session_runner parallel-workspace \
  --run-dir /path/to/new-run \
  --max-parallel-theories 2 --native-slots 1 \
  --native-threads 4 --resident-limit 2 \
  --allow-external-call
```

模型必须匹配冻结配置，当前配置为 `gpt-6-astra / medium`。`--theory NAME` 可重复指定小范围；只完成子集不会成为全仓验收通过。全部固定目标 seal 后自动运行 integration，也可显式调用 `integrate --run-dir ...` 复验；该入口同样经过任务执行器。

根 policy 和 theory session policy 记录 `theory_tool_execution: serial`。`submit_check` 参数格式不变，但返回时已经结束，不再返回用于交错操作的在途任务。

`--call-timeout` 是 provider 事件不活动超时，不是提案次数或整场数学探索预算；`--candidate-timeout` 与 `--check-timeout` 控制相应 native 调用。

## 查看进展

| 路径（相对 run） | 内容 |
|---|---|
| `parallel-workspace-policy.json` | 冻结范围、模型和资源 |
| `parallel-sessions/<session>/<theory>/` | agent policy、transcript、关键阶段事件、result/failure |
| `check-tasks/<task>/task.json` | 类型、归属、输入绑定、queued/running/终态 |
| `check-tasks/<task>/result.json`、`completion.json` | 完整结果与恢复收据 |
| `check-tasks/<task>/runner-events.ndjson` | 入队、获得 native 槽、检查与结果就绪事件 |
| `theories/<slot>/acceptance/` | GREEN/seal 的权威接受历史 |
| `integration-tasks/`、`integration/<id>/` | 集成任务、冻结 manifest、统一 build 证据 |
| `repository-verification.json` | 最新集成结果引用及 hash |

没有周期心跳。任务完成、Isabelle 判定和 journal 接受是不同状态；前端无显示时可直接检查这些文件。

## 本轮证据与仍待验收

串行化后完整回归 **723 tests PASS，35.618s**。针对性覆盖：submit 返回已提交结果、同 theory 的观察/proof/直接读取不重叠、两 theory 控制器实际线程重叠、完成即补位、结果重读不重复证明、同步调用期间受控取消、检索记忆与恢复。证据在 `reports/analysis/serial-file-planner-20260925/`。

本轮未调用模型或重新执行 native seal；成功 proof 的 Python 测试使用合成 native evidence。下文原生测量是此前执行器隔离测试，不能当作本次串行化后的生成速度对照。

真实 Isabelle smoke 使用冻结 Semantic Base，分别运行同名 lemma 的 nat 证明与 bool 声明检查；输出和上下文隔离。首轮 native=1 为 23.50s，native=2 为 12.69s，均通过。这是一次冷进程隔离测试，不能推导完整生成任务有相同加速。最终代码复验 native=1 为 **27.49s**、native=2 为 **13.15s**，两组均通过；结果保存在 `reports/analysis/parallel-pipeline-implementation-20260924/native-smoke-final/report.json`。

仍待独立实验：两个真实 File Planner 从零生成、完整 seal 与全仓 integration；热 prefix 混合负载及 RSS/CPU 校准；基于实际引用的仓库分组/复制成本评估。索引项已缓存，但源 journal 文件完整性检查仍扫描前缀，尚非纯事件驱动索引服务。未接受的 raw native 结果尚无单独自动重提交协议；恢复能读取已完成结果和恢复 GREEN，但未知任务必须显式处理。以上保留在主清单，不以单测替代验收。
