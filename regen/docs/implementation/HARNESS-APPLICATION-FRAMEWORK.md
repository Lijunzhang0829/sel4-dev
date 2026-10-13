# Harness 应用层逻辑框架与整合边界

## 2026-10-11 固定客户端生命周期与基础设施计时

实验客户端不再依赖编辑器插件目录的存续。`codex_runtime` 按冻结 SHA-256 将
Codex 与伴随 tool host 固定到独立 runtime 目录；每次启动校验，已有对象不自动覆盖。
`codex_historical_paths` 仅显式授权旧启动路径作为同一 SHA 的历史别名，不授权新版本、
未知路径或不同摘要。旧 transcript 不改写，审计读取固定副本验证原启动证据。

文件丢失或摘要损坏抛出 `CodexRuntimeError`，经会话、异步检查任务和调度器保留类型；
不会作为普通候选证明错误回给模型。首次明确故障发出
`generation_runtime_integrity_failure`，在清理 worker 前关闭有效生成时钟并全局暂停，
保留已接受证明和独立草稿，不消耗 HTTP 重试额度。普通证明失败仍保留原有反馈与计时。

旧版本已经多计的时间由 `runtime_budget_correction` 只读分析源账本和真实工具回执，
在新建、尚未启动的完整 continuation 中追加一次原子校正 checkpoint。逐 lane 取首次
明确失败至干净暂停的区间，并保守保留实际成功的读取／检索 callback 执行时间；
若存在成功证明动作或不可信时钟则拒绝自动校正。worker 时间分别扣除，全局时间按
所有仍计费 lane 的并集重算，不能把单 worker 故障期间另一 worker 的正常工作也扣掉。
边界按短 checkpoint 内单调计费增量插值，记录墙钟残差，不宣称亚秒精确。
源事件、token、正常候选检查和失败前工作全部保留；校正绑定源尾事件、回执及内容摘要，
重复调用幂等，跨后代也禁止重叠退款。停机及恢复准备不开有效生成时钟。

## 2026-10-10 移除空回合终止条件

File Planner 不再因连续两个模型回合没有信息进展而抛出 `TheoryContinuationError`。删除对应异常类、空回合计数、两轮阈值、`idle_turn_limit` 策略字段及空回合专属警告；模型正常结束回合而 theory 未 seal 时，继续同一会话，直到 checker 确认完成，或实验预算／取消／独立故障保护生效。

这不是取消验收：模型最终回复仍不能代替 journal GREEN 或完整 theory seal。48 小时有效生成预算继续跨恢复累计；当前正式实验不启用 contract 无更新暂停阈值。HTTP 一次重试、协议错误、`SessionStalled` 的无信息等待／重复读取保护、原生检查超时及资源保护是独立机制，本次不改变。空回合期间正常模型工作的时间仍计入有效预算，不因移除终止条件而退款。

部署使用新的冻结应用和完整 workspace continuation：保留失败 theory 的最后提交及独立草稿，同时保留其他 theory 的最新证明、历史请求、累计时间和已消耗 HTTP 重试额度；停机与恢复准备不计入有效生成时间。不就地修改正在运行的冻结代码，也不从零重置预算。

## 2026-10-10 新实验 HTTP 一次重试策略

新运行可在冻结 `execution_policy` 中显式设置 `provider_http_retries: 1`；旧配置省略此字段时仍保留原来的网络暂停行为。此额度独立于 `session_stall_retries`，每个 theory 在该运行中最多使用一次，写入预算 ledger，重启协调器不会重新获赠。

已绑定线程／回合或请求 ID 的 HTTP 4xx/5xx、明确的连接／响应流断开与传输错误走同一恢复路径。首次故障结算该 lane 已发生的有效生成时间，关闭故障 owner、取消其在途检查并等待清理，然后退避 30 秒，在新 retry 目录从当前已接受状态及独立草稿恢复。不会重放可能有副作用的旧工具调用，也不退还故障前的有效时间或 token。另一个健康 lane 继续计时；故障、退避和恢复准备不计入故障 lane 的有效时间。

重试后再出现 HTTP/传输故障，或清理失败无法确认旧 owner 已退出，则保存并全局暂停，不创建第三个 owner。普通证明 RED、参数错误与未知协议错误不伪装成 HTTP；provider 内部宣称 `willRetry` 也不能绕过这个显式上限。

## 2026-10-10 历史召回与进展判定修复候选

本节是工作区候选代码，不替换现有实验的冻结应用，不自动恢复运行，也不修改旧 journal、accepted proof 或计费记录。

- `list_attempts` 提供当前已校验 history 中失败请求的分页目录，可按 entry/status 显式筛选；后续页携带 `snapshot_commit_id`，HEAD 改变则重新分页。目录不进行隐藏排序或推荐，不把“出现在失败 batch 中”说成“该条目证明失败”。
- `read_attempt` 与 `read_attempts` 默认 `view="summary"`：请求身份、提交条目、checker 明确报告的失败条目、带截断标记的诊断摘要、完整补读入口。`view="full"` 返回 hash-bound 原始 candidate/feedback，包括证明正文、上下文和完整诊断；完整对象保留内部原始字段名，避免词汇转换破坏内容哈希。摘要不是完整命题，更不是 GREEN 前提。
- 文档模式 `read_theory` 恢复 target/query/fact 导航、最近动作和 planner notes；旧检查点内嵌的完整历史工作集仅在输出时投影为摘要，不重写磁盘。相同响应内的重复观察使用显式路径引用；重启后仍允许补读必要证据，不携带跨会话的硬性“已读禁令”。
- `target_memory.last_containing_request` 只定位包含条目的最近 batch；`checker_reported_failed` 单独说明 checker 是否明确定位了它。未知定位保留未知，不从整批 RED 推断每个条目失败。
- 进展指纹与审计回执分离：历史读取的 HEAD 回执、任务随机编号等不再单独制造新信息；新上下文、分页、证明内容和诊断仍参与比较。真实 RED 检查仍算证明工作，缓存 RED、参数拒绝和重复符号查询不再仅凭动作名称重置进展。原有重复次数和等待阈值不变；首次读到新历史仍可计为信息，不宣称阻止所有低效探索。
- 重试收到有界的上次故障摘要和恢复入口提示，不注入目标专属数学建议。旧冻结 document registry 仍按明确的历史接口形状审计，未放宽对任意扩展接口的许可。

离线验证入口：`python -m regen.tools.validate_history_recall --run <paused-proof-run> --output <new-report.json>`。
它回放真实 KSP 日志、校验原始证据的完整补读、测试旧检查点投影与元数据去噪，并运行隔离回归；输出源码哈希和实验目录前后保护清单。报告中的字符减少不是实测 token 节省，也不证明新模型轨迹的 GREEN 数量会提高。验收不启动模型或 Isabelle，不替代后续受控生成试验。

