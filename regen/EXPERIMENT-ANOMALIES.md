# 实验异常状态台账

**旧Refine对照批次退役（2026-10-27）：** VERO已更新，用户要求004/007/009整批重做。剩余日志与heap已清理，原48小时active-wall参考004不再用于新版结果比较；已保留历史计数/token/评估摘要，未启动重跑。见 [退役清单](../reports/regen/machine-b-48h-audit-20261027-001/refine-batch-retirement.json)。

**机器B保留集更新（2026-10-27）：** 44条可识别运行/验收记录中，没有确认满足严格48小时有效生成时间的实验。用户明确保留 `vero-sol-medium-active-wall-48h-004` 作为独立48小时active-wall参考；其他43个运行负载已清理，仅留排除摘要和VOID标记。旧远程日志路径不能继续作为可读取原始证据；冻结fixture与本地已抽取证据不变。见 [筛选与执行报告](../reports/regen/machine-b-48h-audit-20261027-001/REPORT.md)。

**2026-10-27清理更新：** 用户已明确要求移除机器A最近CRefine运行的原始日志及heap；原始transcript/native日志引用可能已不可读取。本文和独立问题摘要、fixture、验收/预算记录保留，不能再声称能从原路径完整回放历史。清单见 [cleanup-20261027.json](../reports/regen/two-week-storage-audit-20261027-001/cleanup-20261027.json)。

**最后核对：2026-10-27。范围：当前repo-level证明生成项目可查的历史A/B实验、Codex/VERO适配、规划和原生验证。** 本次只提取报告及现有事故集，不运行模型或native，不改变实验与预算。这里的“全部”指已留存、已能核对的记录；已删除/未观测的历史不补造。

本文件是持续维护入口。旧报告可以退役，但删除唯一证据前应将最小输入、预期不变式和来源固定到fixtures。状态写“历史修复”不代表当前checkout、机器A和机器B均已部署并复验。

## 使用与更新约定

- 优先判断：未启动 → 初始化/恢复 → 模型协议 → 工具/原生 → 验收/seal → 调度/计时，记录停在哪一层。
- 区分 crash、infrastructure_pause、framework_blocked、search_stalled、proof_red、budget_cutoff、operator_pause、measurement_invalid；返回码0、token增长、文件mtime和无新GREEN不能单独代替这些判据。
- 每次新增事件保留 run/machine、冻结应用与环境摘要、发生阶段、准确step/request、开始/最后有效事件/结束时间、受影响目标与现有GREEN、可观测症状、根因确定程度、恢复边界、测试证据。未知字段明确写未知。
- 同一故障多次重试不等于多个根因；worker耗时并行重叠不相加为全局墙钟；normal RED和synthetic故障不计作新的benchmark成功/失败比例。
- `已修复`要求准确实现身份与反例/对照；`已部署`要求实际运行时载入身份；`已验证`注明离线/合成native/历史native/真实模型闭环，不能互相替代。

## 快速对照：终止与停滞机制

以下40项是机制索引，不是40次故障；同一真实事件可涉及多行。实际源码位置和测试身份见后面的44项来源对照。

