# Repo-level 长程证明生成：上下文组织、工作区协作与稳定性评估

本文将论文收敛到一个角度：**在有限上下文下，如何组织相互依赖的证明工作区，使局部进展持续积累为整个仓库的进展。** 重点是仓库任务的上下文与协作管理。helper 拆分属于通用证明难题，在本工作中作为工具辅助的增强项，不再承担主要问题定义。

论文的核心矛盾是：**任务具有全局关联，模型的有效推理范围却是局部的。** 把整个仓库持续输入模型，会使规划信息和探索历史过大；把任务分给独立工作区，又可能丢失任务关系和可复用事实。方法需要同时保留局部上下文的可管理性，以及全局任务和知识的连接。

本文于 **2026-11-12** 对照当前实现更新。方法描述以当前仓库及 `ours-refine-astra-low8h-publication-machine-a-20261112-001` 的冻结配置为依据；第4节与附录A保留 Astra 006 的历史证据，不将不同版本、模型和继承起点的数据混为一轮实验。当前 Refine 运行采用 `gpt-6-astra / low`、`worker=1`、`native_slots=1`、`resident_limit=1`、owner-blind 和完整 contracts 文件读取，策略为 `sketch-reviewed-v3`。

**已实现能力不等于已验证收益。** 固定滑动窗口及完整的非 LLM helper 自动发现链路尚未实现；重复运行的稳定性、组件消融和 CRefine 当前配置的实证仍需完成。当前主线是经审查的仓库规划、持久化 theory 工作区中的异步多目标构造、带环境证据的事实召回，以及完整验证与发布。

## 1. Repo-level 相比单 lemma、单 theory 增加了什么困难

| 任务尺度 | 需要完成的工作 | 额外管理责任 |
| --- | --- | --- |
| 单 lemma | 在给定合法环境中完成一个目标，必要时设计 helpers | 管理该目标的搜索和局部上下文 |
| 单 theory | 完成一组同文件目标及局部辅助性质 | 维护文档顺序、共享事实和整个 theory 的检查 |
| Repo-level | 在多个相互依赖的 theories 中完成固定目标集合 | 分配全仓目标、确定上下文边界、跨工作区发现知识、协调前驱与就绪任务、检查整体集成 |

单 lemma 也可能依赖大型仓库，单 theory 也可能有长上下文。区别不在于前两种任务绝对没有依赖，而在于本任务需要同时生成和组织多个工作区的成果：下游需要的生成事实可能尚未完成，当前可用上下文受生成状态和 imports 约束，最终还要确认整个指定目标集合完成。

因此，repo-level 不是把单目标生成简单重复很多次。需要回答两个相互连接的问题：

1. **怎样分批理解全仓任务，形成能交给局部工作区执行的计划？** 初始信息规模很大，相关目标可能分散在不同文件中；读取次序和分组选择影响上下文共享与等待关系。
2. **怎样让局部工作区持续获得需要的知识，并可靠地连接其他工作区的成果？** 探索历史和生成事实不断增长，全部广播会扩大上下文，完全隔离又容易重复证明或遗漏已有支持。

这两个问题统一指向上下文组织与工作区协调。证明本身的数学困难仍然存在，但本文主要研究的是如何让相互依赖的长程任务保持有效进展。

### 1.1 “稳定性”指什么

正确性由完整 native 检查和集成证据确认。本文研究的生成稳定性是：在相同合法输入与预算下，规划、上下文选择和工作区切换带来的波动是否减少，已验证成果是否持续被保存与复用，整个目标集合能否较稳定地推进。

计划合法、日志持久或单次覆盖高，均不单独证明稳定性。需要重复实验、过程证据和机制对照。加速也应同时计入模型、检索、检查和集成成本。

## 2. 三个机制分别解决什么问题

| 机制 | 对应问题 | 如何发挥作用 | 要验证的效果 |
| --- | --- | --- | --- |
| 经审查的仓库规划 | 全仓任务规模大，分组与前驱选择影响可执行性 | 紧凑目录导航、按需读取声明、查询合法背景、审查修订与问题移交 | 规划及修订成本、首次可执行目标、后续等待与重复构造 |
| theory 内持续共享工作区 | 局部探索历史变长，分散尝试难以保持当前证明状态 | 同一工作区维护 contracts、helpers、证明草稿与检查反馈，围绕当前目标持续读取和编辑 | 重复尝试、有效进展保留、首次目标完成成本 |
| 全局 premise 索引与召回 | 当前目标所需知识分散在其他 theory/session，全文广播成本高 | 按需检索合法事实，读取完整陈述与来源，在当前环境中检查使用 | 实际事实引用、跨 theory 复用、召回成本与目标完成收益 |