## 2026-09-30 A 独立仓库规划入口补齐

`workspace_planner` 现已复用 `ModelIsolation`：network=none、仅允许 provider TLS、2 CPU、2 GiB、128 PID、只读根文件系统；不再使用 host network 或挂载客户端整个目录。原 `/input`、`/workspace`、`/api/planner.sock` 和 `/tool.py` 词汇保持不变，规划算法与 native preflight/提交规则不变。每次规划调用写入独立隔离凭证。

新增 `planner_owner_guard` 通过 PID/start-time 绑定 owner，启动握手通过后才允许模型运行。owner 异常消失时，守护只回收该次具名模型容器和经过路径、属主、权限、inode 校验的私有临时目录，包含认证副本；正常退出和超时也确认回收。Docker 无法回收时保留错误，不声称清理成功。

机器 A 的正确新实验输入为 `reports/regen/refine-first-run-001/dataset-v2`。`refine-planning-input-current` 仍是旧 v1 前提库，不能用于当前 Workspace Planner；先前 prepare-only 只保存输入与策略，未覆盖该版本门槛。v2 库含 77967 条已验证前提，287 个 contract 与旧输入完全一致，没有读取历史生成证明。不要就地修改旧输入身份。

真实独立 Planner 的新合成目标验收通过（含模型调用和 Isabelle 声明预检），完整 v2 输入的 WorkspaceHarness 初始化通过；正常、超时、真实运行后 SIGKILL 的容器及认证目录清理通过。详细双机回归及最终发布身份见准备报告。fresh-only 策略不变，不重试污染上下文；这不是 287 个目标的完整规划/证明实验，也不是双 Planner 长运行验收。

B 的回归揭示了粗粒度文件系统时钟下的缓存失效缺口：同长度内容改写可能保留相同 stat 身份。`batch_file_planner.query_identity` 现在对前提库、WAL 和可选向量文件同时记录流式内容 SHA-256，不仅依赖大小/mtime/ctime；新增固定 stat、改变内容的确定性负例。旧 observation cache 键可能不再命中，不能把旧缓存命中当作证明权威。对 A 的约 51 MB 前提库实测单次摘要约 31–37 ms；该开销换取真实内容绑定，不声称零开销。

## 2026-09-30 机器 B 实测交付更新

串行应用层已经通过机器 B 的真实模型小规模验收，不再只有合成协议测试。正式入口是 `python -m regen.tools.generic_serial_application` 和 `python -m regen.tools.vero_serial_application`。完整证据、启动命令和适用范围见 `reports/regen/serial-application-readiness-20260930/MACHINE-B.md`。尚未进行新一轮整仓 benchmark 或长时间性能对比。

- 通用 Codex 保留单 Agent 自主规划、编辑、检查、seal 和独立 repository integration；正式 CLI 实测通过。
- VERO 保留 B 的原生 `runner.run`、`CodexAgent`、证明槽、辅助声明／imports、提取器和独立 evaluator；仅绑定检查器工厂与模型进程传输。没有用通用 Agent 冒充 VERO，没有伪造 app-server provenance。
- VERO 使用 `VeroCheckerAdapter` 共用 `IsabelleChecker`、`NativeResources`、冷 process/build、容器配额与 owner guardian。`Vero_All` 成功后仍须独立 session build；build 失败不得发布成功。原生 VERO 的审计结果格式与通用 Codex 的 journal/seal 格式仍各自保留，并非强行改成同一方法控制流。
- VERO 模型容器只可写本次 proof-slot workspace 和独立认证 home；通过只读工具脚本及受限 Unix socket 请求 host checker/search/progress，不能访问应用源码、历史证明、heap 或 Docker socket。保留原生 shell 编辑方法。监督进程强杀后，真实容器自动退出测试通过。
- `REGEN_MODEL_CONFIG` 显式选择部署配置；client 的规范路径、SHA-256 和具体生成模型在初始化时冻结，不通过忽略 provenance 检查兼容机器差异。增量 `.meta.json`／`.audit.json` 必须经 `read_artifact` 解析，不能将 Merkle 索引误认为非网络调用。
- 这是 2026-09-30 发布时的状态：两个新串行 CLI 当时都只使用 fresh 输出目录。下文 2026-10-08 策略已为正式 VERO 入口增加同一 output 的显式 `--resume`；generic 入口及旧运行仍按各自冻结策略解释。认证使用独立临时副本，登录失效须更新凭据；入口不会把失效认证当作证明失败。

公平性边界：父环境、原生资源限制、Isabelle 内核及最终构建后端统一；两种方法的热检查使用方式、请求频率和方法级证据格式不强制相同。正式比较必须冻结相同输入、模型、预算与资源政策，不能把本次 tiny-goal 验收耗时当性能结论。

## 2026-09-30 串行发布候选更新

当前用户策略：默认仓库规划采用 fresh-only。基础设施异常或模型超时立即失败，
取消本任务的原生 preflight，废弃提交；新规划使用新输出目录，不恢复旧模型上下文。
正常验证返回错误后，仍允许同一次规划继续修改。成功规划保留最终分组与预检证据。
不再生成规划恢复 checkpoint 或周期快照。theory 生成的增量 journal／恢复不受影响。
旧 direct/iterative 工具中的历史 retry API 不是本次正式实验的默认入口。

本轮新增三条生产约束：

- seal v3 必须具有完整 native source/log、零退出码、原生依赖 receipt、精确源码、
  构建 ROOT/source 集合及 graph/proofs/order/imports/render-policy 绑定。
  普通 runner 与 File Planner finish 均接入；旧弱 seal 不自动升级为 v3。
- 热 worker、冷 process 与 build 都有 owner guardian；冷执行在 Python 被强杀后
  由容器独立租约到期终止。配额与线程数分开：
  默认每容器 4 CPU、16384 MiB、1024 PID、禁止额外 swap；由
  native-cpus/native-memory-mb/native-pids-limit 显式配置并冻结。
  这是单容器限制，不是整个主机或多个驻留 worker 的总内存上限。
- 真实 Codex app-server 需要 pinned 最小镜像，network=none，通过 Unix socket
  网关只允许 provider TLS；只挂载客户端二进制、空工作目录、独立认证 home 和网关。
  不挂载项目、历史证明、heap 或 Docker socket。合成协议测试不冒充 OS 隔离。
  此处描述 app-server 路径；VERO 新串行入口的受限 shell 容器见上节，不代表所有旧 provider 入口均已改造。

