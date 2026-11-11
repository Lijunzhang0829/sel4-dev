# 跨 theory 并行、File Planner 内部串行：实现与验证

> 2026-09-29 整合：协议 reader、sleep v2、取消／固定 transcript 前缀、热 worker
> owner lease 及完整理论 Arch 渲染策略的最新边界，见
> [Harness 应用层框架](HARNESS-APPLICATION-FRAMEWORK.md)。下文各日期的测试数量和
> 缺口描述保留为当时记录，不替代最新验收结论。

> 2026-09-25 源码工作区更新：默认接口已变为持久 `.thy` 编辑、批量检查及修订，见 [源码工作区](../design/THEORY-SOURCE-WORKSPACE.md)。默认流程已同步如下；旧 graph/submit_check 仅兼容历史接口，当前流程以主文档 M4/M7 为准。

> 2026-09-25 串行化更新：单 File Planner 内部已恢复串行；不同 theory 继续并行。完整流程、模块产出和验证责任见 [流程主文档](../design/PARALLEL-PROOF-PIPELINE.md)。环境由人工保证不变，强杀等外部干扰暂不作为本阶段验收要求。

对应 [设计与模块 TODO](../design/PARALLEL-PROOF-PIPELINE.md)。初始并行化实现基于 `shared-proof-workspace` / `7c4b96b` 工作区；旧 `workspace` 入口保留，新入口为 `parallel-workspace`，只接受拥有 theory 独立构建目录的 `refine-dataset` 输入。该初始验收没有调用生成模型或注入旧 GREEN。后续源码工作区及声明索引解耦的验证见下方最新记录。

## 已接通的流程

1. 环境与仓库规划沿用现有入口、来源验证、结构校验和原生声明预检。修正 source-visible/blind 的 prompt 自相矛盾；preflight 使用统一任务执行器，声明 PASS 不产生 proved contracts。
2. `init` 绑定现有计划、固定 contracts、环境配置与实现身份；环境内容由人工维护不变。新 runner 再冻结所选 theories、模型、超时、native 资源和集成政策。修改执行代码或资源政策后创建新 run，不改写旧绑定。
3. 程序按硬 `required_imports` 与既有 ready 排序分配 theory；每个活跃 theory 一个持续 Codex 工具会话和控制器。一个完成后立即补位，不等待同批其他 theory。协作组不形成 barrier，不增加每步总控 LLM。
4. File Planner 持续编辑源码，可增删、改写或重排 helper，批量检查。固定 contracts 声明受保护；改写已接受证明需整版复验，失败保留草稿但不替换已接受版本。新的 prompt 不再列出数学停滞时的 `pause` 动作；确实无法完成时允许如实结束为未完成，不把它当成功，也不自动给它增加重试次数。
5. 默认 `proof_action` 的 edit/check/finish 及观察均完成后返回；`submit_check` 只在旧图接口开放。同 theory 的检索、分组读取、声明、符号、proof batch、seal 及直接读取共用工具串行锁；一个完成后才开始下一项。任务均设置 theory owner，执行池 workers 等于最大活跃 theory 数。正常返回包含终态、结果和 task ID，agent 不需继续轮询；`get_check_result/wait_checks/cancel_check` 保留作结果读取及兼容控制。
6. 共享 batch controller 验证声明索引、上下文、精确源码版本、完整 batch 和提交身份；文档模式不调用局部 DAG 构建/拓扑规划。任务执行器仍持久化请求和结果，但后台线程不再让同一 File Planner 重叠开展其他操作。观察收据在读取时投影为工作记忆，下次证明动作合入非证明 journal 反馈；不伪造证明计数或 GREEN。观察记忆实现命名为 observation_memory；旧 async_observations/async_* 保留作恢复兼容。
7. 原完整检查、语义/依赖审计和 session build 构成 seal；仅 seal 才解锁硬依赖。最后独立 integration gate 验证全集 exact-once 覆盖、精确渲染源码、实际 imports、seal 证据，并在隔离输出中统一 build。进度中的 `repository_verified` 必须匹配当前 manifest，不能只看局部终态。

