# 仓库规划实现

2026-09-25：当前端到端控制流及本模块 TODO 见 [M1 仓库规划](../design/PARALLEL-PROOF-PIPELINE.md#m1) 与 [M2 规划验收](../design/PARALLEL-PROOF-PIPELINE.md#m2)。owner-visible/blind prompt 已按可见性策略修正，规划预检已接统一执行器；实际模型生成及架构质量评价仍需实验。

当前默认链路只支持独立的 Refine 实验。Refine 与 CRefine 是两份数据集；Refine 的输入、
premise 库、plan、运行状态和评价均不读取 CRefine，也不继承另一实验的生成结果。

## 1. 数据准备

入口为 [planning_input.py](../../preparation/planning_input.py)：

```bash
python3 -m regen.preparation.planning_input \
  --l4v verification/l4v \
  --contracts <prepared-contracts.jsonl> \
  --premises <broad-parent-export.jsonl> \
  --output <refine-planning-input>
```

准备模块直接消费已经去除 Refine 普通人类证明的 seL4 树，不创建或暴露 environment ID。
它输出：

- `repository-planning-input.json`：完整 contracts、紧凑 contract cards、ROOT session closure、
  Refine theory import graph、context profiles 和确定性 relation candidates；
- `parent-premises.sqlite`：只读父 session premise 库；
- `parent-premises-manifest.json`：记录数、内容身份和过滤政策。

旧 premise export 的 `owner_session` 实际可能是 theory 标签。准备模块不信任该字段，而是用
ROOT 搜索路径将 `owner_theory` 重新归属到 session。只保留 Refine 的严格上游 closure；
Refine/CRefine 来源以及无法确认归属的记录均排除，并在 `selection_audit` 中计数。
输入校验同时拒绝重复 contract、非 Refine contract、proof/source 字段和过大文本。

## 2. Planner 入口与旧迭代接口

[repository_planner.py](../../planning/repository_planner.py) 的直接入口是 `--planning-input`。
默认策略使用 `interaction: agent-workspace`：agent 在隔离工作区编辑计划文件，通过可信
harness 查询证据、验证和提交，详见第 9 节。当前 workspace 政策为 gpt-6-astra / medium，既有冻结运行仍遵循原模型绑定。
仓库规划不提供 proof state 查询；该召回接口属于后续证明阶段。

下面的 JSON 逐轮接口保留供冻结实验重放，策略样本位于
`regen/fixtures/repository-planning-policy-iterative.json`。这个旧模式通过以下只读接口取得证据：

- `inspect_contracts`：按最多 32 个 ID 返回完整 statement/context；
- `search_parent_premises`：按合法 context profile 检索，单次最多 50 条；
- `inspect_parent_premises`：展开已返回 ID 的完整记录；
- `resolve_symbols`：在父 premise 索引中报告唯一、歧义或未找到；
- `validate_repository_plan`：由结构 validator 实现，不交给模型自报。

旧模式使用 `interaction: agent-iterative`。Agent 从紧凑契约 cards、关系候选和上下文摘要开始，
自主选择检查、检索、验证、修改、提交或停止；不限制检索轮数或提案修改次数，也不要求先完成
固定的两阶段 inspection 流程。单次请求仍有分页上限，避免一次注入整个数据库。

Agent 通过 JSON 操作接口调用 harness：

- `inspect_contracts`：读取完整契约；
- `search_premises` / `inspect_premises`：按名称、定义、概念检索并展开已有引理；
- `resolve_premise_names`：解析已有公开事实名称，不声称完成 Isabelle 常量 elaboration；
- `validate_plan`：检查当前提案，返回具体错误及遗漏、重复、未知契约 ID；验证通过也不发布；
- `submit_plan`：再次验证并提交提案，可省略 proposal 使用当前草稿；
- `read_turn`：读取某一历史轮次的完整请求与结果；
- `save_notes`：保存 agent 自己的工作笔记，在后续轮次持续提供；
- `stop`：说明原因并停止，不发布未完成的规划。

操作格式为 `{"action":"validate_plan","arguments":{"proposal":{...}}}`。
Theory 名称与 helper decision ID 必须匹配 `[A-Za-z][A-Za-z0-9_]*`，不允许连字符；
helper ID 在整份提案中唯一。修正草稿必须通过 `arguments.proposal` 提交完整修订对象；
空参数 `submit_plan` 只重新验证当前草稿，`read_turn` 只读历史，且 turn 必须为整数。
仓库规划不提供 proof state 查询；proof state 召回仍属于后续证明阶段。
Agent 可以在验证失败后补充检索，也可以在验证通过后继续改进分组和调度。
传输超时仍按原请求重试一次，两次连续超时暂停；这不属于提案修改计数。

每轮 transcript 与 `turns/NNNNNN.json` 保留请求、完整结果、草稿和响应摘要。模型上下文携带
当前草稿、工作笔记、累计已检查的完整契约和引理、最新结果及紧凑固定输入；历史请求和失败响应
不重复累积，需要时通过 read_turn 读取。中断恢复沿用已提交轮次的原始 prompt，避免基础设施修复
导致已完成的模型调用被重复执行。
中断恢复重放已审计的完成调用并继续下一轮；只有最终 `submit_plan` 通过后才发布 plan 和 projections。
旧的有限次数双阶段路径仅用于已有冻结策略的兼容重放，新默认策略不含 `max_attempts`。

## 3. Repository plan

当前 workspace 政策的 proposal、plan 与 projection 使用 schema 6，file-generation-list 使用 schema 7，增加 advisory collaboration_groups/reuse_suggestions；旧 schema 4/5 仍按原语义兼容读取。它包含：

- evidence-backed `contract_relations`；
- 每个生成 theory 的完整 contract 分配和 context profile；
- `reuse_existing_fact` 或 `propose_auxiliary_intent` 两类 helper 决策；
- 显式 theory provider 边、ready tie-break priority 和简短理由。

Repository Planner 不得生成精确 helper statement、proof、tactic 或 contract-level DAG。
复用结论必须引用本次规划过程中已 inspect 的合法 premise ID；新的 helper 在本阶段仅为 intent。
[theory_plan.py](../../planning/theory_plan.py) 检查 contract exact-once、证据 ID、context profile、
theory DAG 和 priority，并生成 `repository-plan.json`、`file-generation-list.json` 与
`files/<theory>.json`。

## 4. Plan 身份与 revision

每次独立运行由 [plan_revisions.py](../../planning/plan_revisions.py) 创建一个稳定 `plan_id`。
权威历史位于：

```text
planning/<plan_id>/revisions/0001.json
planning/<plan_id>/HEAD.json
repository-plan.json                 # 当前投影
```

同一运行用递增的日志序号 `revision` 记录计划快照，不再生成 `revision_id` 或 `parent_revision_id`。日志保存内容校验值；投影通过稳定 `plan_id`、日志序号和已有内容摘要绑定。相同计划重放复用原日志，发布中断后可补齐投影，不追加 revision。
已经启动的 theory 绑定其 revision，后续 revision 不能改变或删除该 theory 及其 incident edges。
恢复沿用同一个 plan；新的独立运行取得新的 plan ID。普通更新不使用 `supersedes_plan_id`。

## 5. File Planner 与执行交接

File projection 只冻结该 theory 的 contracts、context profile、显式 provider theories、已检查的
父 premise IDs 和辅助 intents。File Planner 在 theory 第一次 ready 时运行一次，补出精确的
局部 statements 与 DAG。每个生成节点必须记录 semantic role、reasoning gap、intended
consumers，以及 premise evidence IDs 或明确查询。初始图经真实 statement elaboration 后提交为
revision 1；此后只有 Graph Revision Agent 能修订图。

直接 file list 使用 schema 5，并把每份 projection 绑定到同一个 plan revision。Runner 只导入
显式且已 seal 的 provider theories。`--max-parallel-theories N` 可并行执行 DAG 中同时 ready 的
theories；每个 theory 使用独立 checker 工作目录和 heap 副本。发布仍只发生在完整 theory seal
之后，生成 premise 不写入不可变父数据库。

## 6. 运行方式与兼容路径

```bash
python3 -m regen.planning.repository_planner \
  --planning-input <refine-planning-input/repository-planning-input.json> \
  --output <repository-plan-output> \
  --allow-external-call
```

`--prepare-only` 只验证并保存输入，不调用模型。`--planning-input` 不能与旧环境参数混用。
基于 environment gate/dataflow 的 schema 2/3 路径暂时保留，只用于已有冻结实验的兼容重放；
它不是新 Refine 实验的默认接口。schema 5 执行器读取数据集根目录的 `checker-context.json`
和 `checker-assets/`，不读取 environment gate 或 environment ID。`checker_context.py` 可把已有
不可变 toolchain/heaps 以链接方式一次性纳入该目录，避免复制大型资产；旧 schema 兼容代码
仍保留，但不再是新 Refine 运行的依赖。

主要测试为 `test_planning_input.py`、`test_plan_revisions.py`、`test_theory_plan.py`、
`test_repository_planner.py` 和 `test_repository_execution.py`。

并行 theory 使用独立的容器名称、generated 构建目录、ROOT、日志和 heaps；启动前复制冻结 provider 的传递导入源文件，并在本地构建 provider session。仅 sealed 源文件发布到运行级 generated 目录。

首次运行使用 `python3 -m regen.tools.repository_planning_retry --planning-input … --policy … --output … --allow-external-call`。直接入口也使用同一传输重试逻辑：每个逻辑请求超时后按原请求重试一次，两次连续超时暂停；成功重试丢弃前次部分响应并保留时间与用量摘要。重复元数据以表格和符号索引编码，完整契约陈述保持不变。`planner-policy.json` 与输入一起冻结。

New preparations finalize their premise catalog through the native-verified
[schema v2 builder](PREMISE-DATABASE-V2.md). Planner inspection supports the
minimal schema and verifies the catalog hash; proof/GRA rounds can explicitly
retrieve candidates from their goal/context via `proof_state_query`.

迭代控制器及回归测试分别位于 `regen/planning/iterative_planner.py` 和 `regen/tests/current/test_iterative_repository_planner.py`。


## 7. 无原始归属的契约视图

新规划默认 `contract_visibility: owner-blind-v1`。`owner_blind.py` 在 agent 边界构造白名单视图：
保留契约名称、完整陈述、必要上下文和属性；去掉 owner_theory、owner_file_slot、原始带归属 ID、
owner_groups、原 theory import graph 和 same-owner 特征。契约使用语义内容派生的不透明 ID，
按该 ID 排列。关系候选重新由共享语义符号生成，不复用受旧分组影响的候选集合或顺序。

`agent-input.json`、逐轮 prompt 和查询结果是实际模型输入。`planner-input.json` 保留原始冻结输入，
`private-contract-map.json` 与 `planner-validation-input.json` 仅供 harness 验证和输出回映射，
不提供给模型，也不授予模型文件读取能力。公开提案保存在 `agent-proposal.json`，规范契约 ID
回映射后的交接提案保存在 `planner-proposal.json`。后续文件投影中的契约与原冻结契约逐项一致。

这是去除输入中的原始归属，不抹除数学陈述本身需要的语义名称，也不禁止 agent 独立提出某个
分组名称。已有合法引理的来源信息仍用于可信检索，但不携带目标契约的原始归属映射。
旧的已冻结规划策略可重放；新实验不复用先前含原始归属的 prompt、笔记或模型输出。


## 8. 全局架构规划职责

`theory_plan.prompt_v4` 明确要求 agent 把全部契约重新分配到多个有语义边界的生成 theory，
自行决定数量、名称和分组。单一大 theory 不满足这份仓库规划任务；也不能靠空 theory 或任意
切分凑数。单契约输入允许一个 theory。共同上下文或共同 correspondence 谓词不足以支持
把所有语义职责合并；尚无已知依赖时，各 theory 可以保持独立。

Agent 应审视全量契约目录，在依赖具体语义或上下文兼容性时检查完整陈述；通过既有字段交接：

- `purpose` / `grouping_reason`：职责、内部关联、与邻近 theory 的边界及拆分/合并取舍。
- `helper_decisions`：已有 helper 的可信复用，或新共享结果的粗粒度意图与受益契约。
- `suggested_predecessors`：有理由的跨 theory 协作与预期帮助。
- `ordering_rationale`：全局分解、共享结果策略、DAG、ready 优先级和剩余不确定性。

参考本机 VERO 的 `.claude/skills/vero-plan/SKILL.md`（模块职责、全局覆盖和供下游执行的
机器可读计划）、`src/vero/curation/orchestrator/prompt.py`（模块/层次分解与整体审查），以及
`templates/instruction/base.md.j2`（审视全部规格、迭代直至完成）。这些是设计参考，不是运行
VERO 流程；VERO 的 generation 模板本身使用固定模块，不能据此声称它允许任意重分组。
本项目不复制其原始源码访问、Lean 证明生成职责或人审步骤，也不向 agent 提供 VERO 源码。

本次修改是规划目标和 prompt 的强化。结构 validator 仍检查覆盖、合法前提、上下文 profile
及无环性，不把 theory 数量或文本长度当作架构质量认证；也不引入固定分组数、helper/边
配额或提案修改次数上限。已冻结实验及逐轮 prompt 保持原样，新调用才使用更新后的 prompt。


## 9. 文件型规划 agent（默认入口）

`regen/config/repository-planning-policy.json` 默认选择 `agent-workspace`，显式 workspace 策略也保留；旧迭代策略保持可重放。模型仍为 gpt-5.6-sol / medium。

`workspace_planner.py` 启动独立的非 root Docker worker，只挂载只读去归属输入、工具客户端、RPC socket、固定 Codex runtime，以及可写工作区。原仓库、契约私有映射、checker 资产和 Docker socket 不进入 worker。Codex 使用临时独立认证目录，结束时删除；认证不属于模型任务输入。外层容器提供隔离，内层 CLI 可执行脚本和修改工作区文件。审计允许这些工具事件，但该审计类型仅用于仓库规划，不替代 proof-generation 的 no-tool 审计。

全量简洁目录与精确契约是只读文件，不逐轮注入大量共享符号候选。契约按语义 hash 排序映射为 C0001 等短 ID；私有 harness 映射回冻结原 ID。模型通过 `/tool.py` 查询 contracts、definitions、search、inspect、related，增量编辑 plan.json，并运行 validate/preflight/submit。定义查询复用经 native 来源认证的 v2 premise 库，按公开名字查找定义/方程候选；命名匹配不是证明依赖认证。Premise 完整记录必须经过可信查询回执才能在提案中复用。服务在模型容器外运行，查询与提交记录不受 agent 工作区修改影响。

validate 一次返回可定位的格式/覆盖错误，原权威 validator 继续检查 schema、上下文 profile、合法已检查前提和 DAG。preflight 根据冻结契约和模型 provider 边渲染 theory，以 oops 检查原生声明及 imports；不证明任何目标。相邻完全相同的 owned context 共用一个作用域，避免重复 interpretation；声明以 oops 结束，不建立新事实。900 秒原生检查超时独立于模型调用超时。submit 必须同时通过结构检查与原生预检查，最终自然语言回复不能发布计划。

当前字段 required_imports（旧格式名 suggested_predecessors）明确表示后续要等待 seal 并冻结导入的 provider；数组顺序仅用于 ready theory 优先级。Agent 应根据查询到的定义、契约和可复用事实分析潜在循环及跨模块协作，并记录不确定关系。没有 theory 数量、helper 数量或修正轮数配额。

模型调用仍有单次超时，并按相同请求重试一次；重试前恢复调用前的工作区与查询证据状态，保留已发生请求日志供审计，不消费部分最终响应。计划文件每两秒保存变化快照（不保证捕获每次写入）；快照读取拒绝 symlink。原生预检查失败可重试，同一提案成功的原生检查可复用。发布仍使用原有 plan_id/revision 日志和文件投影。

定义查询支持 `--offset` 分页并返回 `next_offset`。`mentioned_identifiers` 只是词法导航线索，可能包含变量，不代表已认证的调用或证明依赖。涉及原始 contract theory 标签的候选逐条过滤，返回过滤数量；不会因为一个候选越界就拒绝整批合法定义。分页位置仍按原始候选计算。

结构反馈提供 JSON 路径、契约覆盖错误、未检查的 premise、关系证据端点不匹配和具体 provider 环路。调整 ready 优先级不能消除 provider 环；agent 应依据真实定义和契约假设判断能否把受限情况放到一般情况之前，不预设固定模块划分。`Planner_Preflight_All` 是预检保留名称，不能作为生成 theory 名称。

恢复时校验输入、premise 库、checker、策略及实现摘要。已提交的计划绑定成功的模型调用、工具审计及原生预检证据；渲染源文件、检查日志和调用产物均校验摘要。完成提交后再次进入相同运行会复用结果，不重复调用模型。修改这些绑定内容需要新的运行目录，不能混用旧回执。快照读取还拒绝非普通文件，避免 FIFO 阻塞监控。

相关回归测试：`test_workspace_planning.py`、`test_workspace_protocol.py`。原生预检不代表已完成证明或完整仓库构建。

文件型 worker 显式设置 `web_search="disabled"`，不依赖 prompt 禁止网页工具。审计仍拒绝所有 web_search 事件；出现此事件的运行不予发布，新运行不继承它的模型工作区。


## 10. 明确的 import 契约

新规划以 `required_imports: [{"theory": "Provider", "reason": "..."}]` 表达强制导入；没有 provider 时填空数组。计划输出使用 `required_import_edges`，文件投影使用 `required_providers` 描述 provider 的目的和理由。旧 `suggested_*` 字段只用于旧 schema 的兼容读取，不出现在新规划的接口中。

Agent 在提交前可以修改 import 方案。计划接受后，每个 provider 都必须 sealed，消费者才能开始；实际导入列表在 theory 启动时冻结。串行和并行调度采用相同规则，retired 或 failed provider 均不能满足依赖。READY 优先级不产生 import，也不能绕过依赖。强制导入不要求证明引用其中每个事实；validator 不认证导入最小性或证明充分性。

原生规划预检显式导入基础语义环境和各 provider；实际证明时有 provider 则导入其 `Generated_Refine.Regen_*` checkpoint，基础语义环境通过 provider 传递继承，无 provider 则直接导入基础环境。预检用 oops 不产生事实，后续证明只能使用已 seal 的真实结果。