机器 B 使用本次同一份代码的新目录；不要合并 B 的旧应用层实现。
串行实验设置 max_parallel_theories=1、native_slots=1、resident_limit=1。
通用 Codex 通过 TheoryController/CheckExecutor/native/acceptance/integration 链；VERO 保留自己的槽与 evaluator，绑定到同一 native 资源及完整构建后端。
VERO 真实源码与入口现已验收；禁止通过伪造 Codex transcript 接入。
新方法必须保留可审计的自身 provider provenance，并共用数学验收权威。

验收与待办见 reports/regen/serial-application-readiness-20260930/README.md。
本地合成原生成功不等于两种模型方法或机器 B 已完成正式验收。

2026-09-30 补充：[增量实验日志](INCREMENTAL-LOGGING.md) 已接入显式新运行格式，
含保存 step 的恢复与性能对照；不自动改写旧实验。

2026-09-30 归档更新：用户已要求将旧生成实验退役为结果参考。
`reports/regen/proof-reference-results-20260930/` 保存最终分组与证明正文；
原 015 等运行的过程日志及私有 heap 已删除，不再可续跑。共享输入／环境保留。
历史仓库规划日志复查发现的损坏投影与完成记录丢失边界，见
`reports/regen/planning-log-audit-20260930/README.md`。当前 fresh-only 入口拒绝恢复
这些目录，不再以修复旧规划恢复路径作为实验准备前置。

状态：2026-09-29 工作区整合。本文描述当前实现，不把历史实验或合成测试当作
新版全仓验收。结论是：**关键已知故障已有修复及验证，具备受控验收基础；尚不能
宣布任意长运行、双真实 Planner 和整仓生成均已稳定完成。**

## 1. 总体结构

```text
冻结输入／新运行身份
  contracts + Semantic Base + imports + 模型／实现／预算／渲染策略
       │
       ▼
Repository Planner → 已验证 theory 分组、硬依赖及优先级
       │
       ▼
全局就绪调度器 ── 空槽立即补位，显式阶段政策除外
       ├─ Theory A：持续 File Planner → 单写入 Controller ─┐
       └─ Theory B：持续 File Planner → 单写入 Controller ─┤
                                                        ▼
                    持久任务执行器 → 独立 native 资源池
                           │               │
                           │               ├─ 声明／符号／合法事实查询
                           │               ├─ 热 batch worker：候选检查及审计
                           │               └─ 冷完整检查 → 依赖审计 → session build
                           ▼                                  │
                    完成收据／失败分类                          ▼
                    模型工具结果反馈                  acceptance guard + journal
                                                              │
                                                     GREEN / theory seal
                                                              │
                                                   发布并解锁依赖 theory
                                                              │
                                    全部所需 theory sealed → 仓库集成 gate
                                                              │
                                                 repository_verified=true
```

两条旁路必须独立于耗时证明：

- 协议接收与合法性检查：回调运行期间继续读取消息，但不并行执行同一 theory 的数学工具。
- 诊断与证据：记录 run/theory/task/请求身份及阶段时间；状态展示不能代替 journal 验收。

## 2. 模块职责与权威边界

| 层 | 当前主要入口 | 负责 | 不负责 |
| --- | --- | --- | --- |
| 准备／身份 | preparation、`proof_session_runner.init_run`、`repository_execution.init_run` | 固定 contracts、环境、计划、模型／实现绑定和新运行配置 | 不因初始化而接受证明 |
| 仓库规划 | `repository_planner`、`workspace_planner`、`workspace_preflight` | exact-once 分组、theory imports DAG、原生声明预检 | 不生成全仓 helper 证明 DAG、不把预检当 GREEN |
| 调度 | `parallel_file_planner.run_parallel_workspace` | 硬依赖就绪、会话槽、显式阶段／失败重试政策、最终 integration | 不用协作组隐式制造 batch barrier |
| 模型协议 | `CodexWorkspaceSession`、`workspace_transcript` | 事件校验、序列、进展时钟、取消、固定 transcript 前缀审计 | 不决定数学正确性 |
| 单 theory 应用服务 | `TheoryController`、`SharedProofWorkspace`、`DocumentStore` | 文档版本、固定声明保护、工具串行、任务 ownership、上下文可见性 | 不与另一 Planner 共享可变证明状态 |
| 检查任务 | `CheckExecutor`、`check_tasks`、`NativeResources` | 请求先持久化、任务终态、结果收据、资源槽、协作取消 | completed 不等于证明 GREEN 或提交成功 |
| Isabelle 适配 | `IsabelleChecker`、`BatchWorker`、`PrefixWorker` | 实际 prover 执行、前缀对齐、原生证据、cold complete/audit/build | 不允许 LLM 自报成功替代内核证据 |
| 接受／恢复 | `ProofCommits`、`repository_execution` | 绑定与哈希链验证、原子接受、已收费工作和有效前缀恢复 | 不通过改指针回退预算或撤销已接受成果 |
| 仓库集成 | `repository_integration` | exact-once 覆盖、精确 seal 源码／imports、独立全集 build | 不把单 theory finish 当全仓完成 |

当前默认模型接口是 document：编辑持久 `.thy`，从声明索引形成检查所需的兼容
graph/nodes 数据。旧图式 File Planner、node prover、GRA 仍用于旧入口与回放，
不能把旧 DAG 控制流描述为默认 document agent 每轮必须执行的步骤。

## 3. 当前 Isabelle 服务到底是什么

生产 batch 路径为：Python `BatchWorker` 继承 worker 生命周期管理，在容器中启动
`isabelle process` 加载冻结 heap，并用 `ML_file /worker/server.ML` 载入项目的
`batch_checker.ML`。Python/ML 通过带序号的 request/response 文件和 audit XML 交互，
以临时文件 rename 发布请求／响应。ML 使用 `Outer_Syntax`、`Toplevel` 和 Isabelle
原生 theorem/context 接口执行命令与取得证明状态。

这条路径是项目维护的私有批量协议，不应称为已经切换到 Isa-REPL、PISA 或官方
Isabelle server。本轮也没有完成另一种 server 的选型或移植。旧 stepwise/实验适配器
的存在不意味着默认 batch 请求走那些后端。

事务状态是：已接受 base → speculative candidate → 原生执行＋审计 → pending。
只有外层 journal 接受对应版本，worker 才能按该已提交前缀 promote；失败、取消或
前缀失配不能把 speculative 状态泄漏给下一项任务。重建从受信任前缀开始。
batch 审计绑定 worker/request/theory/imports/prefix/delta/fact names，并检查新公理、
oracle、skip_proof 等；不能仅凭字符串 GREEN 接受结果。

完整理论另走独立 `isabelle process` 检查及审计，再 `isabelle build`。最终 build
保留，不用热 worker 结果代替。独立仓库 integration 又使用精确 sealed 源码做全集构建。

## 4. 一次应用层请求的完整生命周期