## 2026-11-04：正向证明／证伪策略

新建 repository run 默认冻结
`contract_outcome_policy=contract-dispositions-v1` 和
`unresolved_evidence_policy=proof-or-refutation-v1`。固定 contract 只有完整正向证明
或完整负向证书可以终结：普通 RED、单次候选超时、sketch／helper 失败、模型叙述和
空转都不能产生 UNKNOWN。File Planner 继续使用原生反馈修复；全局预算耗尽时暂停，
所有未证目标保持 pending。首版不增加逐 contract 计时、超时处置或独立修复 agent。

正向 GREEN 仍须通过绑定声明／上下文／实际 imports 的完整原生检查和审计后提交；
完整 theory 的 seal 与仓库 integration 独立验证。负向 REFUTED 使用 v2 导出整个
固定目标后证明其否定，只有原生证书校验成功才能写入当前 contract 的处置记录。
负向检查失败、超时或 unsupported 后继续 pending。一个 contract 被证伪不能停止
其他 pending contract，也不产生可用正向事实。部分封存仅允许每个未证 contract
均有合法负向证书，不能用 UNKNOWN 或遗漏 pending 目标代替；零正向导出仍不代表
仓库验证成功。

工具提示及帮助按冻结策略提供；新策略不提供 report_unresolved 出口，直接调用也被
可信发布入口拒绝。该限制同时检查历史记录读取和部分封存证据回放，不能通过低层
写入或伪造处置绕过。空转 worker 可以暂停并记录尚未解决的目标，但不写 UNKNOWN。

历史缺省策略、`native-attempts-v1` 及已发布的显式 outcome-policy-upgrade 入口
继续按原规则重放。旧升级入口仍固定升级到 native-attempts-v1；普通 continuation
继承原策略，不因本次默认值变化静默更换语义。

## 2026-11-03：逐目标退出与原生尝试证据

### 上下文证伪与全仓库已封存事实发现

`contract-refutation-v2` 在冻结 imports 和 owned context 中读取原始声明，用
Isabelle 的 assumption／variable export 补全前提、展开局部定义并闭合参数。
退出所有 owned contexts 后，才构造并证明完整命题的否定；目标本身不被证明或注册
为事实。内核审计另外绑定导出的目标项，仍要求无 skip proof、oracle、遗留 hypotheses
或新增 axioms。旧 v1 证书通过冻结的 `contract_refutation_v1.ML` 与原渲染方式重放。
`read_entry.refutation_capability` 允许尝试上下文导出；原生解析仍可对不支持的语法
返回 unsupported，例如当前的 obtains／includes。unsupported 不产生终结处置。

纯导出覆盖检查不调用模型、不证明 benchmark 目标：

```bash
python3 -m regen.tools.refutation_export_scan \
  --dataset /path/to/dataset --output /path/to/fresh/check \
  --check-arch-probes
```

可选 probes 是在空／Arch 上下文中人为构造的 False 小例子，不属于 benchmark contracts。

`goal_query`／绑定残余目标查询从本轮全部认证封存结果中发现候选，包括未导入的
provider；上游仍使用原冻结数据库。每次查询绑定不可变的发现目录快照，先索引再
计算相似度，并核对候选声明、证明与当前 provider journal／seal。返回
`provider_theory`、`provider_commit_id`、`provider_seal_id`、`import_name`、
`requires_import` 和 `can_use_as_current_premise`；这些字段不证明数学适用性。
未导入候选保留词法排名并标记 requires_import，仅当前可见事实进入原生结构匹配。
File Planner 选择新增 import 后仍走已有语义复验和证明重验事务。
新 seal 改变目录快照和 query identity，旧检索记忆不能阻止发现新结果。
document／imports 接受事件也记录证明范例来源；证明改写、撤销和环境重验更新版本。