三者形成分层上下文机制：**全仓任务按需理解，相关局部工作集中保存，外部知识按需获取。** theory 工作区是持续存储，不要求每次调用把整份文档及历史都注入 prompt。具体读取与输入规模仍需测量，不能仅凭有工作区就声称消除了长上下文问题。

### 2.1 经审查的仓库规划：自主读取与可执行性检查

初始规划只使用准备阶段的固定 contract 陈述、owned context、合法上游和允许公开的元数据，不含本轮执行诊断或生成 helpers。目录提供目标 IDs、可见性策略允许的名称/owner 标签及公共 context profiles；完整声明、属性与上下文通过 contract-ID 接口读取，也可在启用时直接读取完整 contracts 文件。本轮开放完整文件读取。`principal_symbols`、`logical_shape` 和 `related` 已不作为当前规划输入或接口。

读取批次由 agent 决定；`plan.json` 和 `NOTES.md` 保存规划产物与笔记。当前没有强制固定窗口、确定性阅读顺序或结构化的累计阅读状态协议，不能把按需查询写成已实现的滑动窗口。见 [读取与公开视图](regen/planning/workspace_planner.py)、[本轮规划政策](reports/regen/ours-refine-astra-low8h-publication-machine-a-20261112-001/config/planning.json)。

规划经过全局审查、问题处置、修订与复审，再执行覆盖、依赖 DAG 和 native 声明/imports 预检。审查闭合要求按问题记录决策；移交给 File Planner 的局部证明义务不等于已解决的数学问题。修订应服务于可执行目标、减少重复构造和无谓等待，其投入需与后续收益共同评估，而不是把更多修订视为更好规划。见 [审查流程](regen/tools/reviewed_planning.py)。

规划中的证明方向及复用候选是未验证建议，File Planner 可以采用、修改或放弃。待证 contract 不是 premise；使用前须检查其证明状态、认证导出与有效 imports。候选不可用时，不能因此弱化固定目标或强制等待。建议也不是每个目标都具有完整证明步骤的保证。见 [建议移交规则](regen/planning/planning_advice.py)。

固定滑动窗口仍可作为对照方案：保留全仓紧凑目录、累计分组与待比较关系，并允许补读修订。窗口是阅读批次而非 theory 边界；需测量顺序偏差、输入规模、规划成本和后续效果。在完成实现与对照前，不将其列为已实现贡献。

### 2.2 theory 共享工作区：使局部探索具有连续性

每个活动 theory 由一个 File Planner 写入负责者管理；正常推进使用持续会话，恢复或切换后不保证原模型会话或原生状态仍驻留。相关 contracts 与 helpers 共处可检查的 `.thy` 文档，模型可以尝试直接证明、添加中间性质、使用已有事实，并根据反馈修订。工作区同时保留草稿和正式接受状态，局部失败后能够继续原有探索。

这里的“共享”首先是同 theory 中多个目标共享证明材料，不是多个模型同时无约束地修改同一文件。Astra 006 的历史实现同 theory 工具串行，不同 theories 可以并行。当前文档检查提交不可变快照后异步返回，模型可继续读取或编辑其他未保留条目的 proof body；被检查条目、accepted proofs、imports、声明和 sketch 元数据受保留规则约束。单 native 槽不意味着模型在检查期间必须停工。当前单 worker 实验也不提供多 agent 并行或异构协作收益的证据。

模型独占局部修改权、source hash 绑定和原子接受减少跨会话写入干扰；正式状态和文档保存已有工作。其作用是提供明确、连续的探索载体，不保证每条证明路线可达，也不保证每个 theory 的模型输入都足够小。

多目标 `check_theory` 可以选择多个条目，其他未通过目标在隔离检查中使用 `oops`。普通失败不应跳过后续独立条目，结果按条目返回并独立接受；修改既有 GREEN 则需要对应上下文重验证。`switch_target` 支持切换当前目标或让出工作区给就绪 theory，草稿仍保留。检查任务 ID 和草稿保存均不构成 GREEN。见 [当前工作区协议](regen/planning/parallel_file_planner.py)。