1. 收到经过身份／schema 校验的工具请求，绑定 thread/turn/call 和持久 transcript 边界。
2. theory Controller 获得该 theory 的工具串行锁，读取正确版本与已提交 GREEN 前缀。
3. 对 edit/check/finish/observe/read 等操作执行各自约束；检索到的事实仍需满足
   冻结 imports 与本 theory 已提交前缀可见性，目录命中不等于原生可用。
4. 需要检查时，先持久化任务及其输入身份，再排队等待 native 槽；排队不占第二个
   同 theory 数学操作，也不应被误记为 prover 已在执行。
5. 执行后先保存结果收据。只有匹配当前输入版本、合法 provider provenance、
   native 证据和未取消的接受条件，才在 acceptance guard 下提交 journal。
6. 结果返回模型。默认工具等到本次操作终态才返回；executor 的后台线程不是
   File Planner 同时执行多个数学工具的许可。

`get_check_result`、`wait_checks`、`cancel_check` 等兼容控制接口不创建第二份证明，
重读收据不重新收费。observations 是非证明记忆，不能变成 GREEN 或证明进度。
旧图式路径的 GRA 提供方向／图修订而非证明，SPLIT 的 helper 成功不代表父目标完成。

## 5. 并发、超时、取消：三个不同问题

### 热 worker 观察与 contract 间隔（2026-10-06）

符号解析和声明预检优先在已有 batch worker 内执行 `observe`：仅复用进程，
从冻结 imports 创建临时 theory，不从 accepted/pending 状态创建，不发布到
`Thy_Info`，不修改 base/pending。声明预检仍使用 `oops`，其 GREEN 不是证明。
检查仍持有同一 native lease；不为保住热 worker 额外启动一个未计入资源池的容器。
worker 不存在或 imports 不匹配时保留原冷路径；传输故障丢弃 worker，不伪造成功。
该修改不改变完整 seal、candidate proof audit 或 Arch proof-context 布局。

contract 间隔的建议口径（尚未替换现有墙钟 watchdog）：

- 所有成本都记录，不能删除异常时间或只统计成功证明。每段保留 run/theory/attempt/task、
  开始/结束、阶段、分类理由、终态；持续时间使用 monotonic，跨进程保留 UTC。
- 同时报告原始墙钟和有效生成时钟。有效时间包括模型推理/正常等待、规划/查询、
  helper、失败候选、正常 tactic 超时、证明检查及接受提交；helper 不重置 contract 时钟。
- 环境/provider 初始化、worker 前缀恢复、seal/integration、确认的传输故障/重连、
  人工暂停单列，不计入有效生成。原生队列单列：恢复任务队列属于恢复，正常候选队列
  属于正常调度开销；依赖未就绪、全局无 runnable theory 也单列。
- 一次模型失败不能抹掉此前整次 attempt 的有效工作；仅有长等待不能直接认定网络故障。
  缺失 phase 边界的时段标 unknown，同时给出上下界，不能凭“无 contract”将其排除。
- 逐 theory 计算有效间隔；全局使用各 theory 有效区间的并集，不叠加并行秒数，也不能
  因一个 theory 在恢复而暂停另一个正常 theory 的全局计时。
- 仅 journal 接受新的或改变的 contract body 才重置更新时钟；同一提交的多个 contract
  算一次更新时间事件，另外记录数量。继承/回放同一证明不重置；revoke 单列。
  首次成功前的等待与最后一次成功后的未完成间隔分别报告，不混入完整间隔均值。
- 暂停/重启只排除对应停机与恢复时段，不自动清零上次 contract 更新以来的有效累计；
  若要人为重置观察窗口，应另存显式授权。计时未知区间保留上下界，并由独立基础设施
  watchdog 限制长期无响应，不能为了扣除异常而允许无限等待。
- 2026-10-05 的回溯统计将首个 ready 之前都视为初始化，因此是粗粒度估计；其中若
  已有真实模型证明规划，应在有更细 phase 证据时归还有效时间，不能机械照搬。


### 调度和资源

`max_parallel_theories=2` 表示最多两个活跃 theory agent，不是每次必须凑齐两个。
A 结束而 B 尚在运行时，只要 C 的硬依赖已 seal 且当前阶段允许，就立即用空槽启动 C。
`FIRST_COMPLETED` 驱动补位；用户显式指定的阶段政策可以要求等待一组完成，这与 worker
数量无关。仅有一个就绪 theory、资源排队或阶段限制时，并行度不足不自动代表 bug。

`native_slots` 限制实际 native 作业，`native_threads` 控制 Isabelle 内部线程，
`resident_limit` 限制驻留热 worker；它们不等于 Planner 数，也不是实际 RSS 的保证。
每个 theory/task 有独立输出和可变状态，合法可共享的是固定父环境与已发布成果。

同步文档读取也可能触发 provider session 初始化，因此 `_runtime` 的 `setup`
必须显式绑定同一 `CURRENT_RESOURCES`，不能只在异步 check task 中绑定。
初始化构建失败属于 `ProviderPreparationError`（基础设施失败），在当前控制器中
保留失败状态；重复读取不会重新构建。修复或改变预算后应使用新的、已授权的控制器／运行，
不能依赖模型反复读取来重试。候选证明 RED 不适用此失败闩锁。

### 超时分层

| 时钟／限制 | 约束内容 | 到期语义 |
| --- | --- | --- |
| provider 事件不活动／无新信息 | 模型会话外部等待与没有新工具信息／证明动作的停滞 | SessionStalled 等基础设施停机，不判定目标不可证 |
| 重复读限制 | 相同工具参数和结果持续重复；当前连续 8 次重复会拒绝 | 阻止只读空转，不能靠 sleep 刷新证明进展 |
| provider 冷构建 | `--provider-build-timeout`，parallel workspace 默认 1800 秒；只约束实际构建，不含槽位排队 | 基础设施失败，不延长 candidate、seal 或实验无进展时钟 |
| worker 就绪／prefix replay | 启动及累计回放已认证前缀 | 与单个 candidate 时间分开；区分准备失败 |
| 单命令／candidate | 实际原生命令及候选执行／审计 | 记录具体 timeout_scope，不无限等待 |
| complete／audit／build | 完整理验各原生阶段 | 每阶段独立受限，不以 LLM 无信息阈值截断可信回调 |
| 实验预算 | 请求、证明轮次、累计成本及配置允许的重试 | 不因基础设施恢复清零 |

受管构建使用显式 Isabelle 线程数、单 session job，以及独立的 Poly/ML heap 上限。
`--build-ml-heap-mb` 默认取容器内存的 75%（16 GiB 容器对应 12288 MiB），
为 Java、ML heap 以外内存及运行时开销保留余量。这不是不发生 OOM 的保证，
也不应将退出码 137 单独等同于 OOM；OOM 结论需要内核或容器证据。
任何新阈值都必须记录进新运行的启动配置，不能热改旧冻结应用。