此前新建 repository run 默认绑定 `contract_outcome_policy=contract-dispositions-v1`
和 `unresolved_evidence_policy=native-attempts-v1`；该默认已被上方 2026-11-04 策略替代。
历史 run 缺少这些字段仍按原策略
重放；普通 continuation 显式继承旧值，不能因默认值变化而静默升级或改写原 run。

严格策略下，`report_unresolved` 每次只处理一个未证明的固定 contract。harness
从已绑定的检查记录验证该目标确实失败，并验证修改候选后再次检查的证据。仅有模型
理由、helper 失败、条件性 sketch、未执行到的批次目标、重复缓存、过期上下文或
基础设施故障，都不足以终结目标。`read_entry` 的 `unresolved_eligibility` 返回
可用证据与缺口，便于 File Planner 修订局部路线或转向其他 pending 目标。
UNKNOWN 是当前搜索的处置，不是不可证结论；目标声明、已有 GREEN 和累计费用保持。

`no_progress_turn_limit` 只限制没有新原生工作的空转 final turns，不是证明失败次数。
严格策略达到该阈值后，控制器提示其他 pending 目标；一轮提示仍无工作则以
`planner_idle_without_outcome_evidence` 停止 worker，保留所有未处置目标。它不会
替模型为未尝试目标写 UNKNOWN。独立且 ready 的其他 theories 仍可运行。
基础设施故障单独记录，不可用 `proof_search_exhausted` 代替。

逐目标 UNKNOWN 后，其他目标继续；正向部分导出仍须通过完整源码检查和发布审计。
`exported_partial` 只使已检查的正向事实可用，不代表 theory 或仓库全部完成。

旧实验需要显式升级时，使用独立目录准备 continuation：

```bash
python3 -m regen.tools.outcome_policy_continuation \
  --source-run /path/to/stopped/proof \
  --run-dir /path/to/new/proof \
  --authorization "升级逐目标退出策略，保留历史证明和预算"
```

该命令只准备，不启动实验。源实验必须已停止、使用 document 接口且预算干净暂停；
已存在 contract dispositions、部分导出或反证结果的迁移不在此入口支持范围内。
旧 theory UNKNOWN 归档为历史，未证目标重新开放；已有 GREEN、seal 与累计预算
均保留。只有预算迁移完成后才发布准备回执，未完成的目录不能开始原生检查或新建预算。

## 代码对应

| 模块 | 实现入口 | 核心变化 |
|---|---|---|
| M0–M2 | `planning/workspace_planner.py`、`workspace_preflight.py` | visibility 一致；复用冻结输入与验证；原生预检任务化 |
| M3–M4 | `planning/parallel_file_planner.py`、`theory_document.py`、`declaration_index.py` | theory 持续所有权、源码草稿/版本、独立声明校验、全部工具串行；imports 派生就绪 |
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
  --max-parallel-theories 1 --native-slots 1 \
  --native-threads 4 --resident-limit 1 \
  --allow-external-call