持久化文档、模型会话连续性与原生前缀复用必须分别报告。让出 theory 会释放 resident worker，恢复时可能重建认证前缀；历史证明链重放也有成本。因此当前能力是可核验地保留状态，不是零成本恢复或始终驻留内存。

### 2.3 premise 召回：用事实接口连接工作区

局部会话无需阅读其他会话的全部对话，而是按当前目标检索事实，读取陈述、来源和可用状态，尝试实例化与使用。已有 facts 帮助减少重复构造，新成果则在符合接受、seal 和 imports 边界后供后续任务使用。

索引命中不认证适用性。当前 theory 必须满足前提和合法导入条件，最终使用由 native 检查确认。未封存或未导入的生成材料只能作为参考，不能直接作为已成立前提。可复制为本地 helper 并重新证明；已认证导出的 provider 也可通过合法 imports 修订引入。未发布 theory 的 import 草稿在下一次 native 检查时应用，并验证 provider 版本、固定目标语义和已有证明；已发布 theory 不可修改 imports，已有反证证书还受其环境绑定限制。

“全局”指覆盖实验允许访问的合法事实范围，不包括被移除的目标证明和辅助材料。跨 Isabelle session 使用受冻结上游约束；跨模型会话复用受正式接受状态约束，两种 session 含义需在论文中区分。

目标关联的检索记忆保留查询、候选与使用证据，历史候选需要按当前环境判断是否仍可用。检索范围可以宽于当前 imports；结果需区分可发现、需要导入、已认证可见和数学上实际适用。记忆不恢复已失效的事实缓存，也不把一次失败永久解释为定理无用。见 [检索记忆](regen/planning/retrieval_memory.py)、[可见性说明](regen/planning/premise_visibility.py)。这些能力尚不证明检索已减少整体证明成本。

## 3. 协作框架如何把三个机制接起来

```mermaid
flowchart TD
    I[固定 contracts 与合法上游] --> P[目录导航与自主读取：形成仓库计划]
    P --> V[审查、修订、复审及覆盖/DAG/native 预检]
    V --> S[按有效依赖与资源选择就绪 theory]
    S --> A[File Planner：持久文档、建议与审查义务]
    K[静态与生成事实召回：记忆及环境证据] --> A
    A --> C[异步快照检查：逐目标反馈与接受]
    C -->|反馈修订；条件拆分可选 sketch| A
    A -->|切换目标、等待 provider 或让出 theory| S
    A --> M[imports 草稿修改]
    M --> N[下次检查验证环境并重新认证证明]
    N --> A
    A --> F[完整 theory 检查、依赖审计和 build/seal]
    F -->|认证导出| K
    F --> S
    F --> R[固定目标集合最终集成]
```

检查器、索引和调度器是服务，不是额外推理模型。一个写入负责者可以在异步检查期间处理独立条目；跨 theory 的实际并行度仍受 worker 配置限制。事实召回不自动添加 imports，规划候选也不自动建立证明前提。

保持 theory 为完整验证与发布单元。单项目标通过后仍需支付完整检查、依赖审计和构建成本；一个困难目标仍可能阻塞该 theory 向下游发布。当前发布路径保留已认证条目顺序，并允许发布失败时保留证明与草稿、结束该局部会话，避免一直占用生成流程。该能力不保证所有失败会自动得到修复。见 [发布准入](regen/planning/publication_admission.py)。

受验证的 imports 修订已是当前能力，应与动态拆分发布、任意全仓运行时重规划区分；后两者不能作为当前已完成能力概括。

### 3.1 为什么可能更稳定或更快

机制预期是：经审查的任务组织改善可执行性；局部持续工作区降低重建和重复探索；召回降低外部全文输入与重复构造；独立负责者和检查边界使协作状态明确。不同就绪 theories 的模型推理可以并行，但 native 检查并发是独立限制。

Astra 006 有两个 theory 会话、一个 native 槽和两个驻留 workers。不能用两个会话推出两倍加速；需要分开测模型时间、检查等待、上下文输入成本和总墙钟。工作区隔离及 seal 也有成本，论文报告其收益与代价。

## 4. Astra 006 历史实验能支持哪些仓库级证据

### 4.1 实验身份与实际协作方式