600 或 900 秒都是具体配置值，不是全系统唯一超时。CLI call-timeout 默认仍可为
600；既有实验指定 900 不等于修改所有新运行默认值。可信回调内部等待计入 native/action
预算，不计作模型独立空转。无进展与网络无响应也不是等价结论，必须结合阶段和事件证据诊断。

sleep v2 仍校验 owner、唯一 ID、开始／完成配对及每次 0–60000 ms；移除错误的
每回合累计 120000 ms 上限。事件不会触发 harness 再 sleep 一遍，也不是有效证明进展。
旧无版本／v1 transcript 仍按旧规则验证，不能事后改标签使失败记录可 seal。

### 协议与取消

回调期间的 reader 只读取、校验、排队消息（有界队列），工具仍由 owner 线程串行执行，
保留 contextvars 和取消上下文。finish 校验请求时记录并 fsync 的 transcript 前缀，
不审计仍在增长文件的临时 EOF；最终闭合会话仍执行完整审计。

协议错误通过 on_abort 取消当前 run/theory 的任务。检查前、任务注册后等竞态有保护；
acceptance guard 决定取消与接受先后。已接受结果保留，取消后的未提交结果不可发布。
热 worker、冷检查与 build 有 owner lease／容器 guardian，但这不是整台主机崩溃恢复或任意 Python
回调可强制中断的保证。物理执行也不保证 exactly-once。

## 6. 本次整合：版本化完整理论策略

新增统一策略模块 `execution/checker_policy.py`：

| 策略 | 行为 |
| --- | --- |
| `isolated-v1`／字段缺省 | 保留原逐节点包装与旧源码字节；旧运行、旧 journal 不增加新字段 |
| `arch-reuse-v1` | 最终完整理论的合适连续 Arch 节点复用外层环境；节点仍有内层隔离；属性节点构成边界 |

策略仅影响 `check_complete`、`audit_complete_theory`、`complete_source` 三个最终入口。
声明 oops 壳、逐节点 `_source`、hot batch 的 append/prefix 协议不改变，以免破坏
现有 GREEN 前缀、缓存和恢复证据。build 使用同一策略生成的源码，integration 使用
同一策略精确重渲染，不接受“任意一种渲染匹配就算通过”。

关键语义修正：共享组的前置结果不能留成未命名 local 副本；须清除局部副本并恢复
全局已导出事实及索引，而且修改相关嵌套 target 才能在下一命令 reset 后保留。
这不是全局解释 Arch，也不是去掉所有 context/end。

策略由新运行 init 写入不可变运行身份，传给 checker；非旧策略同时进入 journal
binding 和 integration manifest。三个 continuation/fork 入口继承原策略，跨策略
continuation 明确拒绝。观察 checker 可继承配置，但不会据此优化声明或 batch 操作。
未知策略拒绝，不悄悄回落。隔离重放工具已改用正式 constructor 参数，不再临时替换 `_source`。

新运行可显式选择（占位路径，非本次执行命令）：

```bash
python3 -m regen.execution.proof_session_runner init \
  --dataset-root /path/to/accepted-dataset \
  --schedule /path/to/accepted-file-list.json \
  --run-dir /path/to/new-run --run-id new-run \
  --complete-context-policy arch-reuse-v1
```

默认仍为 isolated-v1，旧环境路径不能开启新策略。不能通过修改 015/run.json 或
添加 resume 临时参数切换。带历史 seal 的跨策略迁移需要另行设计和验证，不能假装
普通 fork 已完成这种迁移。当前整合没有恢复 015、创建新实验或调用生成模型。

## 7. 恢复、历史与应用层状态

权威是 acceptance journal 中的已验证链、输入身份和接受证据；progress、指针、
premise catalog 是可重建投影。`repository_execution.progress()` 会 replay 并写投影，
不是只读监控 API。检查运行状态优先读已有 task/event/progress 产物。

历史回放已有按环境／验证器身份隔离的有效前缀缓存、记录 fingerprint 检查及受控
冷回放。它不是持久数据库索引，也不能保证每次恢复 O(1)。缓存失效要重新验证，
不能为了快而跳过绑定。请求内容、proof 与 audit 大对象不应为每次查看进度重复拷贝。

任务重启：已完成收据可修复 result 落盘与状态更新之间的窗口；未知非终态任务标为
interrupted，不能盲目重发 LLM／重复执行外部动作。恢复新会话读取持久成果，不等于
恢复完整旧聊天。重试必须遵循冻结政策、保留实际成本且不重复接受；两次网络超时也
不能推断第一次一定没有执行。

需要同时展示：活跃 theories、各自当前阶段／最后有效事件、排队与 native 时间、
checked 和 sealed contracts、helpers、任务终态、已收费成本、seal／integration 状态。
只有全体所需 seal 加上匹配当前 manifest 的 integration 才是仓库完成。

## 8. 验收矩阵与还不能宣称的事情

| 能力 | 已有证据 | 边界 |
| --- | --- | --- |
| 协议 reader、sleep v2、取消／接受竞态、固定 transcript 前缀 | 专项协议、取消、文档及原生测试 | 多数协议生产者为合成，不代表真实网络已穷尽 |
| Arch 语义等价 | 80 项保存前缀指纹、132 项隔离／属性控制、261 项已构建 heap 对照 | 覆盖保存的 Scheduler_Queues 和合成控制，不是任意 ML 状态 |
| 完整 theory finish | 122 节点 complete/audit/build GREEN；122 依赖集合、18580 有序规则一致 | 没有发布新实验 seal；retained-premise 空输入限制仍在 |
| 性能 | 同轮三节点 ABBA 63.19→30.19 秒；历史全量 73.21→11.24 分钟 | 全量不是同轮 ABBA；不能推导双 Planner 吞吐率 |
| 本次应用接线 | 正式策略入口、兼容／拒绝／恢复测试；与已原生验证完整源码逐字对照 | 不是重新完成全仓模型实验或 native integration |

已有证据：`reports/regen/arch-context-reuse-20260929/README.md`、
`reports/regen/harness-protocol-repair-20260929/`；本次接线证据放在
`reports/regen/harness-application-integration-20260929/`。

进入正式长运行前仍需：

1. 若未来开展并行实验，在新的执行绑定中验证两个真实 Planner 的补位、隔离和重试；
   这不是当前机器 B 串行 Codex/VERO 比较的前置。
2. 两个独立 theory 完成 seal 后，通过严格的完整仓库 integration，并检查当前 manifest。
3. 真实长历史、网络中断、取消／崩溃、资源压力场景的端到端恢复和成本保全验收。

