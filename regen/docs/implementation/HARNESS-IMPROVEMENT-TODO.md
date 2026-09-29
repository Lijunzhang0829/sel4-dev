# File Planner harness 改进 TODO

记录日期：2026-09-21。本文记录已观察的问题、改进顺序和验收标准，不代表这些改进已经实现，也不触发实验重启。

2026-09-21 历史范围：单 File Planner 管理一个 theory 的图、helper 生命周期、检索和批量证明。当时暂不扩展多 File Planner 并行；2026-09-24 已确认的新范围见下方索引。保持固定 contract 不可削弱、正式 GREEN 仅来自验证证据、最终完整封存的边界；不恢复任意决策次数上限。

## 2026-09-25：当前流程与后续 TODO 索引

当前主清单为 [跨 theory 并行、单 File Planner 串行](../design/PARALLEL-PROOF-PIPELINE.md)。本节只维护导航；下文是历史实验与问题记录，其勾选不替代当前验收。

| 模块 | 当前状态与后续关注 | 主清单 |
|---|---|---|
| 输入与环境 | 人工保证环境内容不变，保留来源/数据库/目标校验 | [M0](../design/PARALLEL-PROOF-PIPELINE.md#m0) |
| 仓库规划 | visibility 提示已修正；实际分组质量待评价 | [M1](../design/PARALLEL-PROOF-PIPELINE.md#m1) |
| 规划验收 | 原生预检已接执行器，拒绝过期证据 | [M2](../design/PARALLEL-PROOF-PIPELINE.md#m2) |
| 调度 | 多 theory 持续 agent、单写入与完成即补位已接通 | [M3](../design/PARALLEL-PROOF-PIPELINE.md#m3) |
| File Planner | theory 内检索、读取、检查、seal 均串行；保留 batch 与探索自由 | [M4](../design/PARALLEL-PROOF-PIPELINE.md#m4) |
| 检索与共享 | 一致提交快照与复制复验已有；索引增量化待评估 | [M5](../design/PARALLEL-PROOF-PIPELINE.md#m5) |
| Isabelle | 跨 theory 共享资源池，工具完成后返回；资源校准待实验 | [M6](../design/PARALLEL-PROOF-PIPELINE.md#m6) |
| 接受控制器 | 保留上下文/证据校验与原子 batch；raw check 独立重提交延后 | [M7](../design/PARALLEL-PROOF-PIPELINE.md#m7) |
| Theory seal | 完整检查、审计、build 与资源关闭已接通 | [M8](../design/PARALLEL-PROOF-PIPELINE.md#m8) |
| 仓库集成 | 全目标覆盖与独立构建 gate 已实现，实际生成闭环待实验 | [M9](../design/PARALLEL-PROOF-PIPELINE.md#m9) |
| 日志评估 | 关键阶段日志已有；完整模型与 native 成本对照待实验 | [M10](../design/PARALLEL-PROOF-PIPELINE.md#m10) |

先验证正常工作流程。环境冻结与强杀进程回收已退出当前必需范围；不再开展单 File Planner 内部异步交错。代码和单测通过不等于完整模型实验验收。

## 2026-09-25：仓库规划质量复核与 token 观测 TODO

用户确认：记录以下建议，当前实验沿用已接受计划，不重新规划。模型为
`gpt-6-astra / medium`，两个 theory 并行，各 theory 内工具串行；不新增修订次数上限。

基线：`repository-plan-serial-astra-visible-20260925-001`，
`plan_id=repository-plan:3cfec5a378b5d72d6f0c`。287 contracts、27 theories、57 条硬 imports、
4 个根、最长链 9 层。当前已有持续 agent 内的草案、自审、验证与修订，不是一次无反馈出稿。

### P1：边界、接口与并行瓶颈复核

- [ ] 在现有覆盖、命名、引用、DAG 校验外，增加紧凑风险报告：组大小、根、最长链、
  高影响 provider、后继数量、传递冗余。指标仅提示，不按数量阈值拒绝合理划分。
- [ ] 试验一次独立定向复核：消费者具体希望获得什么成果，为什么必须等待 provider
  整体 seal；可能帮助是否被误设硬 import；强相关证明是否拆散；受限/一般情况是否
  消除了实际循环。每条意见引用具体 contract/theory 和证据。Planner 决定采纳或解释
  保留理由；主观意见不成为机械拒绝门槛。复用已有 reason/NOTES，避免重复维护计划。
- [ ] 优先检查 Thread_Object_State（22 contracts、22 后继）、Scheduler_Queues（24/14）、
  Translation_Entries（22/10）：高复用小接口能否自然独立，还是确需共享局部证明空间。
  不按数量机械拆分。13 条传递冗余边删除后不改变 readiness，不作为加速重点。
- [ ] 分开评价声明上下文与未来证明接口：本次首次预检通过后，agent 自查为
  Invocation_Dispatch 补充 TCB_Invocation_Decode import，两版均通过。oops 不导出
  新定理，不能据此认定未来证明依赖齐全或数学路线成立。

### P2：检查成本与小规模 A/B

- [ ] 默认在边界复核后执行 native 预检，仍允许按需提前检查。本次总耗时 836.013 秒，
  两次 native 分别 204.441、213.113 秒，约占一半。本次实际改了 import，第二次验证
  有理由，不能直接标为无效重复。
- [ ] 评估预检缓存按实际渲染的声明、上下文、imports 与 checker context 绑定，提交记录
  独立绑定；仅解释文字变化不强迫重检。受影响 theory/后继增量复验另行实现和验收，
  未实现前仍保留完整验证。不引入环境冻结。
- [ ] A 用当前计划，B 用定向复核后的计划；选实际改变边界的 provider–consumer 子图
  及必要上游，从空证明、同模型/资源开始。比较完成/封存、总墙钟、模型输入、native
  时间、重复 helper 与跨 theory 读取，新增规划成本计入。有效后再决定 reviewer 是否
  默认启用；当前实验不隐式执行 B。
- [ ] 普通证明失败、helper 调整与检索问题留在 File Planner；仅有具体跨 theory 接口
  缺失/边界证据时提出局部调整。已启动 theory 的 imports 不静默修改，不增加常驻
  全局重规划或基于失败次数的自动让出执行槽。

一手资料与边界：

- [CodePlan](https://arxiv.org/pdf/2309.12499)：已有仓库的依赖/影响分析和验证反馈驱动
  修订；未知证明依赖不能由共享符号或实现调用直接推导。
- [Agentless](https://arxiv.org/html/2407.01489v2)：结构→相关声明→局部代码的分层读取及
  定位/修复/验证；issue 修复结果不能直接外推为限制形式化证明自主探索的依据。
- [MetaGPT](https://arxiv.org/html/2308.00352v6)：文件/接口设计、结构化交接及执行反馈；
  借鉴接口复核，不直接复制全部角色或外推性能结论。

### P1：统一 token 统计，利用现有事件而非周期心跳

已有记录：仓库规划 `transcript/*.ndjson` 保存 `turn.completed.usage`；新版并行 File
Planner 的 `parallel-sessions/<session>/<theory>/session-transcript.jsonl` 保存
`thread/tokenUsage/updated`；正常结束的 theory `result.json` 包含 `provider.usage`。
中断时可能只有最后一次 usage 通知，未观测尾部不能记成零。图修订和证明生成共享持续
File Planner 会话，不能虚构独立 GRA/prover 成本。检索和 Isabelle 本身不消耗 LLM token，
工具返回内容可能增加后续模型输入。

- [ ] 可重建汇总：按 experiment/module/theory/thread 分开统计 input、cached input、
  output、reasoning（服务提供时）、完整性、最后观测时间，保留原字段与来源。
  CLI/app-server 字段不同；累计 total 快照不可相加，last 不与 total 重复计数；
  transcript/result 是同一次消费，不能重复累计。重试和独立 thread 分别计入。
- [ ] 结合工具时间线与成功提交分析上下文增长、缓存命中、检索反馈体积、失败修订成本、
  每 GREEN/seal 成本和并行重复工作。批量证明/共享 helper 不强行精确摊分到 contract，
  无法唯一归因的区间明确标注。
- [ ] token 与总墙钟、模型等待、Isabelle 执行及资源排队分别报告。高缓存命中不等于
  零延迟；token 不能独自证明数学质量、有效思考或前端正常，不能直接推算账单。

本次规划 usage：input=3,261,580，cached input=3,155,456（约 96.75%），
input 减 cached=106,124，output=16,180，reasoning output=1,772，cache-write input=0。
input 是多次推理累计消费，非独立上下文长度；reasoning 不另加到 output。
当前仍缺跨模块统一汇总与精确工具归因；现有原始日志可作为本轮基线。

本轮启动诊断（2026-09-25）：

- [x] 修复真实 app-server 启动时误禁用 `code_mode_host`，保持 shell/其他工具禁用及
  原有动态工具白名单、参数校验、完整审计。21 项相关测试及 Astra medium 的单次真实
  `proof_action` 传输测试通过，见 `reports/analysis/dynamic-tool-host-20260925/smoke-001/`。
- 初次 `parallel-file-planner-astra-20260925-001` 四个根 theory 在工具传输阶段结束，
  无 Isabelle 检查、无 GREEN/seal；总 input=402,955、cached=266,752、output=698、
  reasoning=88。完整记录在该 run 的 `startup-diagnosis.json`，不计作数学证明失败。
- 修复后使用新身份 `parallel-file-planner-astra-20260925-002`，沿用同一仓库计划，
  重新从空证明开始。传输 smoke 成本另行保留，不混入 theory 证明成本。
- [ ] 后续评估启动工具自检以及零回调终止的基础设施诊断；协调器进程 exit 0 只代表
  调度正常返回，不代表 theory seal 或实验成功。

## 2026-09-25：默认改为 theory 源码工作区

- [x] 单 File Planner 默认持久 `.thy` 编辑、批量检查和失败修订，不要求模型维护局部 DAG。
- [x] 固定 contract/imports 保护、按文件顺序检查、oops 无前提权威、失败草稿持久保存。
- [x] 修改/删除/重排已接受 helper 或 proof 时完整复验；成功版本原子接受，旧证据保留。
- [x] seal 绑定当前草稿；检索、实际引用审计和跨 theory 并行资源池保持可用。
- [x] 735 项测试、真实 Isabelle 原文件通过→悬空 helper 引用失败→修复通过。
- [ ] 同 theory 的源码模式/旧 graph 模式 A/B 成本与成功率，不能由接口小例推断性能。

当前语义见 [源码工作区](../design/THEORY-SOURCE-WORKSPACE.md) 及主流程 M4/M7。
内部 graph 字段保留作兼容声明索引；仓库 imports DAG 继续决定跨 theory readiness。

## 证据与统计口径

### Refine_Capability：固定前 178 次请求

运行：[batch-capability-20260921](../../../reports/regen/refine-first-run-001/batch-capability-20260921/)。统计截止请求 178 完成；这是历史截面，后续运行进度不能覆盖此口径。

| 动作 | 次数 | 模型调用耗时 |
|---|---:|---:|
| query | 150 | 90.18 分钟 |
| submit_batch | 27 | 27.88 分钟 |
| read_proofs | 1 | 0.24 分钟 |
| 合计 | 178 | 118.30 分钟 |

27 次证明批次中 6 次 GREEN、21 次 RED，产出 3 个 contract 和 3 个 helper。4 次多节点提交均失败，成功提交均为单节点。3 个 contract 是 `arch_postCapDeletion_corres`、`arch_deriveCap_corres`、`postCapDeletion_corres`。

| 其他已记录执行环节 | 次数 | 耗时 |
|---|---:|---:|
| 整图声明检查，含初始化和声明修改 | 14 | 85.72 分钟 |
| 证明批次检查 | 27 | 33.32 分钟 |
| Isabelle 符号解析 | 23 | 14.72 分钟 |
| 上游 session build | 1 | 6.64 分钟 |

以上模型耗时和 token 是每个请求主调用文件的历史统计，不含额外重试；暂停快照统计了 323 次物理模型调用（含 4 次重试），累计模型耗时 211.934 分钟，覆盖请求 1–319，不能与前 178 次口径直接比较。

含模型主调用的已计时合计约 258.70 分钟。不是完整端到端耗时：未单独统计 journal 回放、序列化、数据库查询及其余间隔，也未把等待时间强行归因给这些操作。

观察到：

- 请求 59–126 连续 68 次都是 query，没有提交证明。
- 24 次 query 返回空 records；另有 5 次请求因参数组合不合法被拒绝。
- 相同 query/mode/scope/offset 的 `cte_relation` 搜索出现 6 次；这不是已经排除 context 差异后的“完全重复请求”统计。
- 前 10 次平均输入约 35,943 token，最后 10 次约 118,509 token。
- 累计输入 16,014,809 token，其中 cached input 1,297,536；输出 94,693 token。累计量包含重复上下文，不等于独立资料量或账单金额。
- 通过拆出 IRQ 状态相关 helper，最终解决了 `postCapDeletion_corres`。检索不是全部无效，但转向构造和验证辅助节点较晚。
- 此截面的实际检查均为旧冷检查路径；不能把新增量 worker 的收益计入此实验。

复核入口：`theories/Refine_Capability/batch-requests/000001` 至 `000178` 中的 `proposal.json`、`feedback.json`、`call.meta.json`、`call.ndjson`、`check.json`，以及根目录 `runner.log`。一次决策是一次模型 JSON 动作，不等同于一次证明尝试；每次 query 还可以包含多个查询。

### Refine_StatePrimitives：批量续跑与优化回放

- [批量实验指标](../../../reports/analysis/batch-file-planner-validation-20260920/METRICS.md)：请求 1–26 和封存的已记录计算耗时约 80.32 分钟。继承 12 个已证明节点，仅新增两个 contract；不含旧单节点实验和诊断测试。
- 7 次批次检查中，已接受前缀的节点计时累计约 31.07 分钟，占批次检查约 79.4%；节点区间含 context 处理，不是纯 tactic 时间。
- [增量验证报告](../../../reports/analysis/incremental-batch-check-20260921/REPORT.md)：首次准备 12 节点前缀 214.27 秒，`zipWithM_x_corres` 新批次 9.02 秒；下一批 `setObject_other_corres` 总计 9.57 秒。
- 上述是已接受证明的只读回放，不是新证明生成；与旧冷检查的耗时对比不是同负载受控基准。
- [VERO 检查器快照](../../../reports/analysis/vero-checker-comparison-20260921/incremental.py)：源码/导入/配置绑定的成功缓存和尾部截断。其底层仍启动新 Isabelle process，不应描述为持久 Toplevel 状态复用。

## 已实现，但需区分实验采用状态

- [x] 持久 Isabelle 批次 worker：真实前缀复用、试验失败回滚、提交后延迟推进、依赖审计、重启重建和冷检查回退。实现见 [batch_checker.py](../../execution/batch_checker.py)、[batch_checker.ML](../../execution/batch_checker.ML)、[proof_batches.py](../../execution/proof_batches.py)。完整回归 434 项通过，原生 smoke 和真实案例回放通过。
- [x] 外部目标检索与 sealed proof examples；当前 theory 的 GREEN 留在 planner 工作状态中，通过 read_proofs 读取。参见 [批量 File Planner](BATCH-FILE-PLANNER.md) 和 [premise 数据库](PREMISE-DATABASE-V2.md)。当前相似度是词法方法，不是 MaSh 或学习型证明策略检索。
- [ ] 将增量 worker 用于后续正式生成实验。创建新身份或受验证 continuation，保留提交、反馈和累计成本；记录旧进程停止边界。验收：真实新候选记录 `audited-batch-worker-v1`，能区分首次准备、热检查与冷回退，最终通过完整封存。

## P0：恢复和验收信息必须准确

- [ ] **统一正常执行与中断恢复的反馈更新逻辑。**
  - 问题：feedback.json 写完而 planner-feedback 尚未提交时中断，恢复只补部分 history/notes，可能漏掉已读取证明、查询 facts 和最新反馈。
  - 入口：[restore_batch_memory](../../planning/batch_file_planner.py)。
  - 验收：在反馈文件落盘后、journal 提交前注入中断；恢复后下一轮可见信息与不中断执行一致，不增加 GREEN，不退还已收费请求。
- [ ] **移除根据“证明文本相同”推断历史请求 accepted 的逻辑。**
  - 问题：后来接受相同 proof body，可能让较早失败候选在恢复历史中被误标 accepted；正式 proof state 未被直接修改，但会误导模型。
  - 验收：接受状态只来自已验证提交和请求绑定；RED 历史候选保持 RED，checked-but-uncommitted 保持未提交。

## P1：减少低收益查询与重复上下文

- [ ] **持久化可查询的检索记忆。**
  - 记录规范化 query、mode、scope、offset、context、导入/数据库身份、返回 ID、空结果和后续用途；可见前缀变化时合理失效。
  - 给模型提供重复/空结果提醒和按目标汇总，而不是只依赖最近 12 次动作；允许模型说明新理由后重查。
  - 验收：区分完全重复、分页和 context 改变；恢复后记忆不丢失；相同条件下重复查询可复用结果且保留动作审计。
- [ ] **提供“继续检索／试证／拆 helper／换目标”的停滞反馈。**
  - 展示距上次新事实、证明提交和 GREEN 的间隔，要求模型说明下一次查询要验证的具体假设。
  - 不设置任意硬轮数上限，不强制执行未经模型提出的数学路线。
  - 验收：在固定对照案例中报告最长纯查询段、查询到提交比例、单位 GREEN 成本；不能仅凭查询次数变少宣称提升。
- [ ] **工作上下文按需装载。**
  - 保留完整权威数据，输入只携带当前目标相关 facts、必要证明示例及摘要；其他信息用 ID 按需读取。不得截断命题以节省 token。
  - 提供模型维护的工作笔记及按目标的失败摘要；避免把全部历史 facts/证明正文重复发送。
  - 验收：长任务输入不再随历史简单单调增长；被卸载的完整 statement/context/proof 可恢复读取；记录 input/cached/output token 和正确性变化。
- [ ] **改善检索相关性与参数可用性。**
  - helper、proof_example、vectors 的合法组合提供清楚 schema 和可恢复错误；避免模型反复请求不支持的组合。
  - 呈现候选的完整前提、locale/context、类型、来源和导入可见性；区分名称/结论相似与实际可应用，不将目录命中当作适用性证据。以 cap_delete_one 缺失 locale 实例和 get_cap 表示关系失败作为评估样本。
  - 为相关性不足的 proof examples 明确返回“无合适示例”，提供匹配依据，不让弱词法重合伪装成相似证明策略。
  - 使用真实目标及人工评估的小规模标注集比较召回/排序；不把被删除的目标人类证明加入数据库。
  - 验收：覆盖当前 5 次参数错误模式；记录候选被引用/采用的比例、空结果和错误率，而不仅是返回条数。

## P1：按节点缓存、增量检查声明（已确认可行，待实施）

2026-09-21 补充：当前声明检查并非不检查上下文。`_source(..., statements_only=True)` 为每个节点生成 `wrap_context(node.context, node.statement + "\\n  oops")`，所以会执行其 locale/context/interpretation 并检查声明的解析和类型。它不执行证明正文，也不检查“证明能否在所需前提下完成”；每个 `oops` 不会建立供后续节点引用的定理。Python 图检查与原生声明检查是不同责任。

因此，在冻结 imports/语义环境中，按节点检查完整 context + statement 是可行的，没有必要因一个新 helper 重查所有未变化声明。检查单位是节点，但调度可以把多个失效节点放进一次原生调用，避免每个节点都冷启动一个进程。

- [ ] **拆分图结构验证与节点声明验证。**
  - 每次图修订继续做低成本全图检查：固定 contract 覆盖、ID/名称冲突、引用完整性、DAG、GREEN 不变性。
  - 原生检查只处理新增或失效节点，成功证据必须覆盖候选图全部声明；不要求本次重新执行所有声明。
- [ ] **建立独立于 proof batch 提交的声明证据缓存。**
  - 缓存键至少绑定 name、完整 statement、完整 context、冻结 imports 的传递内容身份、语义环境和检查器实现；ID 仅用于映射，不能单独决定命中。
  - 声明检查通过而证明失败时，可以保留声明证据；候选仍不成为正式图或 GREEN。
  - 本案例 13 次候选图检查全部 GREEN；失败批次中的新 helper 没有提交，下一次相对正式图仍是新增，导致反复整图检查。缓存应直接覆盖这条重复路径。
- [ ] **定义精确的失效与跳过规则。**
  - 新增、改名、改 statement/context：检查对应节点。
  - 仅修改 proof body、策略、路线：不重查声明。
  - 仅修改调度依赖：继续图验证；只有实际声明上下文/可见性改变才失效。
  - 删除节点：检查图引用和 GREEN 约束；其余节点声明环境未变则复用。
  - 修改共同 imports、语义定义或共享上下文：使所有受影响节点失效；依赖影响无法准确确定时保守扩大范围。
  - 研究用成功的真实证明检查覆盖对应新增声明，避免同批先声明检查、再证明检查的重复；未证明节点仍需要独立声明证据。
- [ ] **验收与对照。**
  - 与现有整图检查对照合法/非法 statement、类型错误、未知 locale、context 改动、名称冲突、失效引用、删除及 imports 变化。
  - 同一 helper 声明不变而连续修正证明：只有首次原生声明检查，后续明确记录缓存命中；不增加正式 GREEN。
  - 所有节点证据与候选声明精确绑定；重启和证据损坏不能绕过验证。
  - 报告检查节点数、缓存命中、原生启动次数与时间，单独核算本案例 85.72 分钟整图检查的节省；不混用证明前缀缓存收益。

本项仅加入 TODO，本轮不修改检查器，也不切换当前实验。

## 下一阶段：以两个案例为依据优化检索逻辑

用户指定下一阶段先研究检索；上面的声明增量方案保留待实施，不作为开展检索分析的前置条件。

| 观察 | 对应问题 | 下一步检查/验收 |
|---|---|---|
| StatePrimitives 中查询曾覆盖最新证明失败；后已分离 last_proof_feedback | 检索和证明修正的记忆衔接 | 保留已修复行为；覆盖查询后继续修正、跨中断恢复，不能重新丢失失败候选和空结果 |
| Capability 请求 59–126 连续 68 次查询 | 查询缺少结束依据，难以及时转向试证/构造 helper | 按目标还原“问题→查询→新信息→下一动作”；提供停滞反馈，不设任意次数上限 |
| 同 query/mode/scope/offset 多次出现，且近期动作仅保留 12 条 | 长期检索记忆与重复检测不足 | 按 context、imports、数据库身份区分真正重复和合法重查，保存空结果与分页游标 |
| 24 次 query 无 records | 可能是覆盖缺口、查询形式、范围过滤或排序问题，尚未逐条归因 | 分别诊断；无匹配不等于不存在定理，也不能混同符号解析提供了信息的情况 |
| 相似证明查询返回与路线无关的已封存示例 | 词法重合缺少相关性阈值和解释 | 分开评估“可引用 helper”和“可借鉴证明”；允许无合适示例，禁止把 sealed 检索说成目标历史证明检索 |
| 5 次参数组合拒绝 | 接口 schema 与模型使用方式不一致 | 给出用途对应的合法参数组合，并提供不需要重新猜参数的错误反馈 |
| 输入平均从约 3.59 万增长到 11.85 万 token | facts 和证明正文累积，重复输入昂贵 | 相关工作集、按 ID 读取完整内容、按目标摘要；测量 token/延迟与证明成功率 |
| 多次精确查找不存在或未导入的对应引理 | 名称解析、定义展开、可见性与目标本身混淆 | 返回明确来源/可见性/缺失类型；不把未证明 contract 当 premise；保留上下游隔离 |

交付目标：先产出固定请求样本的检索诊断和配对评估集，再决定修改查询协议、排序、记忆或输入组装。保持已批准的三层数据边界：上游 SQLite、已导入 sealed 成果、当前 theory 的 GREEN 工作空间。仅使用合法事实和已验证生成证明，不读取已删除目标人类证明作参考答案。

## P2：失败修正、批次粒度和结束条件

- [ ] **基于真实反馈改善批次修正。**
  - 返回准确失败节点、源码位置、剩余目标及失败类型；按目标保存反复出现的错误，减少 `all_goals` 等已知不适用语法的再次提交。
  - 验收：回放典型错误并验证反馈绑定正确候选；一般语法提示不得注入目标特定证明。
- [ ] **帮助模型选择合理批次，而非强制单节点或整 theory。**
  - 支持 helper→消费者、若干独立简单目标；大批失败后由模型缩小批次，保持当前原子验收。
  - 验收：记录每批节点数、成功率、每新增 GREEN 的检查时间；区分批量生成与多 worker 并行，本轮不扩展多 File Planner。
- [ ] **明确 seal 失败的可恢复边界。**
  - 基础设施失败可以重试；需要替换 GREEN 证明的失败不能靠重复 finish 解决，应显式给出受控恢复/新实验路径。
  - 验收：基础设施、依赖审计、完整构建失败的不同反馈及恢复路径，不静默削弱 GREEN 约束。

## P2：日志、计时与实验对照

- [ ] **模型输出事件持续落盘，补齐中断终态。** 目前 capture_output 在调用结束或超时后保存；中断可能留下 running 元数据。验收：中断保留已收到事件、已知耗时和未知成本标记，不能把缺失耗时视为零。
- [ ] **统一成功与失败步骤的结果入口。** 成功 batch/finish 没有单独 feedback.json，结果在提交里。提供由 journal 派生的步骤摘要和 commit ID；不能把摘要变成新的接受权威。
- [ ] **细化耗时归因。** 分别记录模型、检索、符号解析、图声明检查、前缀准备、增量试验、冷回退、journal 回放/提交、发布和 seal；标识等待与并行区间，避免重复相加。
- [ ] **建立配对性能评估。** 固定环境、模型与输入，用旧冷检查和新增量检查回放同一候选序列，报告冷启动/热运行、总成本及失败路径。正式生成另报告成功率，不能把已知成功证明回放当成生成能力提升。

## 建议实施顺序

1. 修复 P0 恢复问题，保证后续实验记录可信。
2. 在新运行身份采用已实现的批次 worker，并补齐分项计时。
3. 下一阶段优先分析两个案例的检索问题，形成固定评估集，再改进查询记忆、排序和上下文管理。
4. 按节点声明检查作为已确认可行的待实施项，单独安排与对照；随后评估批次粒度、失败摘要等优化。

每完成一项，填写实现位置、测试和真实实验报告，再勾选状态；修改运行绑定时保留旧结果和累计成本。不把本 TODO 当成暂停、重启或修改当前实验的指令。

## 2026-09-21 暂停后的补充证据

详见 [Capability 低收益分析](../../../reports/analysis/capability-low-yield-20260921/REPORT.md)。实验按用户要求暂停在 319 次请求、3 contracts + 3 helpers；154 后无新增证明。

- [ ] 将单一 last_proof_feedback 扩展为按目标/路线索引的失败记忆，重新激活目标时恢复未满足前提。以请求 178 → 315–317 的 cap_delete_one 路线回退作为回归样本。
- [ ] 对完整查询建立持久签名与新信息反馈。以重复 8 次的请求组及六次返回相同结果的请求组为评估样本；不设任意迭代上限。
- [ ] 对 journal 单独计时并设计可验证的增量回放/checkpoint：当前 640 条快照约 494 MiB，只读哈希链核验实测 14.05 秒；优化不得省略绑定、恢复及证据完整性验证。

这些是待实施项，本次未切换实验实现。

## 待修改内容总览（按暂停后的证据整理）

以下是上述 TODO 的实施索引，不代表新增独立任务或已经完成。

| 优先级 | 修改内容 | 预期交付 |
|---|---|---|
| P0 | 中断恢复与接受状态纠正 | 正常/恢复共用反馈归约逻辑；accepted 仅由请求绑定的验证提交确定 |
| P1 | 按目标与路线保存证明记忆 | 失败候选、剩余前提、已试路线和重新尝试依据可跨目标切换恢复 |
| P1 | 持久查询记忆与结果复用 | 完整上下文签名、空结果、分页、新信息及合法失效规则 |
| P1 | 停滞反馈与自主转向 | 展示查询/提交/GREEN 的进展，agent 自主决定试证、helper、换目标或暂停，无任意次数上限 |
| P1 | 检索适用性与接口改善 | 相关性评估、前提/context/来源/可见性呈现、示例阈值、明确参数 schema |
| P1 | 按需工作上下文 | 当前目标工作集、笔记摘要、按 ID 读取完整证据，避免全历史反复输入 |
| P1 | journal 回放与存储优化 | 先分项测量，再评估可信 checkpoint/增量验证，保持证据完整性 |
| P1，检索改进后 | 节点声明增量检查 | 全图结构验证保留；只原生检查新增或失效声明，缓存独立于证明成功与否 |
| 已实现，待正式采用 | 持久批次检查 worker | 在新运行身份/受验证 continuation 中生成新候选并完成 seal，记录冷热路径 |
| P2 | 证明反馈与批次组织 | 准确错误位置/类型/剩余目标；支持 helper 与消费者批次、失败后自主缩小 |
| P2 | seal 失败恢复 | 区分基础设施、依赖审计和证明修改需求，明确可恢复路径 |
| P2 | 日志和验证对照 | 流式输出、中断终态、统一步骤结果、逻辑请求与物理重试成本、分项耗时与配对评估 |

下一步实现顺序：P0 正确性修复 → 按目标记忆/查询记忆/停滞反馈 → 检索适用性与输入工作集 → journal 优化与正式增量 worker 实验 → 节点声明缓存及其余 P2。分项计时随各项实现补齐，不等待所有优化完成。实验继续保持暂停。

## 2026-09-21 第一批实现与实验前复核

- [x] 正常执行/恢复共用 `apply_feedback`，恢复 facts、read_proofs、JSON 失败与笔记；删除 proof 文本相等推断 acceptance。回归覆盖反馈落盘而提交未完成的窗口。
- [x] 按目标保留最新证明尝试；模型输入给出明确标记的错误摘要，通过 `read_attempts` 获取完整候选和诊断。更完整的多路线长期笔记仍待后续实现。
- [x] 查询持久签名、重复/新增 ID 反馈；精确身份与反馈哈希匹配时复用查询结果，仍保留每次模型请求计费与审计。旧查询只作提示，不跨身份直接复用。
- [x] 输入提供停滞信号，由模型自主选择下一动作，没有新增迭代次数上限。
- [x] 事实工作集与完整索引、`read_facts`；查询摘要/索引与 `read_queries`。完整命题不截断，原始证据保留。
- [x] journal 进程内已验证前缀缓存，文件变化重验、重启全验；不是可跳过验证的持久 checkpoint。存储去重与持久 checkpoint 仍待实施。
- [x] continuation 保留并验证传递 sealed providers，修复 Capability 续跑遗漏 StatePrimitives 的问题。

离线验证和一次输入迭代见 [真实样本回放](../../../reports/analysis/batch-memory-improvement-20260921/REPLAY.md)：初版完整展开记忆导致输入增长，已改为摘要及按需读取。冷/热 journal 回放约 15.0/0.10 秒；不代表端到端证明速度或成功率提升。正式实验另按新增请求统计，不将继承的 319 次请求和 6 GREEN 算作本轮成果。

尚未实施：检索排序/相关性阈值的配对改进、完整 locale 适用性分析、节点声明增量检查、seal 恢复扩展、流式日志和完整分项计时。上述历史分组中的复合验收项仍保留，不能因部分子项完成而全部勾选。

实验前补充：最终 474 项回归通过。非法 request/proofs 字段进入错误反馈后不会使下一轮崩溃；恢复接受状态来自实际 batch 事件（含 continuation），不会继承旧派生 history 的误标，即使对应观察文件缺失。新增观察绑定 proposal/feedback 哈希，恢复/按需读取遇到不匹配停止使用；向量索引、校验文件和 WAL 纳入缓存身份。最终同样本输入 285,565 字节，较旧输入减少 39.77%，实际正式生成效果仍待实验。

- [ ] **复核跨 theory 进度刷新的 journal 缓存保留。** 当前 schedule 有 15 个 theory，共享 LRU 容量为 8，空 journal 也可能占位；progress 遍历可能逐出活跃缓存。先记录真实循环间隔，再在新运行身份修复空项占位/实例缓存回退，并用超过容量的未启动 theory 覆盖测试。单 journal 热回放指标不能替代该验收。

本轮交付与真实实验状态见 [IMPLEMENTATION.md](../../../reports/analysis/batch-memory-improvement-20260921/IMPLEMENTATION.md)。v3 已创建，自动审批两次拒绝模型外发启动（含5动作检查点）；截至交付未运行新模型，不能据此勾选正式生成验收。

## 明确授权后的5动作试跑与反馈修复

用户明确确认数据外发后，v3 已运行320–324并正常检查点退出。详见 [试跑报告](../../../reports/analysis/capability-memory-v3-20260921/REPORT.md)。5次模型调用约165.83秒，总约528.25秒；无新增query、proof候选或GREEN。模型使用历史摘要识别了两条旧失败路线，但最后两次读取因同一ID缺少末尾字符被拒，原反馈过于笼统。

- [x] **读取ID错误反馈可修正。** `read_facts/read_queries/read_proofs`统一返回具体 invalid_ids 与无歧义 suggested_exact_ids，类型/数量错误分别解释；不自动替换，不放宽事实可用性。新增缺字、歧义、类型和实际错误后重试测试。
- [x] **修复空journal占用共享缓存及实例缓存丢失。** 空theory不入共享LRU；共享逐出后仍使用实例已验证前缀；优先保留较长已知历史以检测截断。15空theory遍历后不再重验活跃前缀，篡改检测测试保持通过。
- [ ] 在新运行身份验证上述修复的实际控制层间隔与证明生成效果。v3冻结版本不能直接采用后续代码；无第325次调用。

试跑后的完整回归483项通过，git diff --check通过。上述修复完成于v3退出之后；v3实验指标不包含修复后的收益。先前“尚未启动/审批阻断”是历史阶段，现已由明确授权解除。

## Refine_StatePrimitives 从零实验准备

用户改选已证明的小theory：6 contracts，使用新的init_run，不继承该theory的证明、helper、查询或失败历史。无生成provider依赖；同一上游SQLite仅含76911父session事实与1056语义导出。初始图离线核对6 contracts、0 helper、0 GREEN、空检索/目标记忆，输入约11KB。

- [x] 为File Planner补充分项计时：charge/input_and_prompt/model/action/commit/progress，覆盖正常、accepted continue、seal与异常；启动计时逐次归档。模型/原生事件不能与包含它们的阶段重复相加。
- [ ] 完成新的6-contract生成与seal，按真实过程比较旧源链，不将旧batch续跑80分钟误报为全6contract从零基线。

冻结前完整回归486项通过。设计与错误实物复现见 `reports/analysis/stateprimitives-fresh-memory-20260921/`。此前5-contract问题已由用户澄清为选择另一较小theory，不创建Capability子集。

### 从零试跑发现的证明策略长尾（待验证/实现）

- [ ] 将候选证明的策略超时与 worker 崩溃分开处理。当前 `BatchWorker.wait` 和 ML 单命令均使用相同 `check_timeout`；Python 若先抛 `TimeoutError`，`proof_batches.check_batch` 会冷检查同一候选，可能重复消耗整个超时窗口。候选超时应保留源码、已执行命令与耗时并返回可修正反馈；仅在明确的基础设施失败时考虑冷检查，且共享候选的总时间边界。
- [ ] 将单命令/候选检查时间与整体 File Planner 决策次数分离，明确配置与反馈；不以此新增 planner 迭代次数上限。验收包括长策略、worker 异常、超时竞态、后续可继续使用合法前缀，以及失败不能产生 GREEN。

观测来源：`stateprimitives-fresh-memory-20260921` 第11步，模型的 `metis` 候选产生分钟级搜索；此前同worker的失败批次约9–10秒。当前仅确认长搜索和代码分支，尚未观测实际超时/fallback。运行实现保持冻结，不将待改进项计为已实现收益。

### 候选超时修复与真实验证

上面长尾项中的超时分类、独立候选预算及避免超时冷重试已实施。CLI新增
`--candidate-timeout`（默认120秒），完整check/seal仍使用独立`--check-timeout`；
ML覆盖候选execute+audit，Python留10秒响应宽限。超时保存RED证据，不进入cold
fallback，也不限制File Planner决策次数。真实worker测试1秒预算在1016ms返回
超时、拒绝commit，并在同一worker随后完成synthetic True（356ms）。
回归493项通过。证据：`reports/analysis/batch-candidate-timeout-native-20260921/`。
第一次从零试跑第11步人工中断，682秒成本保留，未实际触发timeout/fallback；
新实验必须新init、不复用其中2个GREEN，完整生成效果仍待验证。

### 2026-09-22 探索续跑的按需读取保留修复

- [x] 最近一次显式read_attempts/read_queries跨其他动作保留，下一次对应成功读取替换，最多4条；失败读取不清空，旧反馈不覆盖新选择，恢复幂等。完整回归499项通过。

真实复现：exploration续跑54含attempt39后query，55正文消失又读39；56读facts后57又失去attempt39，58再读facts。此前单轮保留诱发反复读取。修复不改变GREEN判据，不增加证明提示。该续跑已实际自主证明一个helper，暂停保留5contract+1helper及全部费用，新实现通过continuation继续。


## 实现审查：待补齐的三个边界问题

本节仅记录检查结果，不修改运行绑定或启动实验。该次审查运行的493项回归通过，
`git diff --check`通过；下述检索与预算问题另用临时目录中的隔离夹具复现，
不代表原生Isabelle验证，也不覆盖随后新增实现与测试。

- [ ] **P1：区分逐theory封存完成与仓库联合验收完成。**
  当前`repository_execution._progress_unlocked`仅按全部theory为sealed/retired生成
  `session_status=complete`，`run_session`返回前未执行独立的整仓联合构建。
  单theory完整检查与provider构建已有实现，此处缺的是任务级集成证据。
  入口：[repository_execution.py](../../execution/repository_execution.py)。
  验收：全部theory封存后，按冻结contract清单核对最终覆盖，对所有生成theory及其依赖
  进行联合构建，保存绑定最终源码和环境的integration证据；集成失败不能报告仓库成功。
  覆盖多个独立分支、联合构建失败和恢复重试，允许单独报告theories-sealed状态。

- [ ] **P2：sealed事实检索目录包含冻结导入的传递闭包。**
  `setup`按直接`ancestors`的canonical-name前缀生成`sealed-premises.json`，但实际
  checker源文件准备遍历传递imports。隔离复现`Target -> Provider -> Base`时，
  Target检索目录只保留Provider事实，遗漏实际导入的Base事实和可复用证明示例。
  入口：[repository_execution.py](../../execution/repository_execution.py)的`setup`。
  验收：依据已验证seal及冻结imports计算闭包；检索覆盖直接与传递provider，排除
  未导入的旁支、未封存节点及当前待证目标；continuation与新运行保持相同可见性规则。
  不把catalog收录当作具体locale/context中的可用性认证。

- [ ] **P2：worker基础设施故障后的cold fallback共享候选剩余预算。**
  已实现的候选TIMEOUT不冷重试规则继续保留；本项针对RuntimeError/OSError等故障路径。
  `check_batch`发生fallback后将`checker.timeout`重新设为完整`candidate_timeout`。
  隔离复现：候选预算120秒，模拟执行110秒后worker崩溃，cold checker仍获120秒。
  入口：[proof_batches.py](../../execution/proof_batches.py)的`check_batch`。
  验收：从候选执行开始建立单调时钟deadline，fallback仅获剩余预算；耗尽则保存失败
  证据并停止该候选。前缀准备、清理和watchdog宽限另行标识，不重置候选预算；
  完整theory/seal预算独立。覆盖执行中途崩溃、预算耗尽及后续合法候选可继续执行。

这三项分别影响任务完成口径、合法知识的检索覆盖和成本控制。它们属于方法落地的
验收与工程修补，不能单独作为论文的主要创新或证明生成效果改进证据。

## 2026-09-22 第一批实施状态

已实现：大诊断显式摘要与按原始证据分页读取；最近反馈去重；模型调用前输入大小检查；异常退出写入 journal、进度投影和终止文件；请求生命周期区分登记、后端尝试、审计完成和动作结果；冻结环境下的进程内 GREEN 声明缓存，独立于候选证明成败。

边界：缓存不跨进程信任历史文件；新声明仍整图检查，增量影响范围检查尚未实现；输入阈值是字符预算，不是决策次数限制；分页是精确诊断片段，尚未实现结构化中间 proof state。保留正常候选验证和最终完整 seal。

第 147 步原始输入离线重放从 1,258,520 JSON bytes 降到 163,602 bytes，未调用模型。真实 Isabelle 合法合成声明首检 16.41 秒，缓存复用 2.40 秒（含环境身份检查）；不将此比例外推到整个 contract。最初 smoke fixture 错将裸命题作为声明，RED 证据保留；改为完整合成 lemma 声明后通过。

第二批仍待实施：工作上下文进一步组织、组合读取、中间 proof state 检索、helper 消费者闭环。第三批仍待实施：批量生成利用率、路线进展评估。完整证明实验未重启，不能据此宣布最后 contract 已完成。

## 2026-09-22 第二、三批实施与步骤复验

- [x] 第二批：显式 workspace 选择；observe 组合读取及独立错误反馈；proof-state 原生导出/历史诊断索引和按目标召回；独立符号解析入口；helper/消费者验证间隔提示。
- [x] 第三批：ready 节点与 helper-consumer 批次提示；逐节点批次结果；引用历史正文重新检查（不是自动接受部分成果）；候选/失败重复及已记录成本信号。
- [x] 保持原子批次提交、固定 contracts、GREEN 不可削弱和最后完整 seal；不引入固定迭代次数或强制 helper 数量。
- [ ] 部分批次自动接受：本次审查后仍不启用。已执行完成不代表通过独立 oracle/axiom 审计；目前通过精确引用后独立重检来复用。
- [ ] 全 theory 端到端完成率/总时间对照：本次只做历史单步/局部原生复验，不能由接口通过推出稳定收敛。

证据目录：`reports/analysis/file-planner-stage2-step-replay-20260922/`、
`file-planner-stage2-native94-20260922/`、`file-planner-stage3-step112-20260922/`、
`file-planner-stage3-decision113-20260922/`。具体结果与限制以各报告为准。

## 2026-09-22 交互简化、声明检查合并与停滞可观测性

本节更新前面条目的实施状态，历史测量和失败记录不覆盖。

- [x] `observe` 允许查询和多个同类读取，独立编号、反馈和查询审计；同批选择不会互相覆盖；恢复与缓存验证包含父反馈绑定。历史95两个残余目标查询加证据读取复验通过。
- [x] 主模型输入应用完整对象去重。固定声明、合法事实、证明及反馈保留唯一完整副本，其余显式引用；`recent_actions`只作导航，不强迫先重新读取已有完整attempt。第113步离线复验确认数学正文逐字不变。
- [x] 所有新增/变更声明均由本批证明覆盖时合并声明与证明检查。未证明声明变更继续单独检查；coverage由validator独立重算，continuation验证原提交链。历史145相同candidate/source再次GREEN，原额外157.329秒声明检查被省略，但冷前缀启动仍需210.771秒，不能声称端到端仅11.816秒。
- [x] batch入口接持久runner事件/心跳并记录终止。Codex stdout/stderr持续落盘，超时及可捕获取消清理所属进程组；部分输出不可seal。终端BrokenPipe不再掩盖已保存进度。
- [x] 独立只读外层Codex会话监测：区分主agent、子任务、未返回工具和终态；仅报告无可观察进展，不推断网络根因，不自动重启/发送请求。
- [ ] 持续工具会话：当前仍是每次模型调用一个JSON动作。需新适配器对白名单工具的调用/反馈做审计绑定，不能直接删掉`no_tool_events`。先在同一合法快照、同模型和checker配置上比较至下一次submit_batch的模型往返、输入量和耗时，再比较完整小theory封存。
- [ ] 同条件完整生成对照：本轮验证是历史候选、工具与输入回放及本地子进程测试，没有新模型证明生成，不宣称已达到VERO或直接Codex的整体速度/完成率。
- [ ] SIGKILL/宿主故障的外部清理：Python finally无法运行，需systemd/cgroup等外部supervisor；legacy supervised_proof_run目前不能声称覆盖batch容器清理。
- [ ] 终端管道长期不消费仍可能阻塞console print：持久日志已优先保存，可通过独立日志/状态文件观察；后续按需改非阻塞console或统一外部supervisor。不要把心跳误报成模型/证明进展。

验证与边界：[本轮报告](../../../reports/analysis/file-planner-speed-20260922/REPORT.md)。
此前主Codex会话42分钟停滞另见 `reports/analysis/codex-stall-20260922/REPORT.md`；不是Isabelle检查持续42分钟，仓库runner改动无法直接修复外层客户端或服务端的请求等待。


## 2026-09-23 实施：重复候选与恢复

- [x] 同冻结上下文的确定性RED复用，显式retry_check保留重跑自由；绑定历史proposal/feedback，timeout不作为逻辑失败缓存。
- [x] proof_edits提供历史草稿哈希校验和唯一文本替换，完整展开候选仍需Isabelle检查；不接受未完成子证明。
- [x] 小型完整剩余目标及失败命令随下一轮输入提供，重复失败路线带请求索引；大目标按原读取接口获取。
- [x] 已确认传输异常退避重试，连续失败可恢复暂停；resume保留计数、证明与审计。
- [ ] 原生持久模型工具会话与跨模型受控对比：本次仍为独立模型决策调用，不能把局部编辑或自动反馈说成已经实现persistent agent session。
- [ ] 按共享执行语义重新评估仓库分组；本轮保持仓库plan和6个contract不变，以隔离harness和模型变更，不导入VERO生成证明。

回放索引：453→459（重复RED）；477→478（精确编辑展开与真实native复验）；478（完整三目标自动投影）；479（已保存超时分类、暂停/恢复控制测试、独立Astra决策）。报告需区分机械回放、模拟传输测试和真实模型/Isabelle调用。


## 2026-09-23：可靠性修复与固定随机样本回放

本批仅修正工具与验收基础设施，不新增证明策略、helper 配额或提案次数限制。

- [x] v2 native audit 先于 GREEN 原子发布；绑定请求、worker、theory、imports、实际 prefix/delta 与事实列表。stdout 不再充当完成握手，历史 v1 验证保持兼容。
- [x] 第39步及 seed=20260923 抽中的12、34、46、53步恢复原上下文配对重放；成功样本在审计日志缺失时仍能正确接受，失败样本仍拒绝且目标一致；七类证据篡改均拒绝。
- [x] resolve_symbols 的底层能力对齐已公布的1–16个名字，准确区分 term 与 fact 查询；26、27及随机68、69、16、43工具/子调用复验，三个随机 read_facts 控制不变。
- [x] audit_content 只保留在持久证据，紧凑反馈保留哈希；真实39步反馈11896→1528字符，五个随机既有反馈不变。
- [x] stop 文件遭遇 ENOSPC 不阻断 worker 清理；清理失败保留后续重试能力。非已识别瞬态 provider 错误不自动重复 dispatch；已识别传输故障仍一次退避重试和可恢复暂停。此项为本地故障注入对照，未人为写满磁盘或调用真实 provider。
- [ ] legacy supervised_proof_run 的外部容器清理仍只覆盖旧名称，未覆盖 regen-batch；当前 batch 主路径不使用该 legacy supervisor，本批不扩展其职责。与上文 SIGKILL/宿主故障外部监督条目一并处理。

最终回归598项通过。抽样池、随机种子、修改前代码、精确候选绑定与全部结果见
`reports/analysis/harness-reliability-20260923/REPORT.md`。原实验和已封存成果未修改。
本批没有重新调用LLM，不能据此推断完整theory生成速度或新prompt下模型策略已经改善。


### 2026-09-25：源码模式与局部 DAG 解耦

- [x] 声明索引独立构建/校验，初始化与正常文档候选不调用旧图构建器。
- [x] 文档接口拒绝旧图修改、submit_check 与 prepare_reuse；共享成果以源码复制后复验。
- [x] 不计算源码模式的消费者/计划边批量建议，模型视图隐藏兼容边与图所有权。
- [x] 保留旧文档证据 hash 回放；imports 循环/就绪检查与单 theory 写入约束不删。
- [x] 明确 Python 协调器派发、File Planner 负责本 theory；同步流程与实现文档。
- [ ] 全量 journal/checker wire-format 改名仅在后续确需 schema 迁移时考虑，不作为本轮运行前置条件。


### 2026-09-25：DAG、并发与名称一致性复核

- [x] 默认 source 流程不存在局部 DAG 排程；保留真实 imports 就绪/循环检查。
- [x] 默认 document 的串行读写、跨 theory 工具重叠、单 native 槽排队轻量回归。
- [x] 修复并行模式 list_workspace 固定 worker_count=1 的状态误报。
- [x] 公共 entry 参数/反馈、Declaration_Check 标签及 declaration-index 进度投影。
- [x] 中性 observation_memory 实现，旧 async 名称仅兼容历史导入/状态。
- [x] 旧异步/图方案就地标明历史，当前主文档、接口和实现一致。