Astra 006 沿用 [001 的计划](reports/regen/refine-astra-medium-20260930-001/planning/file-generation-list.json)：287 个 contracts、30 个 theories、48 条 required import edges、7 个 collaboration groups 和7 条 reuse suggestions。只有 required imports 形成硬前驱；建议组不建立证明事实或额外 barrier。

模型为 `gpt-6-astra / medium`。每 theory 的同一 File Planner 会话承担局部规划、helper 创建、proof 和修订；配置中的 Lemma Prover/GRA 名称不意味着每次检查调用独立模型。执行依据见 [并行政策](reports/regen/refine-astra-medium-20261005-006/proof/parallel-workspace-policy.json)、[冻结会话实现](reports/regen/provider-bootstrap-repair-20261005/resume-application/regen/planning/parallel_file_planner.py)。

最终接受 199/287 contracts，885 个 helpers，13 个 theories sealed；起点继承 194 个 contracts，本轮新增 5 个。未接受的 88 个中，66 个从未初始化，22 个在已初始化 theories 中。006 是 continuation，未完成全仓 integration，不能作为从零完整生成或稳定性对照成绩。

覆盖与案例证据：[evidence.json](reports/regen/astra006-long-horizon-analysis-20261012/evidence.json)、[extract_evidence.py](reports/regen/astra006-long-horizon-analysis-20261012/extract_evidence.py)、[运行绑定](reports/regen/refine-astra-medium-20261005-006/proof/run.json)。

### 4.2 局部工作区之间确实存在共享知识需求

最终13 个 sealed theories 的完整 native 审计覆盖173 个 contracts 和732 个 helpers，共905 个 targets：

| 指标 | 结果 |
| --- | ---: |
| 跨 theory named dependency edges / theory pairs | 260 / 17 |
| 直接使用跨 theory 生成事实的 contracts | 30/173，17.3% |
| 沿生成事实依赖链包含跨 theory 依赖的 contracts | 94/173，54.3% |
| 直接使用生成 helper 的 contracts | 119/173，68.8% |
| 沿生成事实依赖链使用 helper 的 contracts | 145/173，83.8% |
| 被至少两个 contracts 直接使用的 helpers | 70 |

`heap_tcb_relation` 被6 个 contracts 使用，涉及6 个 theories；`asid_machine_op` 被6 个 contracts 使用，跨 ASID 与 Mapping。事实共享有实际发生，是仓库任务的实证基础；它尚不证明某个规划或召回策略造成了收益。

数据来自 Isabelle `Thm_Deps.thm_deps` 的命名节点依赖，非源码出现次数；间接指标另对生成事实图计算可达关系。26 个 accepted contracts 位于未 sealed theories，不属于这173 个的完整审计范围。复核：[依赖分析](reports/regen/astra006-repository-reuse-20261013/analysis.json)、[脚本](reports/regen/astra006-repository-reuse-20261013/analyze.py)。

### 4.3 召回参与证明，但因果收益仍需验证

006 两个活动 theory 会话中，有56 个 standalone queries 和21 个 observe 内 query 子动作，合计77 个 query actions，以及2 个事实读取动作。一次动作可能含多个查询串，不等于一次成功检索。

累计 query index 的909 个召回 premise canonical names、102 个召回生成事实与 native 依赖重合。该交集不证明检索先于使用，也不全部属于006 的新增调用。一个直接轨迹是 Scheduler 主会话第479 行返回 `Finalise_AI.gts_wp`，最终 accepted activateThread proof 使用 `wp gts_wp`。该 theory 尚未 sealed，这是过程与接受证明的对应，不是完整 theory 审计。

## 5. 怎样评价稳定性与效率

### 5.1 两次独立 session 实验

Refine 与 CRefine 分别冻结 contracts、合法上游、证明移除规则、运行身份和成本账本。**CRefine 使用冻结、已验证的既有 Refine 上游，不使用本工作生成的 Refine 成果，也不等待 Refine 生成实验完成。** 两次实验内部可以跨 theory/session 使用合法事实，但不是联动生成两层验证链。

CRefine 的 adapter、sanitized packages 和 native 验证路径须按当前实验版本单独确认；本文尚未给出其当前版本的完整运行证据，不能把历史“待准备”说明当作最新实现盘点，其目标数与结果也不能照搬 Refine。每次任务的“完整成功”均要求规定的全部目标闭合、theories 完整检查和最终集成通过。不得通过历史、索引或检索重新引入禁止的目标证明或被移除辅助材料。