因此当前适合称为“关键修复已整合、已验证路径可受控使用”，不适合称为“所有
应用场景已妥善解决”。下一阶段是有边界的真实运行验收，而不是直接放开长期无人值守。

## Activity-supervised comparison policy (2026-10-01)

The serial comparison entries now use `--inactivity-seconds 7200`. There is no
per-target attempt budget, helper credit, fixed experiment wall budget, or default
outer iteration/turn cap. Transport and native operation timeouts remain explicit
infrastructure controls. Planning time, failed work, queries, native checks, seals,
and integration all belong to the observed experiment wall time and token cost.

A new contract/helper declaration/proof version or validation state refreshes the
activity deadline. An unchanged observation, query, provider heartbeat, timestamp
change, or return to an already seen version does not. Draft activity is not GREEN.
The monitor starts before planning; planning alone does not indefinitely postpone
an experiment that has never produced a contract/helper update. Termination retains
all artifacts. These launch wrappers still require fresh output directories; this
change does not advertise conversation recovery support.

Generic Codex keeps its autonomous document/controller workflow. VERO first uses
its own shell agent to write `PLAN.json`; the existing VERO `compile_plan` validates
exact contract coverage and acyclic imports before the original runner starts.
The controller does not choose grouping, proofs, helpers, scheduling, retry tactics,
or feedback interpretation. A complete GREEN generic theory must attempt `finish`;
a complete GREEN VERO module automatically attempts an independent native session
build. A failed seal remains a failed seal with diagnostics. Full repository
integration is required for complete success.

Use `regen.tools.prepare_vero_activity --source ORIGINAL_SRC --output NEW_SRC` to
create a separate VERO source copy. It changes only two budget guards to support
an external inactivity supervisor, records the patch identity, and fails if the
expected upstream implementation differs. The original source is retained.

Both entries persist hash-chained `events/` and `experiment-summary.json`:

- proof commits and current valid contract/helper counts, separately;
- provider cumulative token snapshots, deltas, available response receipts, and
  CLI fallback receipts without adding both accounting sources;
- optional declared work scopes, with shared and unattributed costs retained;
- check wall intervals, native evidence, seal attempts/outcomes, and termination;
- observed success-versus-token/time curves and 10/30/50/75/100-contract milestones.