| ID | 阶段 | 观测及根因边界 | 状态/对照要求 | 来源 |
|---|---|---|---|---|
| RUN-001 | 进程/内存 | OOM kill：历史日志反复展开、缓存容不下活动历史、并发冷解析及continuation全历史常驻，协调器被杀。 | 旧修复有只读回放；不保证当前全负载内存上界 | [README.md](../reports/regen/replay-repair-20260929/README.md) |
| RUN-002 | 磁盘/存储 | ENOSPC：native诊断在log/result/response多份复制，heap与日志叠加；写入/恢复失败并留下未结算尾部。 | 历史归档及限界响应已实施；日志增长仍是风险 | [repair-manifest.json](../reports/regen/vero-rpc-repair-20261026-001/repair-manifest.json) |
| RUN-003 | 原生/内存 | Poly/ML栈增长或资源耗尽中断候选；不能算作命题反例或普通未闭合目标。 | 历史事件；当前特定候选需精确环境复验 | [HEARTBEAT-SUMMARY.md](../reports/regen/kernel-object-access-autonomous-011/HEARTBEAT-SUMMARY.md) |
| RUN-004 | provider/认证 | TLS隔离白名单缺少认证域、refresh token已使用/撤销等，使provider启动失败；同凭证重试不能修复。 | 旧隔离配置已修；无效凭证需重新登录，不保存凭证正文 | [MACHINE-B.md](../reports/regen/serial-application-readiness-20260930/MACHINE-B.md) |
| RUN-005 | provider/可用性 | ProviderOverloaded/serverOverloaded、usage limit导致终止或暂停，不能计作证明失败。 | 存在旧续跑/退避记录；当前额度和服务状态未检查 | [MACHINE-B.md](../reports/regen/serial-application-readiness-20260930/MACHINE-B.md) |
| RUN-006 | provider/传输 | TLS/SSE/websocket EOF或900秒无完整响应；两个worker同时静默也不能直接证明主机断网。 | 历史一次重发成功；底层网络/远端/client根因仍可能未知 | [REPORT.md](../reports/regen/crefine-pause-audit-20261024-001/REPORT.md) |
| RUN-007 | 计时/协议 | SessionStalled关闭时有未闭合agentMessage/reasoning item，升级为effective_clock_evidence_error/generation_clock_untrusted，暂停整个并行预算。 | 有修复与重发记录；不能把未观测尾部自动记零或退款 | [REPORT.md](../reports/regen/crefine-pause-audit-20261024-001/REPORT.md) |
| RUN-008 | 协议/回调 | native工具回调阻塞协议reader；事件延迟、pending tool配对和finish前缀竞态导致会话异常终止。 | 旧独立reader、取消桥接和完整前缀修复已验证 | [README.md](../reports/regen/harness-protocol-repair-20260929/README.md) |
| RUN-009 | 协议/sleep | 旧规则把四次合法40秒sleep累计后拒绝，或sleep生命周期审计错误导致协议退出。 | 旧sleep policy v2修复；sleep不等于信息进展 | [README.md](../reports/regen/harness-protocol-repair-20260929/README.md) |
| RUN-010 | 客户端/工具 | app-server/code_mode_host禁用、动态工具接线/参数错误，导致零工具回调就结束或工具拒绝。 | 历史接线修复；退出码0不表示有证明或seal | [HARNESS-IMPROVEMENT-TODO.md](../regen/docs/implementation/HARNESS-IMPROVEMENT-TODO.md) |
| RUN-011 | worker/启动 | incremental batch worker waiting for ready超时，已有GREEN保留但theory停在infrastructure_error/unsealed。 | 故障症状确认；不能从摘要判定CPU、IO或native哪层卡住 | [README.md](../reports/regen/crefine-owner-blind-sol-xhigh-20261025-001/README.md) |
| RUN-012 | worker/预算 | 累计prefix startup watchdog跨多个请求耗尽，重复尝试同一个已验收前缀；不是每次候选都真的用了900秒。 | 旧动态前缀策略已有调整；按具体绑定核对 | [README.md](../reports/regen/native-prefix-recovery-20260928/README.md) |
| RUN-013 | 原生/耗时 | 每节点重新Arch interpretation，长前缀反复激活locale；完整seal在总预算内超时。 | 已定位并有arch-reuse等价证据；非全仓固定加速保证 | [README.md](../reports/regen/arch-context-diagnosis-20260929/README.md) |
| RUN-014 | 原生/超时 | 候选、依赖加载、完整检查或审计超时；timeout_scope必须分开，排队与执行不能混记。 | 保护性终止；不代表命题不可证，不能无证据无限加时 | [README.md](../reports/regen/scheduler-finish-replay-20260929/README.md) |
| RUN-015 | 审计/事实 | 证明或完整检查GREEN，后置审计按global短名查locale导出事实失败，或Audit target has no theorems，阻止seal。 | Codex B有locale v4；A窗口与后续native修复需分别标识版本 | [REPORT.md](../reports/regen/crefine-green-gap-audit-20261027-001/REPORT.md) |
| RUN-016 | 审计/兼容 | 新审计renderer与旧v3 sealed源码身份不匹配，恢复准备安全停止。 | 历史v3精确renderer兼容已有修复；不得通过忽略摘要绕过 | [REPORT.md](../reports/regen/crefine-owner-blind-sol-xhigh-repair-20261027-001/REPORT.md) |
| RUN-017 | 证据/撤销 | 未使用helper被修改/删除后整段prefix hash变化，过度撤销不受影响的GREEN。 | 旧独立prefix revalidation v3修复；真正依赖变化仍须撤销 | [audit-summary.json](../reports/regen/vero-green-regression-audit-20261025-001/audit-summary.json) |
| RUN-018 | 证据/边界 | 后续草稿RED使整份报告rc!=0，早期合法prefix不能重新验收；失败import又对各contract重复冷检。 | 已有独立prefix与精确失败import复用；失败日志不能直接推广GREEN | [native-smoke-report.json](../reports/regen/vero-prefix-revalidation-repair-20261025-001/native-smoke-report.json) |
| RUN-019 | 证据/真实失效 | 被导入helper真的失败，或iteration的artifact改变，旧GREEN/report不再验证当前上下文。 | 正确拒绝；需恢复合法源码后重新native验收，不能复制旧report | [audit-summary.json](../reports/regen/vero-green-regression-audit-20261025-001/audit-summary.json) |
| RUN-020 | RPC/帧 | 单次readline(16MiB)截断巨大JSON响应，json.loads失败、模型读不到诊断，错误沿依赖传播。 | 历史完整帧、预览限界与按字节分页已修；需同时验证client/server | [repair-manifest.json](../reports/regen/vero-rpc-repair-20261026-001/repair-manifest.json) |
| RUN-021 | 运行时/漂移 | server已更新但冻结PYTHONPATH仍加载旧transport及邻接client，并将旧client挂入模型容器。 | 历史env override/显式迁移已修；普通progress成功不足以验收新RPC | [runtime-repair-manifest.json](../reports/regen/dual-step-resume-validation-20261027-001/runtime-repair-manifest.json) |
| RUN-022 | 恢复/性能 | 共享Merkle DAG反复解码/重编码、全历史语义验证、多次扫描和逐小文件sync，使恢复长时间没有模型工作。 | copy专项修复存在；同应用普通continuation优化不能假定已全部部署 | [codex-recovery-cost-audit.json](../reports/regen/dual-step-resume-validation-20261027-001/codex-recovery-cost-audit.json) |
| RUN-023 | 持久化/损坏 | state.json损坏或完成事件丢失；缺失投影可重建不等于内容损坏也可恢复，不能静默重发已完成请求。 | 旧审计记录的风险，当前状态需重新验证 | [README.md](../reports/regen/planning-log-audit-20260930/README.md) |
| RUN-024 | 计时/退出 | 崩溃没有close checkpoint，最后落盘至退出之间用时未知；恢复若误计停机或反复退款，会污染预算。 | 保留未知尾部、区间并集和幂等冲正；冲正需要明确授权 | [credit-receipt.json](../reports/regen/vero-budget-credit-20261026-001/credit-receipt.json) |
| RUN-025 | 模型输出/异常分支 | proof_body=null触发AttributeError；被拒SPLIT的decision误用于执行/撤销；异常分支使runner崩溃。 | 旧空值/事务类型修复已记录；仍需故障分支回归 | [REPORT.md](../reports/regen/historical-run-audit-001/REPORT.md) |
| RUN-026 | 查询/预算 | 查询与proof共用候选额度，25次合法observation而0个实际候选；第5次查询还误报空proof，最后预算耗尽。 | 旧查询/候选配额分离；不能把25次响应当25次Isabelle失败 | [RUN-REPORT.md](../reports/regen/kernel-object-access-autonomous-006/RUN-REPORT.md) |
| RUN-027 | 查询/状态 | 新一页查询清除上一页完整事实/诊断，模型反复read/query；错误scope或漏传premise_database又提前终止round。 | 旧观察工作集与传参有修复；当前信息累积需以输入验证 | [REPORT.md](../reports/regen/recall-context-repair-001/REPORT.md) |
| RUN-028 | 模型输入/体积 | 原诊断与best/latest重复嵌入，prompt约129万字符触发input_too_large；存储格式直接当模型输入。 | 后续投影/主动观察改造；旧10k硬上限已退役 | [INPUT-COMPRESSION-REVIEW.md](../reports/regen/kernel-object-access-autonomous-003/INPUT-COMPRESSION-REVIEW.md) |
| RUN-029 | GRA/误判 | 当前fingerprint混入历史checked_history，与自己匹配，错误判定重复路线并提前ABANDON。 | 旧改为比较本轮attempts；保留历史查询能力但不充当本轮停滞证据 | [GETENDPOINT-ANALYSIS.md](../reports/regen/kernel-object-access-autonomous-008/GETENDPOINT-ANALYSIS.md) |
| RUN-030 | 规划/反馈 | 评审自消费引用、revision decisions缺失/非法、workspace路径无一致重试/恢复合同，规划一次性失败。 | 部分schema反馈已修；F1/F4等仅有历史审计，不能统一写fixed | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| RUN-031 | 规划/完整性 | 损坏NDJSON仍可能通过provider审计；修订后的最终plan未重新消费评审。前者是审计缺口，后者是质量边界而非已证实挂死。 | 旧审计发现/设计限制，尚无本次当前实现复验 | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| RUN-032 | 规划/依赖 | 小接口与困难目标绑定，消费者必须等整theory seal；早期冻结imports使稍后provider成果无法进入下游。 | 有规划消费评审；不能仅靠静态DAG证明实际阻塞消除 | [REPORT.md](../reports/regen/theory-priority-audit-001/REPORT.md) |
| RUN-033 | 调度/错误继续 | 4/4已GREEN但trusted seal审计失败；模型明确报告blocked，调度器仍四次Continue，持续无效修复。 | 历史窗口确认；当前状态分类/让出与修复需独立验收 | [GAP-2.md](../reports/regen/crefine-green-gap-audit-20261027-001/GAP-2.md) |
| RUN-034 | 调度/观察 | effective-budget模式progress_timeout=0，信息重复读取检测一起关闭；持续RED或慢query可长期不触发信息停滞提示。 | 策略风险，不自动认定bug；不能据此擅自加no-GREEN硬截止 | [REPORT.md](../reports/regen/crefine-green-gap-audit-20261027-001/REPORT.md) |
| RUN-035 | 检索/证明搜索 | 慢query/observe、错误fact/method、宽展开、同类残余反复；候选变了仍可能没有有效策略推进。 | 混合正常探索与工具成本；源码hash变化或token增长都不证明数学进展 | [GAP-3.md](../reports/regen/crefine-green-gap-audit-20261027-001/GAP-3.md) |
| RUN-036 | helper/分解 | 父目标复制成helper、叶子过强、风险probe反复超时，probe成功被当准入死门槛，或一直未进入显式分解。 | reviewed/guided/phased各版本不同；短probe失败不等于正式证明不可行 | [REPORT.md](../reports/regen/switch-phased-astra-20261016-001/REPORT.md) |
| RUN-037 | 绑定/清理 | workspace model differs from frozen File Planner model在调用前失败；清理又读不存在的service-launch.json，批次退出，后续案例未启动。 | 旧分层首批失效；后续新身份003有终点，不补造旧结果 | [REPORT.md](../reports/regen/sketch-guided-stratified-20261014-001/REPORT.md) |
| RUN-038 | 测量/无效 | 两臂时钟重叠/排除时间泄漏、measurement_invalid、磁盘预留提前中断，无法形成有效AB。 | 实验无效而非证明RED；不把缺失arm或截尾记0耗时 | [validation.json](../reports/regen/step-optimization-validation-20261006/validation.json) |
| RUN-039 | 测量/截尾 | 固定预算耗尽或operator_pause、usage-limit后停止；仍余目标或helper不构成基础设施崩溃。 | 正常截止/人为停止与异常分列；终点后GREEN不能回补截止前成绩 | [REPORT.md](../reports/regen/sketch-guided-stratified-astra-20261015-003/REPORT.md) |
| RUN-040 | 启动/授权 | 自动审批拒绝外部模型调用，只有初始化，无实际proof尝试；属于执行未启动。 | 历史授权阻断；不据此发明当前重复授权需求 | [PENDING-RUN.md](../reports/regen/kernel-object-access-autonomous-010/PENDING-RUN.md) |