### 5.2 围绕主线收敛实验问题

| 研究问题 | 主要比较 | 主要指标 |
| --- | --- | --- |
| RQ1：仓库是否持续推进，效果是否稳定？ | 相同输入、模型和预算下重复完整运行 | 覆盖随成本的曲线、最终覆盖分布、sealed 数与集成状态 |
| RQ2：规划与修订是否改善后续执行？ | 固定输入下的规划/审查对照；窗口化作为未来可选消融 | 规划与修订成本、实际输入规模、就绪目标、后续等待及重复证明 |
| RQ3：持续工作区和召回是否改善协作？ | 可比的状态保留与召回对照 | 重复尝试、首次目标完成成本、事实实际引用、跨 theory 复用 |
| RQ4：非 LLM helper 支持是否有附加收益？ | 相同主框架下启用/关闭工具辅助 | 候选筛选成本、困难 contract 完成率、端到端成本 |

前三项是核心实证，第四项是增强实验。规划重复与证明重复分开：前者固定初始输入重复规划，后者固定计划重复证明。关注成功分布与较差运行表现，避免只报告最好的一次。理论分组一致但执行差，不算稳定性改善。

成本分开记录模型、检索、native 检查、资源等待及集成，并报告首次产出和达到给定覆盖的成本。部分检查错误属于基础设施，部分目标未执行；`not_reached`、直接失败和未初始化分别报告。受控恢复可作为状态连续性的补充测试，当前证据不支持任意主机故障恢复保证。

已有历史 Vero/Codex 案例见附录A，不能作为主框架稳定性或加速的公平对照。新增的 VERO-style Isabelle 与通用 Codex 运行应另列实验身份：前者是官方 VERO 工作流提示的 Isabelle 适配，不是原版 Lean 实验的逐字复现；两种基线采用共同任务规则与检查工具，但方法 prompt 不同。必须公开这些差异，而不能仅凭模型和 CPU 相同认定所有控制变量一致。

### 5.3 当前迭代证据与尚未完成的评估

本轮8小时检查点为53个 GREEN contracts、36个已导出 contracts、3个 sealed theories，未完成全仓集成；之后授权追加40小时，总有效额度48小时。以上是追加前检查点，不是正在运行实验的最终结果。前4小时与上一轮的比较见 [成本与覆盖快照](reports/regen/ours-refine-astra-low8h-publication-machine-a-20261112-001/four-hour-comparison-20261112.json)。版本、规划分组和目标顺序有变化，改善只能作为组合改动的观测，不能分配为各组件的因果收益。

VERO 首4小时通过目标中存在明显的早期容易目标集中现象，也包含较重的辅助引理证明。其目标集合与 Ours 不同；应同时报告共同目标成本、全体覆盖曲线、已导出数量和未到达目标。短主证明可能依赖昂贵 helpers，不能按行数独立判定难度。见 [成功快照审计](reports/regen/refine-cross-method-progress-20261111-001/vero-difficulty-first4h-20261112.json)。

当前还没有足够的重复完整运行和机制消融来支持“稳定性改善”结论。规划方向、召回、异步检查及 sketch 的收益需要分别验证。一次恢复成功也不构成任意故障恢复保证：本轮追加预算时，旧复制续跑入口拒绝了新计时策略，最终使用原工作区计时器正式追加并恢复；应修复和测试兼容性，不能把它描述为透明恢复。

当前有效时间策略计入正常规划、生成、检查、普通证明超时和集成开销，排除有证据的暂停、离线恢复和基础设施中断。报告同时保留实际墙钟及排除明细，避免只展示有效时间而隐藏恢复成本。

## 6. 增强项：非 LLM 工具辅助 helper 设计与筛选

当前 `sketch-reviewed-v3` 将普通 helper 与条件分解分开：普通 helper 可直接通过 `check_theory` 证明；显式 sketch 组才需要消费者条件闭合与对应审查。`conditional_closed` 只证明假定这些 helper 后消费者能够闭合，不证明 helper 自身成立，也不把父目标变为 GREEN。正式子证明及父目标实际检查仍必需。见 [策略定义](regen/planning/generation_policy.py)、[消费者与 probe 工作流](regen/planning/sketch_workflow.py)。