Cached input is a subset of input, reasoning output is not added twice, and absent
usage is unknown rather than zero. Usage delivery can lag a proof event: observed
curve values are not an assertion of exact token consumption at that instant.
Exact causal contract/helper costs cannot be recovered from shared generation;
`cost_intervals` preserve the declared target/theory sets instead of duplicating a
shared call's cost for each target. VERO supports `tool.py scope ID ... --theory
THEORY [--theory THEORY ...] --phase helper`; multi-target or multi-theory scopes
are recorded as `shared`, while an empty scope remains `unattributed`. Labels grant
no proof authority. Rollout accounting is restricted to the current private home
and bound thread. No old experiment is exposed.

The cold VERO evaluator and incremental generic checker retain their respective
execution paths. Their timeout scopes and caching policies must be disclosed in
system comparisons; common resource limits do not make these execution strategies
identical. This policy measures complete systems by actual token and wall cost.

### 有效生成总预算：可恢复的状态门（2026-10-08 更新）

新实验统一采用 `global-effective-generation-budget-v1`：默认 **48 小时累计有效生成**，
不限制 token，不再以 contract/helper 更新重置预算，也不叠加旧的一小时无 GREEN
停止条件。已冻结的旧实验仍按原政策解释，缺少此时钟的历史运行必须先审计迁移，
不能直接从零继续计费。此前 VERO 的“10 亿 token 与 48 小时同时达到”政策已被替代。

实现为 `EffectiveTimeBudget`，并非一个可由模型任意切换的布尔参数：

| 可信应用层边界 | 时钟动作 |
| --- | --- |
| theory 开始正常生成 | 打开对应 worker 的门 |
| 模型推理、读取反馈、检索、编辑、helper、候选检查及普通失败 | 保持打开，沉默本身不能证明网络故障 |
| 初始化、声明预检、已接受前缀／heap／provider 准备、原生 seal/integration | 以具名句柄暂时排除；允许嵌套 |
| 准备期间确有模型工作 | 可信模型生命周期事件恢复该 worker 的计费；不能覆盖确认的故障恢复／规划排除 |
| 确认的断线、退避、故障恢复、人工暂停 | 关闭受影响的门，之前的正常工作不退款 |
| 排除结束／故障恢复完成 | 只结束对应句柄；其他排除仍生效 |
| 累计到限 | 记录 `termination: disposition=pause`，阻止后续证明发布，取消在途任务并保存 |

设每个 worker 的有效状态为 `open_i(t)`：
`effective_seconds = ∫ any(open_i(t)) dt`；另记
`worker_seconds = ∫ sum(open_i(t)) dt`。双 worker 的预算时钟按并集计算，
不会重复收费；单个 worker 的准备或暂停不影响另一正常 worker。
总运行墙钟、暂停间隔、准备／认证耗时与 token 仍保留，不能用有效时间替代全部实验成本。
模型、native 槽位及并行度必须在比较中披露；相同有效墙钟不等于相同算力投入。
当前 VERO 的冷候选 `_check` 内部 session／Arch 装载尚无独立可信边界，整次冷候选
检查保守计入；adapter 初始 heap 克隆、显式恢复、seal/integration 可单独排除。
这不等于三个方法已精确分离所有 prover 内部准备子阶段，不能估算退款或宣称这些成本完全同口径。
VERO 重试的恢复／构造／渲染使用已知应用边界排除；进入原 `Agent.run` 前开钟。
此后 CLI/provider 内部尚未细分的启动等待也保守计入，不凭未验证的协议事件免费排除整轮。

日志只新增哈希链中的状态转换和小型 `time_budget_checkpoint`：累计有效秒数、
worker 秒数、每 theory 秒数、预算与暂停原因。固定 contract 分组登记一次，新增 GREEN
和 seal 单独记事件；不把 proof body、完整聊天、token 历史复制进计时 checkpoint。
每秒检查阈值，稳定阶段默认每 30 秒写一次 checkpoint，切换／暂停时立即写。
原有 proof journal、持久检查任务和去重 usage ledger 继续作为各自权威，不新建一份证明系统。
`experiment_log summary` 显示最后落盘的 `generation_clock`，明确标为持久观测，
不能将旧的 OPEN 样本当作进程仍活着的证据。
计时落盘失败按失败关闭处理，但仍执行 worker／服务／锁清理；失败不能被描述为
“已经安全保存”。必须核对最近有效账本和证明收据后才能决定恢复，不能清零重开预算。

恢复须持有单写入锁，并校验原输入、模型／实现配置、分组和日志哈希链；恢复的门
一律先关闭，准备完成后才重新打开。累计时间、token、已接受证明、历史暂停点都保留。
异常退出后恢复工作区与证明成果，不承诺原 LLM 会话仍可恢复，不盲目重发未知工具调用。
强杀可能丢失最后尚未落盘的计时尾段；恢复报告 `accounting_complete=false`、未知尾段
为 `null`，不能将 30 秒间隔宣称为任意系统故障下的误差上界或把未知时间解释为零。
正常到限可能有 watchdog／取消的尾部开销，实际观察值不被裁剪为恰好 48 小时。
证明发布以持锁后的最终预算准入检查为线性化点；截止前已获准的原子落盘可在截止后结束，
不会回滚已接受证明。其开销保留，随后检查暂停；这不是承诺磁盘写入一定早于物理截止时刻。

轻量日志实测（本地临时目录，18 theories／287 contracts，双 worker 模拟 9000 秒，
300 次周期采样，合计 305 个 checkpoint）：plain ledger 307 个文件、实际占块
1,265,664 bytes，写入 1.20 秒；Merkle ledger 616 个文件、实际占块 2,523,136 bytes，
写入 2.34 秒。仅测新增计时日志，不含 proof／provider 日志，也不是实跑 2.5 小时或
对旧日志系统的整体性能对照；文件数、块分配开销和序列化 JSON 大小分别报告。

入口与控制参数：

- A：`proof_session_runner` 的 `parallel-workspace`（含 service manifest）接受
  `--effective-seconds 172800`；同一运行恢复须 `--resume-authorization TEXT`。
- 通用串行：`generic_serial_application --budget-mode effective-time --effective-seconds 172800`；
  恢复通过 `--continue-from OLD_ROOT` 和 `--resume-authorization TEXT`，输出为新目录。
  恢复链全程互斥，防止同时从同一累计预算分出多个活跃实验。旧 inactivity 模式仅显式兼容。
- VERO：`vero_serial_application --effective-seconds 172800`；使用同一 output 的
  显式 `--resume` 授权恢复。已接受规划仍冻结。
- 达到预算后继续：恢复时显式增加 `--extend-effective-seconds 86400`，即追加 24 小时，
  保留原 48 小时基线和 cutoff，最终比较把扩展段单列。不能改原始预算或清零累计值。

切换到新实验前仍须核对旧 `proof_idle_watch` 等外部服务的 run 绑定；代码更新不会自动
停止历史 watchdog。协议无响应、重复工具空转等故障保护保留，但不等同于旧的无 GREEN 额度。

本次验收包括确定性时钟／真实日志测试、实际子进程强杀恢复，以及模型/native doubles
驱动的入口暂停／恢复／扩期测试；不是新一轮真实模型或 Isabelle 长运行验收。
最终指定 23 个相关测试模块共 276 项：275 通过，1 项因本地缺少 prepared VERO
源码跳过，零失败；另通过 Python 编译检查。故障注入覆盖磁盘失写仍清理、发布截止
竞态及重试准备排除。未部署机器 B，也未启动／重启正式实验。此结论仅限相关回归，
不声称全仓测试通过；额外检查的旧 `test_generation_strategy` fixture 缺少既有
`target_contract_green()` 方法，需另行维护。

### VERO frozen-plan policy under the shared time-only budget

Fresh VERO experiments supersede the earlier 7200-second wall-activity policy
with `regen/config/vero-global-exploration-limits.json`. Planning runs before
proof generation and assigns every fixed contract to exactly one theory. Once
accepted, the theory split, contract allocation, imports and execution order are
hash-bound and cannot be revised during generation. The theories still share one
workspace; that storage choice does not make unsealed sibling facts legal premises.

The shared workspace has one cumulative budget: 48 hours (172,800 seconds) of
effective generation by default. Reaching it pauses unfinished generation and
preserves recovery state. Tokens are reported, not used as a stopping limit.
Missing or delayed provider usage remains unknown rather than zero.
There is no inactivity/no-progress threshold and no contract, helper, attempt,
iteration or per-theory limit. Authoritative contract GREEN remains correctness
evidence and a reported progress point; it does not reset or otherwise alter
the time allowance.

Planning, runtime/provider/theory initialization and preparation, explicit
pause/offline time, and only the actual native seal or integration check/build
call are excluded from effective generation. Model reasoning, retrieval, edits,
candidate checks and model-driven repair after a failed seal continue to count.

This effective-generation interval is not the experiment runtime. Runtime and
cost retain initialization, preparation, ordinary checks, native seal and final
integration; only recorded experiment pause or machine downtime is removed from
runtime. Thus initialization or a long seal does not consume the 48-hour effective
generation allowance, but its duration and cost remain visible in final system
evaluation. Model-driven seal diagnosis and repair resumes effective-generation
charging.

When every contract in a theory is GREEN, VERO automatically attempts a seal.
Native contract evidence and seal evidence are recorded separately: a seal build
failure cannot erase an already audited contract GREEN. The agent receives the
seal diagnostics, repairs proofs or helpers inside the same frozen theory and
retries automatically. Successful repository integration remains necessary for
overall completion.

The formal experiment entry is only `python -m regen.tools.vero_serial_application`
with `--expected-contracts 287`. It is release-bound to `--model gpt-6-astra
--reasoning medium`; those are also the CLI defaults and other values are rejected.
The launch binding records both fields, the exact inventory digest and the frozen
base time allowance, and rejects another contract count or limit policy. The older
resource-runtime entry, which still supports plan revision, is not an admissible
entry for this frozen-plan experiment.

A fresh formal run requires a new output directory. `--resume` instead requires
that same existing output and validates the original benchmark, client, VERO
sources, formal model controls, accepted plan, planned benchmark, allocation,
imports and runner configuration before opening a new operation segment. It skips
planning and initial rendering, restores the persisted effective-time state and invokes
the original VERO runner with `resume=True`. Recovery preserves the shared
workspace, native evidence, GREEN identities, cumulative usage and effective
generation, but uses a new private HOME and model thread; it does not claim to restore an evaporated
Codex conversation. Agent/provider process failures are handed back only through
the original runner's durable iteration recovery or an already evaluated
`agent_error` checkpoint. Unknown tool calls are never replayed by the wrapper.
The formal watchdog polls every second and checkpoints the compact clock every
30 seconds and on transitions. An unclean shutdown's unobserved tail is explicitly
unknown; checkpoint cadence is not a guarantee when the watchdog or storage hangs.

### A/B prompt and tool parity correction (2026-10-01)

`proof_tool_help` now belongs to the shared document-mode registry used by both
TheoryController and the generic repository agent. It returns the same schemas,
examples, helper markers and observation contract without opening a theory or
starting Isabelle. The generic registry adds only repository planning, experiment
status/scope and integration operations. The exact retired generic registry remains
readable for evidence replay; new sessions use the complete current registry.

The generic agent uses a neutral task prompt and mechanical planning schema help,
not the standalone architecture planner's grouping suggestions or JSON-only output
instruction. The cumulative effective allowance in new prompts comes from launch
configuration; legacy inactivity prompts retain their own frozen configuration.
This changes the generic adapter, not the other method's planning strategy.

Live status selects the latest cumulative usage snapshot for each thread, with
response receipts used only when a thread has no snapshot. These sources are never
added for the same thread. Unknown usage remains unknown. The tool description
states that notes persist in this run and does not advertise unsupported resume.
The generic CLI requires explicit `--model` and `--reasoning`; the requested
experiment uses `--model gpt-6-astra --reasoning medium`.

### Codex logging parity (2026-10-01)

The generic entry enables Machine A's `regen-merkle-json-v1` storage at the run
root before planning. Planning query artifacts and experiment ledger events use
immutable content-addressed objects; the hash-chained ledger reads both legacy
plain JSON and this common storage format. Nested proof workspaces retain their
own storage roots. Raw app-server JSONL stays lossless and append-only because
proof audit receipts address byte offsets in that transcript.

Usage snapshots carry the original notification's transcript path, byte offset
and raw-message digest when available. Final cumulative counters and available
rollout receipts are drained before the private home is removed, including when
the session fails. The final event follows resource cleanup and records both run
and cleanup failures. Tool outcomes record exceptions and wall duration; seal
returns are analysis events, never a replacement for native seal evidence.
Proof events link to their source journal artifacts. Summary target rows retain
failed targets and mark removed helpers and plan-invalidated results explicitly.

Rebuild statistics with `python -m regen.tools.experiment_log summary RUN_ROOT`.
This verifies the event hash chain and resolves Merkle objects. Use `show PATH`
for an individual artifact and `verify-storage ROOT` for storage integrity.
The summary exposes current observed targets, check and seal events, shared cost
intervals, token/time curves and milestones. It does not fabricate an exclusive
per-target cost for shared generation, missing usage, or a proof's exact token
timestamp between delayed usage notifications. Original provider counters and
native proof evidence remain the authoritative sources; summary files are derived.

### Generic native timeout separation (2026-10-01)

The generic CLI freezes independent `--preflight-seconds 900`,
`--check-timeout 900` and `--candidate-timeout 120`, matching the A workspace
planner and document controller defaults. Repository integration uses the full
check timeout. The previous entry incorrectly bound all three to 120 seconds;
the affected first Astra run is retained as an infrastructure-affected run, not
a fair proof-generation result. Corrections require a fresh run binding.

### Declaration preparation and asynchronous terminal handling (2026-10-01)

Document declaration checks use `arch-declarations-reuse-v1`: consecutive exact
Arch interpretation contexts share an outer interpretation, while each declaration
has its own anonymous inner context and ends in `oops`. Other contexts are barriers.
No theorem is established by preparation. This avoids reinterpreting the same Arch
locale hundreds of times when a generic agent chooses a large theory. Legacy graph
rendering and proof/complete-theory rendering policies are unaffected. Actual source
hashes bind declaration-cache entries and native evidence.

New workspace sessions mark pending-terminal policy version 2. A successful terminal
for the current turn received while a callback is executing is retained until every
already-received callback has returned a real result. No callback is replayed; no
new turn starts with pending tool results. Cross-turn terminals, unsuccessful or
duplicate terminals, unfinished sleeps and new requests after a terminal remain
errors. Audit preserves raw event ordering and requires all results before treating
the session as complete. Old unmarked transcripts retain their strict historical
policy and are not retrospectively reinterpreted.

Explicit generic continuation uses `--continue-from` with a new output directory.
It requires a terminal source ledger, unchanged dataset/model/resource policy and
an accepted plan matching the source pointer. Existing proof journals and seals
are verified through `fork_workspace_continuation`; the source remains untouched.
Logical ledger events retain their hashes, experiment identity and cumulative
usage. The new provider thread has independent counters; no pending call is replayed
and the vanished ephemeral model thread is not claimed to be restored. Prior plan
references remain at their original validated paths and must remain available.
Running-time curves exclude finished-to-resumed downtime, while wall-time curves
retain it. A fresh inactivity deadline retains historical content identities so
old versions cannot indefinitely refresh activity. Continuation preparation time
and the infrastructure intervention are recorded separately from model execution.

User policy update (2026-10-04): generic repository allocation is locked after
initial acceptance. The adapter rejects begin_revision, validate, preflight and
submit after acceptance; shared Isabelle interfaces and proof registries remain
unchanged. Theory-local proof/helper exploration remains autonomous. The launch
records locked-after-acceptance-v1. --restore-before-revision selects the preceding
accepted epoch, checks its observed journal commits, and uses an explicit hashed
allowlist of pre-cutoff drafts and batch artifacts. Historical costs and original
branches remain intact; abandoned post-cutoff notes are hidden from the new agent.
A plan_restored event resets active proof authority before verified inherited
commits populate it. This policy change is an intervention, not a pristine rerun.

### Opt-in AND/OR helper selection (2026-10-09)

`parallel-workspace --generation-strategy and-or-helper-selection-v1` enables
advisory local task selection in the ordinary document File Planner. The model
first saves helper declarations, then submits `proof_action/select_helpers` with
the current `source_sha256` and a `contract-rooted-and-or-v1` problem. Its root
must be a fixed local contract; goal IDs and roles must match saved declarations.
Routes contain only IDs, proposed AND requirements and finite model-estimated
costs. The existing exact AND/OR scheduler chooses among OR alternatives; the
adapter bounds input to 128 goals, 256 routes and 512 frontier plans.

The selection is advisory, not an enforced proof-source filter. Only unchanged
journal-accepted prefixes supply certified goals. Draft proofs never do. Each
read/action recomputes advice from current accepted state; after requirements
are accepted it selects the parent for normal proof checking, never marks the
parent GREEN automatically. No native parent probe, automatic premise retrieval,
budget reset or automatic inference of mathematical dependencies is introduced.
RED may motivate a model-authored route revision; automatic RED-based route
penalties are not implemented in this adapter.

`document/helper-selection.json` retains the bounded problem and its package,
declaration and content bindings. Original tool proposals retain normal audited
action provenance. The file is a recoverable search hint, not proof authority;
declaration edits invalidate advice, while proof-only edits trigger fresh
certification checks. The default baseline and dedicated strict A/B supervisor
remain separate and unchanged. Historical benchmark runs are not upgraded in place.