## 必须单列的正常失败与错误归因

普通open goals、证明方法不适用、未定义fact、语法/类型或编辑拒绝，只有被trusted harness错误处理、没有完整反馈或反复框架阻塞时，才升级为基础设施异常。B的batch628执行完成仍余5目标、无timeout，属于真实proof RED。失败import和改变artifact后拒绝旧证书也是正确行为，不应为了曲线好看强制恢复GREEN。

A的三个长GREEN空档共662次失败事件和108次慢检索观察；locale审计解析错误19次、native超时73次、无完整反馈3次。它们不是770个独立bug，也不代表整个系统连续挂死。Capability的4次blocked后自动Continue是框架循环；其他窗口包含真实候选推进。来源：[问题次数台账](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md)。

## CRefine既有44项来源与覆盖逐项对照

保留全部原ID以便更新和去重。这里还列有输入公平性、结果解释与证据缺失项：这些不一定导致进程终止，不把它们伪装成中断根因。覆盖等级：offline_regression=离线可执行边界；documentary_regression=历史证据关系断言；historical_statistics_only=历史统计重算；documentary_only=只有记录。测试存在不等于本轮运行通过。

| 原ID / 类别 | 问题与记录的根因 | 历史状态 | 覆盖 / 入口 | 首要来源 |
|---|---|---|---|
| B-OWNER-VISIBILITY / 比较局限 | 旧B输入暴露原contract到theory映射：旧Codex source-theory-visible-v1包含309条owner/25个原theory；VERO也有原source IDs/modules。目标语义相同不等于模型输入信息相同。 | 20261025新实验已owner-blind对齐；旧运行按授权删除 | offline_regression；state-owner-visibility | [machine-b-comparison.json](../reports/regen/crefine-owner-blind-input-20261024-001/machine-b-comparison.json) |
| B-DELETED-OLD-RUNS / 比较局限 | 旧xhigh运行、日志和发布已删除，无法原目录完整回放：用户批准删除三旧运行和两旧发布；删除收据仅能证明范围，不能替代原始step。 | known irrecoverable evidence gap | documentary_only；无可执行case | [full-directory-cleanup-receipt.json](../reports/regen/crefine-owner-blind-machine-b-20261025-001/full-directory-cleanup-receipt.json) |
| B-CODEX-LOCALE-AUDIT / 框架bug | Codex原生审计对locale导出事实解析错误：生成事实在locale中导出为限定名；旧后置审计按短名/全局context查找，合法候选无法取得审计证据。修复仅解析新生成native facts，拒绝歧义、父事实冒充和sorry。 | fixed in locale repair v4; native26 + independent6 checks; B81 tests | offline_regression；codex-locale-fact-audit | [recovery-receipt.json](../reports/regen/locale-audit-stability-20261025-001/recovery-receipt.json) |
| B-CODEX-RECOVERY-MERKLE-COPY / 性能 | Codex恢复对共享Merkle日志展开重编码，初始化过慢：旧copy对共享对象解码/重新编码；恢复应保留Merkle identity直接复制可达对象，并批量校验/同步而不弱化正常日志fsync。 | copy repair deployed v4; initial attempts001/002/003 stopped before provider | offline_regression；codex-merkle-recovery-copy | [recovery-receipt.json](../reports/regen/locale-audit-stability-20261025-001/recovery-receipt.json) |
| B-VERO-UNUSED-HELPER-REVOCATION / 框架bug | 未使用helper修改导致已证明contracts过度撤销：收据绑定整个proof前置文本；删除未被证明依赖使用的11个helper改变prefix hash。先前OPEN slot修复未覆盖辅助声明修改。独立native回放证明受影响正文和实际依赖均未变。 | fixed independent prefix revalidation v3; authorized workspace restored from12366 and rechecked | offline_regression；state-prefix-revocation | [audit-summary.json](../reports/regen/vero-green-regression-audit-20261025-001/audit-summary.json) |
| B-VERO-LATER-RED-BLOCKS-REACCEPTANCE / 框架bug | 整份草稿RED阻止独立已证明前缀重新验收：native rc!=0会丢弃所有clean markers；后续c0088结束错误使不受影响的早期contracts不能重新accept。不能直接信任timeout/failed run的clean文字。 | fixed by independently checked prefix boundaries | offline_regression；state-prefix-revocation | [audit-summary.json](../reports/regen/vero-green-regression-audit-20261025-001/audit-summary.json) |
| B-VERO-FAILED-IMPORT-RECHECK / 性能 | 失败import对每个contract重复原生重验：同一失效import阻塞多个前缀；每contract重复native检查造成额外恢复/生成耗时。只允许精确源/日志/manifest绑定的负结果复用。 | fixed v3 failed-import reuse; changed imports force fresh check | offline_regression；state-prefix-revocation | [recovery-receipt-v3.json](../reports/regen/vero-prefix-revalidation-repair-20261025-001/recovery-receipt-v3.json) |
| B-VERO-REAL-IMPORT-INVALIDATION / 正常证明失败 | 导入的当前辅助证明真的失败，旧证书不能直接恢复：当时Vero_Cache_Maintenance完整被导入theory内含失败aux；即使修复过度撤销逻辑，新的import context不成立时不能恢复历史5个GREEN。 | addressed via user-authorized archived workspace restoration then native revalidation; no manual GREEN promotion | offline_regression；state-prefix-revocation | [recovery-receipt-v3.json](../reports/regen/vero-prefix-revalidation-repair-20261025-001/recovery-receipt-v3.json) |
| B-VERO-BUDGET-BRANCH-OVERLAP / 比较局限 | 回滚分支与框架重验用时需按区间去重冲正：原计时连续且忠实保留已消费成本；用户选择退还弃用分支9954.647329s与重验1362.444s，重叠906.1s不能双退。该冲正是授权实验口径，不是全段均为框架错误的证明。 | credited1362.444 +9048.547329=10410.991329s; history/token/planning retained | documentary_regression；state-budget-union | [audit.json](../reports/regen/vero-effective-time-audit-20261026-001/audit.json) |
| B-VERO-UNCLEAN-TAIL / 运行资源 | 非正常退出留下未持久结算尾部，不能补算离线或猜测退款：checkpoint每约30s；进程非正常退出没有close边界，最后checkpoint至退出间有效用时未知。10/25约17.0025s；10/26 ENOSPC约28.384s。 | uncertainty retained across resume; no guessed charge/credit | offline_regression；state-crash-clock | [audit.json](../reports/regen/vero-effective-time-audit-20261026-001/audit.json) |
| B-VERO-RPC-TRUNCATION / 框架bug | 16MiB单次readline截断巨大JSON响应：客户端readline(16*1024*1024)把798033089-byte response切断后交给json.loads，模型看不到native诊断；失败还沿dependency链传播。 | fixed bounded diagnostic preview +64MiB complete frame + byte paging; historical785184572-byte log validated live | offline_regression；vero-transport-frame-16m, vero-transport-bounded-diagnostics, vero-transport-archived-completion | [protocol-and-targets.json](../reports/regen/dual-token-stall-audit-20261026-001/protocol-and-targets.json) |
| B-VERO-DISK-AMPLIFICATION / 运行资源 | 日志/结果/响应复制放大导致ENOSPC：evaluation log、native prefix log、native result.json和response.json重复承载大诊断；47个>256MiB native result合计26.897GB，最大response1.254GB。旧heaps/历史产物也占盘；不能按大小盲目去重。 | 31 responses losslessly gz archived,21.773GB reclaimed; new responses bounded; native-log growth remains resource risk | offline_regression；state-storage-enospc, vero-transport-bounded-diagnostics, vero-transport-archived-completion | [pause-audit.json](../reports/regen/vero-rpc-repair-20261026-001/pause-audit.json) |
| B-VERO-STALE-RUNTIME-CLIENT / 框架bug | 冻结PYTHONPATH导致新server仍挂载旧client：Agent配置合并保留冻结env.PYTHONPATH指原始release；python -m vero_isolated_exec从旧目录载入并将其旁旧tool.py挂入容器。scope/progress成功不能证明新版diagnostic可用。 | fixed runtime env override and explicit v2 migration; actual mount hash/help/RPC validated | offline_regression；vero-runtime-stale-pythonpath, vero-runtime-explicit-migration | [vero-stale-client-mount-evidence.json](../reports/regen/dual-step-resume-validation-20261027-001/vero-stale-client-mount-evidence.json) |
| B-CODEX-RECOVERY-COST / 性能 | 同应用continuation仍多次全历史解码校验并逐文件sync：991 commits嵌套约6.07GB逻辑证据；preflight/carry/append进行三次语义验证，HEAD后另需decode+hash；普通continuation不进入native_audit_repair的批量copy上下文，21000+小文件逐个sync。 | observed and audited; recovery completed without mutation; general optimization not deployed | offline_regression；codex-recovery-history-amplification, codex-step991-continuation | [codex-recovery-cost-audit.json](../reports/regen/dual-step-resume-validation-20261027-001/codex-recovery-cost-audit.json) |
| B-CODEX-ORDINARY-PROOF-RED / 正常证明失败 | Codex batch628仍有5个未闭合目标，非timeout或传输故障：resolveAddressBits_ccorres候选执行到done仍余5个subgoals，returncode1、timeout=null；最近无GREEN不能自动归为基础设施停滞。 | normal proof exploration; continuation preserves11 accepted | offline_regression；codex-batch628-proof-not-timeout | [codex-live-validation.json](../reports/regen/dual-step-resume-validation-20261027-001/codex-live-validation.json) |
| B-VERO-RECOVERY-CHANGED-ARTIFACT / 比较局限 | 不同iteration源码不能复用旧完整评估报告：iter005模型已改变c0236:prove，artifact SHA不同于iter004；旧eval report即便14GREEN也未验证新候选。恢复全native检查有成本，但不能复制report伪装已验证。 | normal recovery re-evaluated iter005; no unsafe report reuse | documentary_regression；state-artifact-reuse | [vero-resume-reuse-audit.json](../reports/regen/dual-step-resume-validation-20261027-001/vero-resume-reuse-audit.json) |
| B-MEDIUM-XHIGH-COMPARISON / 比较局限 | medium/xhigh产出差距不能单独归因推理深度：原始归属信息、规划计费、分组/导入结构、框架修复、恢复开销、机器/槽位条件不同；长GREEN间隔混合正常探索与框架错误，缺配对对照。 | requires controlled comparison; no causal benefit/no-benefit claim established | documentary_only；无可执行case | [README.md](../reports/regen/crefine-owner-blind-input-20261024-001/README.md) |
| A-STALL-UNCLOSED-CLOCK / 框架bug | SessionStalled遇未闭合item升级全局计时暂停：900s无有效工具/证明进展触发SessionStalled；本地停滞没标明确中断，_generation_stop进入未闭合agentMessage校验，generation_clock_untrusted关闭并行全局预算。底层最终静默是网络/provider/client哪层未证实。 | provider-feedback repair and once resend recorded; original cost retained | documentary_only；无可执行case | [REPORT.md](../reports/regen/crefine-pause-audit-20261024-001/REPORT.md) |
| A-SILENCE-ROOT-CAUSE-UNKNOWN / 运行资源 | 同时静默不构成已确认主机断网证据：TLS/SSE异常确实发生并恢复；最后两worker在0.317s内静默，但无连接生命周期/流量时间戳/部分缓冲区观测，无法区分网络、远端或app-server。 | unresolved underlying cause; do not assign network credit by assumption | documentary_only；无可执行case | [network-evidence.json](../reports/regen/crefine-pause-audit-20261024-001/network-evidence.json) |
| A-NATIVE-WORKER-READY / 运行资源 | Thread_State worker启动等待ready超时：NativePreparationError incremental batch worker timeout waiting for ready；已有18/18GREEN保留，无法从摘要单独定CPU、IO、进程哪层根因。 | historical snapshot: infrastructure_error/unsealed; current A not inspected | documentary_only；无可执行case | [progress-20261027-0004.json](../reports/regen/crefine-owner-blind-sol-xhigh-20261025-001/progress-20261027-0004.json) |
| A-GAP-AUDIT-EMPTY-TARGET / 框架bug | 后置审计未取得目标定理：后置审计未取得目标定理这一症状已确认；它发生于seal后置检查，现有类别摘要不足以确认每个实例究竟是context解析还是候选未导出。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-EDIT-VALIDATION / 正常证明失败 | edit_validation：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-INVALID-TOOL-REQUEST / 正常证明失败 | invalid_tool_request：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-NAMESPACE-FACT-LOOKUP / 框架bug | 后置审计locale短名查找失败：草图检查和seal后置审计在包装context之外/global context按节点短名查locale事实；19次与正文18次Undefined fact分开，不把前者归普通证明错误。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-NATIVE-TIMEOUT / 运行资源 | 原生执行或审计超时：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-OPEN-GOALS / 正常证明失败 | open_goals：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-PROOF-METHOD-FAILURE / 正常证明失败 | proof_method_failure：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-PROVIDER-NO-COMPLETE-RESPONSE / 运行资源 | 约900s无完整provider反馈：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-SEAL-PREREQUISITE / 正常证明失败 | seal_prerequisite：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-SKETCH-PREREQUISITE / 正常证明失败 | sketch_prerequisite：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-SLOW-RETRIEVAL / 性能 | 成功query/observe回调耗时至少60s：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-SYNTAX-OR-PROOF-MODE / 正常证明失败 | syntax_or_proof_mode：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-TYPE-OR-BINDING / 正常证明失败 | type_or_binding：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-UNKNOWN-FACT / 正常证明失败 | unknown_fact：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-UNKNOWN-METHOD / 正常证明失败 | unknown_method：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-GAP-UNSUPPORTED-WORKFLOW / 正常证明失败 | unsupported_workflow：结构化台账观察类型；不把错误字符串直接当成底层根因。普通证明/工具拒绝不计框架bug。 | historical A window audit only; no current A changes or verification | historical_statistics_only；history-a-green-gap-observations | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-BLOCKED-AUTO-CONTINUE / 框架bug | 模型报告harness阻塞后自动Continue四次：4/4GREEN但seal审计错误，模型final报告blocked仍被通用continuation继续；模型继续无法修复trusted审计器。状态分类/调度未将framework故障分离。 | historical A report; current behavior not verified | historical_statistics_only；history-a-green-gap-observations | [auto-continuation-observations.json](../reports/regen/crefine-green-gap-audit-20261027-001/issue-ledger/auto-continuation-observations.json) |
| A-PROGRESS-WATCH-DISABLED / 性能 | 有效预算模式关闭信息进展检测，长期RED仍可能持续：progress_timeout=0不创建ProgressWatch，信息重复读取检测一起关闭；无GREEN和无信息进展不是同一概念；真实RED本来就算有效证明工作。 | intentional policy/risk, not automatically a bug; preserve no attempt/no-progress hard cap requirement | documentary_only；无可执行case | [ISSUE-LEDGER.md](../reports/regen/crefine-green-gap-audit-20261027-001/ISSUE-LEDGER.md) |
| A-PLAN-REVIEW-SELF-REFERENCE / 框架bug | 旧medium评审自引用导致一次性退出：历史review包含Generated_TCBInvocations/setPriority_ccorres及Generated_InvocationDispatch/Arch_decodeInvocation_ccorres自消费，旧validator一次抛错终止。 | schema feedback repair now exists; deleted old raw run | documentary_only；无可执行case | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| A-PLAN-REVISION-DECISIONS-FEEDBACK / 框架bug | 修订决策错误缺少受控反馈修复：revision planner结束后才检查decisions，缺文件/字段/非法枚举直接失败。 | audit finding F1; current implementation status not revalidated | documentary_only；无可执行case | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| A-PLAN-CORRUPT-TRANSCRIPT-AUDIT / 框架bug | 损坏NDJSON未阻止provider调用审计通过：parse_errors被记录但no_tool_events/终止条件未强制完整；模拟provider返回0和合法final可被接受。 | audit finding F2; not observed in old live experiment; current status not revalidated | documentary_only；无可执行case | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| A-PLAN-FINAL-PLAN-REVIEW-GAP / 比较局限 | 一次修订直接冻结，最终计划未重新消费评审：当前被评审对象是draft；修订后只结构比较/native声明检查，不能当作最终语义消费成功。 | audit finding F3; design limitation | documentary_only；无可执行case | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| A-PLAN-PLANNING-FAILURE-RESUME / 框架bug | planning失败目录fresh-only且workspace路径超时策略不同：review/workspace CLI要求新目录；workspace分支未用call_with_retry；旧运行不自动续跑。 | audit finding F4; current implementation status not revalidated | documentary_only；无可执行case | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |
| A-PLAN-REVIEW-SEMANTIC-BOUNDARY / 比较局限 | 格式合法、静态import图与评审意见不能证明计划质量：非空占位assessment、空issues或revise但无真实plan差异可通过；review本身不是通用脱敏器。 | audit findings F5/F6/F7; synthetic behavior, no proof acceptance claimed | documentary_only；无可执行case | [REPORT.md](../reports/regen/plan-interface-review-audit-20261024-001/REPORT.md) |