当前运行使用 semantic review 分支，不能把历史 guided/phased 策略的结构评分、强制短 probe 次数和阶段转换规则全部写成当前必经流程。分解候选主要由模型提出，native 工具负责验证；这不等同于非 LLM 自动 helper 发现，也不属于 Astra 006 的历史执行流程。

helper 设计是证明生成中的长期难题。本文保留它作为局部证明能力，重点考虑让工具承担可机械处理的部分，减少模型任意猜测和重复投入；不要求解决所有抽象发现问题来支撑仓库主线。

| 可考虑的手段 | 非 LLM 部分负责什么 | 仍需模型或完整证明解决什么 |
| --- | --- | --- |
| 从真实残余目标提取候选 | 由 prover 状态保留参数、类型和局部假设，生成对应子义务候选 | 哪些义务值得独立封装，以及怎样形成可复用抽象 |
| 规范化与已有事实匹配 | 对候选做项级规范化、去重、已知事实匹配 | 语义等价和复杂蕴含不能靠字符串或符号相似认证 |
| 有限自动化预检查 | 尝试受预算限制的 simp、算术或已有规则，得到实际证明结果 | 自动化未完成的目标仍需进一步推理；超时不是反证 |
| 反例检查 | 对适用候选用 Nitpick 等寻找反例，提供修订信号 | 无反例不代表成立，潜在/近似反例需按工具结果区分 |
| 父目标使用检查 | 尝试将已证明 helper 接入真实目标，记录剩余义务 | helper 成立不保证父目标完成；缺失条件和路线改变仍需判断 |

建议首版优先做**真实子目标提取、规范化去重和有预算的自动化预检查**，在现有文档/checker 链路中提供候选与证据。候选必须来自真实、版本绑定的 prover 状态，保留所有必要假设，重新检查独立声明，最终仍由完整 proof 和 theory 审计确定成立。

提取子目标不自动产生更好的抽象：如果将原目标换个名字，或携带目标中无法获得的额外假设，仍没有改善证明。候选需绑定服务目标和预期用法；工具反馈可以辅助模型选择，而不能把未证明 helper 当作 premise。