```

Ours 新运行默认使用一个 File Planner worker、一个 native slot 和一个驻留 checker，以单 worker 进行比较。显式设置 `--max-parallel-theories 2 --resident-limit 2` 仍可开展并发实验；已有冻结运行保留原配置。

模型必须匹配冻结配置，当前配置为 `gpt-6-astra / medium`。`--theory NAME` 可重复指定小范围；只完成子集不会成为全仓验收通过。全部固定目标 seal 后自动运行 integration，也可显式调用 `integrate --run-dir ...` 复验；该入口同样经过任务执行器。

根 policy 和 theory session policy 记录 `theory_tool_execution: serial`。默认源码模式不暴露 submit_check/prepare_reuse；旧接口的 submit_check 返回时已经结束。

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

## 初始串行化证据与仍待验收

串行化后完整回归 **723 tests PASS，35.618s**。针对性覆盖：submit 返回已提交结果、同 theory 的观察/proof/直接读取不重叠、两 theory 控制器实际线程重叠、完成即补位、结果重读不重复证明、同步调用期间受控取消、检索记忆与恢复。证据在 `reports/analysis/serial-file-planner-20260925/`。

本轮未调用模型或重新执行 native seal；成功 proof 的 Python 测试使用合成 native evidence。下文原生测量是此前执行器隔离测试，不能当作本次串行化后的生成速度对照。

真实 Isabelle smoke 使用冻结 Semantic Base，分别运行同名 lemma 的 nat 证明与 bool 声明检查；输出和上下文隔离。首轮 native=1 为 23.50s，native=2 为 12.69s，均通过。这是一次冷进程隔离测试，不能推导完整生成任务有相同加速。最终代码复验 native=1 为 **27.49s**、native=2 为 **13.15s**，两组均通过；结果保存在 `reports/analysis/parallel-pipeline-implementation-20260924/native-smoke-final/report.json`。

仍待独立实验：两个真实 File Planner 从零生成、完整 seal 与全仓 integration；热 prefix 混合负载及 RSS/CPU 校准；基于实际引用的仓库分组/复制成本评估。索引项已缓存，但源 journal 文件完整性检查仍扫描前缀，尚非纯事件驱动索引服务。未接受的 raw native 结果尚无单独自动重提交协议；恢复能读取已完成结果和恢复 GREEN，但未知任务必须显式处理。以上保留在主清单，不以单测替代验收。


## 最新声明索引解耦验收（2026-09-25）

默认 document 模式独立构建声明索引；局部 DAG 只留在旧接口/历史回放。当前源码可改写
已接受证明，但完整检查成功前不替换版本。imports 循环/就绪、单 theory 单写入和
最终 seal/integration 标准保留。底层 graph/nodes 名称仍是 checker/journal 兼容格式。

完整回归 741 项通过（33.904s），真实 Isabelle 合成小例 GREEN → 删除 helper 后 RED
→ 修复消费者后 GREEN；不是 Refine 完成或加速结论。详细证据及受控停止的实验身份见
[源码工作区验证记录](../design/THEORY-SOURCE-WORKSPACE.md#声明索引解耦后的复验)。


## 名称与并发审查补充（2026-09-25）

- 文档模式公开工具参数/反馈使用 entry，原生声明检查采用 Declaration_Check，进度派生
  文件为 declaration-index.json；旧 journal/checker wire schema 在适配边界保留。
- 修复 list_workspace 在跨 theory 并行中仍报告 worker_count=1；分别展示单 theory
  writer=1、max_parallel_theories、native_slots、resident_limit。
- 默认源码模式的轻量测试确认同 theory observe 会阻塞后续 read/edit，两个 theory
  实际重叠执行，native_slots=1 只让第二次 native 请求排队。
- CheckExecutor 内部派发线程与 File Planner 可交错的异步接口是两回事；当前模型
  所有工具等待终态，observe 内也顺序读取。旧异步设计文档已就地标明历史状态。
- 未启动新的生成模型实验；不能将这些接口/并发测试解释为 Refine 加速或完成率证据。


本轮验证：完整 Python 回归 **757 项通过（36.603s）**；默认 document 并发专项 3 项、
命名/接口与证据绑定专项均通过。真实 Isabelle `Declaration_Check_v2` 声明检查 GREEN
（11.642s，proved_contracts=0）。证据位于
`reports/analysis/document-interface-native-20260925/`：python-tests.log、result.json、
evidence.json、validation-summary.json。没有启动新模型生成实验或迁移旧历史记录。

## Native watchdog 与模型消息收尾恢复（2026-11-04）

工具结果在 `CodexWorkspaceSession` 中分别记录 persisted、sent、server_confirmed。
确认绑定 thread/turn/callId 及原始响应内容，允许提前、乱序和跨 turn 迟到；缺确认
仅作协议诊断，不能重复执行工具，也不能据此宣称模型已理解结果。未闭合 commentary
单独记录，不按消息年龄或无 GREEN 时长判断证明停滞，不补造 `item/completed`。

candidate-response-watchdog 仍是基础设施错误。仅在故障 worker 已关闭、未接受的
pending 已清除并已移出 checker 后，返回明确的 retryable 反馈；不产生数学 RED、
GREEN 或 UNKNOWN。下一次显式检查重建已提交 GREEN prefix，草稿和失败历史保留。
前缀启动、真实 I/O、证据绑定或清理失败仍按基础设施故障处理。致命 native 异常
通过 `turn/interrupt` 和真实 interrupted 终态完成有界收尾，失败保留原异常和证据。

旧运行恢复使用 `parallel_repair_continuation --native-watchdog-repair`，要求精确的
watchdog/工具/会话证据及替换实现审查。恢复最新有效提交和原草稿，全部历史费用
原值继承，不重做规划、不退还或重置预算、不修改旧的时钟不确定性记录。

`regen.tools.native_watchdog_repair_smoke` 用合成 HOL 目标及真实 Isabelle 验证
已验收前缀 → 注入响应 watchdog → 反馈返回且无新 GREEN → 新 worker 重检 → seal。
注入仅改变一次测试 worker 的延迟及宿主等待期限；没有模型调用、没有 benchmark
目标或人工目标证明输入。完整输出保存在本轮恢复实验的 validation 目录。

### Independent checks and explicit work switching

Document `check_theory` checks multiple new proof bodies separately under
`independent-document-entries-v1`. The selected source is frozen once, entries
are visited in source order, and each candidate contains the certified GREEN
prefix plus exactly one new proof. Other pending fixed statements retain `oops`
carriers; their statements are not available facts. No `sorry` is inserted.
An ordinary RED or native candidate timeout does not suppress subsequent checks.
A successful predecessor is audited and journal-committed before a later entry
may use it. `feedback.entry_results` gives each verdict, diagnostic, elapsed
check time and evidence address; `new_green` lists independently published
successes. Mixed results have publication `partially_accepted`. The saved full
draft remains unchanged. Infrastructure failure/cancellation can leave explicit
not-checked entries; it does not establish a negative mathematical result.

Single-body checks retain their existing path. Edits to already accepted proof
bytes or declarations require whole-context revalidation, rather than silently
substituting old accepted bytes. `finish` still validates the complete theory,
imports and semantic audit before seal. The explicitly selected historical
`bounded-audited-prefix-v1` strategy retains its original stopping policy.

`switch_target(entry_id, reason, notes?)` selects another pending fixed contract
inside the assigned theory. It returns the exact target body and persists the
choice without editing proofs, resetting budgets or disposing any contract.
The live controller/native cache is reused. `switch_target(theory, reason,
notes?)` instead yields the current worker to a different theory in the current
scheduler scope whose providers are certified. The coordinator prioritizes that
theory and moves the yielding theory behind other ready work. It later resumes
from its journals, draft and saved switch notes; immutable switch receipts also
restore ordering after coordinator restart. Neither operation records UNKNOWN.
Native actions remain serial and `worker=1` remains one active File Planner.

Durable logical state and a live Isabelle process are distinct. A local contract
switch retains the process and prefix. A theory yield releases native residency,
so a later resume may replay the certified prefix. Retaining every theory's live
Isabelle process is not promised by `resident_limit=1`. Process loss or a native
watchdog also requires reconstruction; persisted journals alone cannot recreate
the process's in-memory heap without loading/replay.

### Reviewed sketches v2: semantic route review and entry-local gates

New `parallel-workspace` runs default to `sketch-reviewed-v2`; `baseline` is the
current ablation. Omit the strategy on resume to use the exact frozen version.
Historical strategy names remain accepted by the current CLI only for exact frozen replay,
and their prompt hashes and review requirements are preserved. The shared entry-local authorization and
readiness feedback also apply to existing document strategies.

After `check_sketch`, each helper review records its role (`classification`,
`bridge`, `local`), definition references, representation/case branches, premise
sources and unresolved cases. These are agent-authored semantic claims, explicitly
marked `semantic_claims_verified=false`. The harness validates their presence
and receipt bindings; it does not prove that a list of cases is exhaustive.
Conditional closure still proves only that the consumer closes assuming its
helpers, and publishes no GREEN.

Classification/bridge helpers must pass ordinary native checking before sibling
helper work is authorized. Helpers in a child sketch serving such a critical
helper remain available. A newly accepted critical helper unlocks later entries
in the same frozen multi-entry request, using the evolving committed state.
A false helper disproves that candidate route only: it does not refute the fixed
contract. Unrelated fixed contracts remain available and can be selected with
`switch_target`.

For new proof bodies in one `check_theory`, a missing/stale conditional receipt,
review or diagnosis returns `blocked_workflow` for that entry, with
`native_check_executed=false`. Other independently eligible entries continue.
Malformed source, changed fixed statements, corrupt receipts and infrastructure
errors still stop the operation. Native successes before a later global error
remain separately journal-committed and are reported. Accepted-body rewrites
and final seal retain whole-context revalidation.

`read_theory.workflow.readiness` and `read_entry.workflow_readiness` report
specific invalidity causes and source-bound `next_actions`. An action is complete
only when `requires_agent_fields` is empty; the harness never invents a review,
diagnosis or proof plan. Compatible helper-proof-only edits and extensions of
the certified GREEN prefix reuse their conditional certificates. Changes to
helper statements, consumers, use sites or incompatible imports/prefixes require
the corresponding checks again.

A v2 leaf can choose diagnostic mode with a compatible probe receipt, or formal
mode with a nonempty `proof_plan` and no short probe. A supplied probe must still
match its context. A timeout leaves feasibility `unestablished`; it cannot prove
hardness or make a helper GREEN. No new probe-success threshold, repair-round
limit or elapsed-time decomposition rule is introduced. The ordinary proof
audit, fixed parent check and complete theory seal remain mandatory.

### One versioned policy definition

`generation_policy.py` owns strategy names, instructions, capabilities and the
new-run default. Runtime `DocumentStore` callers pass one `generation_strategy`;
seven old boolean attributes are derived compatibility views, not independently
configured parameters. Explicit legacy constructor arguments remain available to
old adapters, but cannot override a named strategy. Document-mode requirements
and the historical bounded-prefix check path derive from the same definition.

`leaf_review_issue` is shared by review submission, stored-review authorization
and readiness feedback. Invalid or foreign receipts still raise integrity errors;
only actual workflow prerequisites become local blockers. This consolidates
repeated decisions without dropping native evidence or changing fixed contracts.
Default v2 does not enable the historical guided scores or phased retry-count
gates. These remain available only through their explicit historical workflows.

The parameter inventory and validation evidence are recorded in
`reports/regen/generation-policy-consolidation-20261108-001/REPORT.md`.

### Current launch interface

`regen.execution.parallel_cli` defines 19 public options for `parallel-workspace`.
Executing this generation command enables its audited model session; it needs no
separate `--allow-external-call` switch. Authentication is resolved through the
existing `CODEX_HOME`/default clean-home mechanism. Explicit `codex-home` and
`focus-contract-id` remain advanced diagnostic inputs outside normal help.

New experiments use Document and accept only `baseline` or `sketch-reviewed-v2`.
The graph selector and custom `execution-policy` file are retired from the new
launch surface. Older frozen interfaces/policies can be decoded unchanged, and
cannot be selected as new experiment configurations through compatibility flags.
The generic legacy `workspace` command keeps its old interface independently.

The public budget inputs are `--budget-seconds` (new default 48 hours),
`--planning-budget` (first handoff only), and `--resume-authorization` (resume
audit reference). New clocks use the single `experiment-runtime` policy; there
is no clock selector. Resumes inherit their exact frozen policy and allowance,
including historical clocks. The allowance includes imported planning costs.
Resume rejects a changed allowance; consumed work never resets. Old extension/wall options remain
only in the frozen effective-time compatibility decoder, outside current help.

Native resource semantics are unchanged: one run's native_slots limits active
requests in that run's pool; native_threads controls internal Isabelle
parallelism. Two independent pools do not mutually exclude each other. Host-wide
file-lock admission is an opt-in lower-level capability and is not automatically
enabled by native_slots=1.


### Unified experiment clock (2026-11-08)

Current Ours planning and generation use
`experiment-runtime-minus-evidenced-interruptions-v1`. The only public duration
is `--budget-seconds X`, default 172800 seconds (48 hours). `planning-budget`
identifies the initial paid planning receipt; `resume-authorization` is an audit
reference, neither is a clock strategy or second allowance.

Effective experiment time is elapsed experiment time minus the union of recorded
pauses/offline gaps and trusted worker/network interruption intervals. Normal
planning, model reasoning/waiting, retrieval, prefix preparation, failed proof
attempts, native-reported TIMEOUT, seal and integration count. The initial
planning-to-generation handoff counts once. Explicit stopped/resumed gaps do not.
Ordinary in-run reconstruction counts; a forced exact-prefix/import rebuild
caused by a confirmed discarded unresponsive worker can be excluded once.

The harness binds native outage evidence to the request, worker, prefix and imports,
missing response and completed worker cleanup. Provider evidence must be a trusted
transport/service failure; only the measured wait since the last real model
feedback is refundable. Silence, a hard lemma or missing commentary completion
alone is insufficient. The model cannot issue refunds. Native and provider
intervals share one durable correction ledger: overlaps are credited once,
healthy work in another worker or overlapping model/native work stays charged.

A late diagnosis may correct already charged time, including after a clean
pause or provisional budget cutoff, without changing proofs, token usage or the
original allowance. Corrections are immutable, request-bound and idempotent.
They do not automatically resume work. Use the normal explicit continuation
path after a correction. A stopped audit uses
`correct_stopped_experiment` under the run mutation lock; unobserved crash tails
require reconciliation rather than guessed exclusion. Historical clocks remain
replayable with their frozen policies and are not silently recalculated.

Validation and runnable fault-injection evidence:
[unified clock report](../../../reports/regen/unified-experiment-runtime-20261108-001/REPORT.md).

### Publication admission and failed publication scheduling

`finish` compares the saved draft against the certified entries. If declarations,
imports and proof bodies are identical, a different draft order does not trigger
another candidate-suffix check. `publication-admission.json` records the requested
and certified orders; publication uses the certified order and still requires the
complete native check, semantic audit and session build. The draft is preserved.
Changed proofs or interfaces retain whole-document revalidation, using the existing
`check_timeout` instead of sharing the ordinary `candidate_timeout`. The Python
and ML workers receive the limit per request; subsequent ordinary candidates keep
their original deadline. A smaller cached candidate timeout cannot satisfy or
replace this publication check.

When a newly submitted finish task fails after every fixed contract is accepted,
the controller records `publication-deferred/<task>.json` and ends that File Planner
session as `unpublished`. The coordinator excludes this theory from immediate
resubmission and proceeds with other ready theories. Proofs and drafts remain;
no UNKNOWN or REFUTED disposition is created. Infrastructure failures retain their
existing classification. Reading an old failed receipt does not defer a new session.
The theory must be repaired and explicitly resumed before publication is retried.

Regression coverage is in `test_publication_admission.py`; the model-free
`publication_budget_native_smoke.py` verifies timeout/whole-check/timeout on one
real warm Isabelle worker without proving benchmark contracts.