测试函数完整身份、精确step和证据摘要仍以 [inventory.json](tests/fixtures/crefine_incidents_v1/inventory.json) 为准；覆盖范围与缺失声明见 [coverage.json](tests/fixtures/crefine_incidents_v1/coverage.json)。源inventory文件SHA-256：`a6f2480d00169097ffe5d2d3e08cc654f94856dba12b44e38e418673a3b5780d`。修改源清单后需要同步本节，避免静态表漂移。

## 通用回归的验收合同

不要只针对历史错误字符串打补丁。对两种agent的实际生产入口，执行正常路径与中断注入，检查以下不变式：

| 故障动作 | 必须保持的不变式 | 通过范围 |
|---|---|---|
| native长回调、断开RPC后查询、重复request | progress/result有界返回；同request最多一次提交；busy与新任务不同；断连不丢权威结果 | 离线协议+真实子进程 |
| provider静默、部分消息、terminal先到、非法工具参数 | 分类正确；仅按既定策略重试；不把未完整调用记作proof RED；有界保存并退出 | 协议故障注入，随后真实provider smoke |
| 在reserve/write/fsync/replace/commit/close边界杀进程或ENOSPC | 要么完整旧状态要么完整新状态；GREEN和计费不重复；未知尾部明确；sync失败不得发布成功 | 独立进程/持久化故障注入 |
| 恢复共享DAG、缺对象/损坏hash、同源新增commit | 保留Merkle身份；只读源不改；拒绝损坏；完成持久化后才报告恢复成功；资源增长有实测 | 小型图及真实大历史分别报告 |
| 改unused/used helper、import和artifact、换renderer | 无用变化不无故毁成果；真实依赖变化必须复验/失效；旧证书不能借给新源码 | 合成native正反例，精确历史回放另列 |
| locale短名、同名父事实、bundle、多结论、空名 | 真正新导出事实才验收；拒绝空/歧义/旧fact/skip-proof；seal与sketch范围分别验收 | 真Isabelle合成fixture |
| server升级但冻结env保留旧client | server、transport、容器client的实际载入路径/摘要一致；新版diagnostic真实RPC成功 | 真实容器挂载与RPC |
| blocked后auto-continue、重复查询、长期RED | 框架阻塞不伪装数学失败；信息观察与推进分开；不擅自设置no-GREEN硬截止或无条件退款 | 调度状态机与受控在线长回调 |
| 规划非法JSON/自消费、revision字段缺失、损坏NDJSON | 完整反馈；当前阶段恢复边界明确；非法输出不发布plan；最终消费边界与结构合法性分开 | 当前planner集成正反例 |
| 变更模型/预算/恢复点、多臂时钟、准备排除 | 错配调用前拒绝；未知尾部和重叠不重复扣/退；inherited GREEN不算新增；无效arm明确无效 | 配方绑定与独立时间oracle |