Isabelle 的 [Nitpick 官方手册](https://isabelle.in.tum.de/doc/nitpick.pdf)说明其反例搜索能力；它不是证明器，搜索范围内无反例不能替代证明。只有确认当前环境支持且适用的工具才纳入实验，并将预检查成本计入总投入。

代码现状需区分：项目的 [automation_advisor.py](regen/planning/automation_advisor.py)包含规则匹配和自动化候选建议，不等于自动 helper 发现；[helper_selection.py](regen/planning/helper_selection.py)保留历史 AND/OR 成本搜索代码，但当前控制器已停用 `select_helpers`，不能写成活动基线的能力。旧路线估计还依赖模型输入。当前条件检查与 probe 不等于以上自动发现链路；本次文档更新不启用任何策略或修改运行中的实验。

helper 详细成本、消费者与失败案例已有独立文档：[HELPER-CASE-STUDIES.md](reports/regen/astra006-long-horizon-analysis-20261012/HELPER-CASE-STUDIES.md)。主文只选少量例子解释工具辅助的作用，不展开全部数学证明路径。chooseThread/activateThread 展示组合和性质传播；deleteObjects 展示中间性质发现；schedule/reset 展示局部成果尚未兑现为目标闭合。各自证据仍见 [evidence.json](reports/regen/astra006-long-horizon-analysis-20261012/evidence.json)。

## 7. 论文故事、贡献与结构

### 7.1 引言主线

> Repo-level 长程证明生成需要同时完成多个相互依赖的证明工作区。与给定环境中的单目标或单 theory 生成相比，系统还必须组织全仓任务、持续选择局部上下文，并让分散产生的证明成果在合法边界内被复用。全文输入使上下文与历史不断增长，独立工作区又可能丢失关联知识。我们围绕这一矛盾构建分层上下文与协作框架：经审查的仓库规划组织初始任务，持续 theory 工作区通过异步多目标反馈保留局部进展，带环境证据的 premise 召回按需连接外部知识，完整检查与封存确认成果和整体完成。评估将区分覆盖、发布、成本和重复运行的稳定性，条件分解作为可选增强；固定窗口与非 LLM helper 自动发现仍是待验证方向。

这是拟完成工作的介绍逻辑，不是对当前结果的结论。题目强调稳定性，最终须有重复实验和机制对照；尚无此证据时，应报告框架能力与已有仓库复用，避免把预期收益写成已验证效果。

### 7.2 三项贡献

1. **面向 repo-level 长程任务的上下文与协作框架。** 将全仓任务组织、局部工作区和事实共享连接起来，明确多会话的任务负责者、可用上下文与验证边界。重点是仓库推进，不将通用 helper 分解当作主要差异。
2. **分层上下文机制及其稳定性、效率评估。** 按需规划及审查、持续工作区和全局召回分别处理全仓任务、局部状态与外部知识，研究它们对波动、重复工作和成本的影响。组件存在本身不构成收益证据。
3. **独立仓库任务与可复现实证（待完成）。** 公开合法输入边界、harness、运行配置、生成产物及过程证据，分别报告整体覆盖和集成；Refine/CRefine 及人工翻译数据集须各自列出准备状态、模型边界与实际结果，不以旧适配器或部分短测代替完整实证。条件分解及未来工具辅助按实际实现描述。

### 7.3 推荐结构与取舍

| 章节 | 保留的重点 |
| --- | --- |
| Introduction | repo-level 相比单 lemma/theory 的差异，上下文与协作矛盾，贡献 |
| Background and Problem | 固定目标、合法 facts、工作区边界、规定目标集合完成与稳定性指标 |
| Method | 按需规划与审查、异步多目标工作区、环境绑定召回、imports 修订及验证发布；简述可选条件分解 |
| Experimental Setup | 各独立数据集、证明移除边界、适配基线 prompt、模型、预算、对照与重复设计 |
| Results and Analysis | 核心RQ、跨 theory 复用、稳定性与成本；少量 helper 增强案例 |
| Related Work | 单目标/单 theory 证明、仓库上下文、协作规划；helper 发现与工具辅助 |
| Limitations and Conclusion | 规划和检索误差、未完成目标、资源和状态边界 |

运行恢复细节、全部工具协议、角色历史、所有 helper 的数学路径和成本明细留在附录或开源资料。主文每个机制都解释“处理哪类上下文、怎样连接工作区、哪项实验验证作用”，不追求覆盖项目的所有功能。

## 8. 文献与证据边界

[VeriSoftBench，2026](https://arxiv.org/html/2602.18307)保留真实仓库上下文和跨文件依赖，并比较相关上下文与全仓上下文。它支撑上下文组织的重要性，但任务仍是从仓库抽取的证明义务，不能把它的实验等同于同时生成完整证明项目。

[Co-Coder，2026](https://arxiv.org/html/2606.00953)讨论仓库代码任务中分组、依赖和跨 agent 协调成本，提供工作区划分与协作的设计启发；其代码生成收益不直接证明本框架的证明生成收益。[Stepwise，2026](https://arxiv.org/html/2603.19715)提供系统证明中的多步探索背景，但局部搜索结果不能代替仓库上下文机制的消融。

helper 背景可引用 [Goedel-Architect](https://arxiv.org/abs/2606.06468)、[IDS](https://arxiv.org/abs/2605.23109)；[LEGO-Prover](https://arxiv.org/abs/2310.00656)是较早共享 lemma 库参考，非2026新论文。这些工作解释分解与复用的相关方向，本文主张仍由自身实验支持。

原始过程入口：[Scheduler 会话](reports/regen/refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Scheduler_Dispatch/session-transcript.jsonl)、[Untyped 会话](reports/regen/refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/session-transcript.jsonl)。模型叙述用于解释策略，checker 和正式接受记录确认结果。

## 附录 A. 历史 Vero/Codex 选定目标比较

### A.1 核查范围与结果

此前只读核查机器 B 上两个指定运行，并读取旧 Codex Astra 档案。以正式 acceptance 或 Vero evaluation 为准，不用模型声称“完成”的文字代替验证。

| contract | Astra 持续文档 006 | Vero Sol 48h 004 | 普通 Codex Sol 009，续接 007 | 旧 Codex Astra 档案 |
| --- | --- | --- | --- | --- |
| activateThread_corres | accepted | open；评估 unfilled | 未 accepted，无目标 batch 记录 | 未 accepted，theory 未初始化 |
| chooseThread_corres | accepted | open；评估 unfilled | 未 accepted，无目标 batch 记录 | 未 accepted，无目标 batch 记录 |
| deleteObjects_corres | accepted | open；评估 unfilled | 未 accepted，13 次目标直接 failed | 未 accepted，无目标 batch 记录 |
| schedule_corres | 未 accepted | open；评估 unfilled | 未 accepted，无目标 batch 记录 | 未 accepted，无目标 batch 记录 |
| resetUntypedCap_corres | 未 accepted | open；评估 unfilled | 未 accepted，无目标 batch 记录 | 未 accepted，无目标 batch 记录 |

“持续文档化解决的 contract”在这里准确指：持续文档运行接受的案例，而不是已经通过消融证明由文档化这一组件导致成功的案例。

Vero 路径：`/data/zljj/vero-a-unified/vero-active-wall-sol-20261010-004/vero-sol-medium-active-wall-48h-004`。最新 `result.json` 为 `stopped / active_wall_budget_reached`。`run/iterations.json` 指定的 best iteration 为 004；该评估证明 13/287 个 specs，所选五项均为 `unfilled`，iter-006 的选定目标结果相同。不能沿用较早暂停快照中的 12 个 green 作为最新最佳评估数量。

Vero 的目标映射为 activateThread `c0217`、chooseThread `c0148`、deleteObjects `c0060`、schedule `c0161`、reset `c0252`。其 summary 中 Schedule 模块有 45 条检查记录，目标列表包含 chooseThread/schedule，outcome 为 44 次 `ok`、1 次 `build_error`。但最佳评估仍将两项目标标为 `unfilled`，因此 module 检查的 `ok` 不能算作这两个 contract 的证明成功，也不能把 45 次都算作本体尝试。Tcb、Detype、Untyped 三个对应模块的检查记录为 0。因此至少这些后三模块的未完成不能解读为“充分尝试后证明失败”。此外，iterations 元数据记录 `formal_takeover_count=3`，本文仅报告现存评估结果，不把 13 个 specs 当成已核清干预来源的全自动横向成绩。

普通 Codex 路径：`/data/zljj/vero-a-unified/codex-active-wall-sol-20261012-009/codex-sol-medium-active-wall-48h-009`。这是 007 的延续，模型为 Sol medium，接受状态和历史成本继承；此次读取当前 commits，五项均不在 accepted order。其中 deleteObjects 在请求 1、2、4、6–15 的 `node_results` 中共 13 次直接 `failed`，是目标级失败证据；其余四项没有相应 batch history 或已接受 proof records。对应 modules 的其他历史不能归到这些目标。目标相关记录保存在下面的快照中。

旧 Codex Astra 档案来源为 [contract-statistics.json](reports/regen/restart-readiness-20261006/history/contract-statistics.json)，其累计结果是 130/287，包含历史重规划和恢复，与本地 006 的 199/287 不是同一运行投影。上述四项没有 batch membership，activateThread 所在 theory 未初始化。它提供“该档案中尚未接受”的事实，不提供这些目标上同模型的失败对照。[选定条目](reports/regen/astra006-baseline-contract-comparison-20261013/old-codex-astra-selected.json) 已另存，便于核查。

复核材料：[机器 B 结果快照](reports/regen/astra006-baseline-contract-comparison-20261013/remote-snapshot.json)、[只读采集脚本](reports/regen/astra006-baseline-contract-comparison-20261013/extract_remote.py)。快照记录读取时刻、commit 身份、报告与源文件哈希；普通 Codex 运行还可能继续推进，因此结果仅代表该快照。采集时没有启动、恢复或修改任何生成实验，也未穷举全部历史 Vero/Codex 运行。

### A.2 比较结果的限制

可以报告：**Astra 持续文档运行接受了这三个困难案例，所核查 Vero/Codex 快照尚未接受它们。** 不能由此推出文档化造成了优势。主要混杂包括模型不同、继承起点不同、theory 组织与推进顺序不同、输入上下文和总投入不同，以及部分基线尚未到达所选目标。

更有说服力的对照应在相同前序接受状态上，把这些困难 contracts 分别交给持续文档和对照策略，固定模型、合法 premises、native 并发和总预算。除了最终成功率，还应记录：消费者组合检查次数、接口修订、helper 在父证明中的实际依赖、到首次父目标闭合的成本，以及未到达/未执行目标。

如果要单独评估持续文档机制的价值，可比较：只证明 helpers 的策略，与持续保留消费者 proof、依据残余目标修订接口的策略。保留相同检索能力，避免把召回收益误计为文档收益。helper 数量、局部 green 数和长对话本身均不构成效果指标。