已有离线总入口：

```bash
python3 -m regen.tools.replay_incidents verify
python3 -m regen.tools.replay_incidents run --output /tmp/experiment-incidents.json
```

当前固定包为17案例、19回放；44项源清单中13项offline_regression、2项documentary_regression、17项historical_statistics_only、12项documentary_only。不能把19/19回放通过说成“全部异常都有通用测试、全部修复或完整实验已稳定”。本次汇总没有新增或重跑这些验收。

## 新事件填写模板

```text
ID：沿用已有RUN-ID/原incident-ID；新机制才分配新ID
run/machine/application/environment：
阶段与step/request：
异常类别与可观察终点：
开始/最后有效反馈/结束时间、并行重叠：
症状 / 已确认根因 / 仍未知：
受影响contract、已有GREEN与seal、预算和尾部完整性：
证据路径、内容摘要与原始材料是否仍可用：
恢复前提、重试/退款授权与幂等键：
当前源码修复 / 冻结部署 / native或在线复验：分别记录
最小复现、测试ID、独立oracle、最近结果：
关联旧事件/替代记录：
```

## 本轮清理范围

已按用户要求删除reports下5—7月旧主题和运行内容；与本台账相关的9—10月异常证据保留。本文件不是删除其他历史日志、重启实验或重新计费的授权。删除记录见 [may-july-deletion-20261027.json](../reports/regen/report-conclusion-review-20261027-001/may-july-deletion-20261027.json)。
