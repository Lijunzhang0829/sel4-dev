# Repo-Level Proof Regeneration Harness

本文档描述 Refine/CRefine repo-level 证明重生成 harness 的总体流程与模块契约。
它只说明每个模块负责什么、接收什么、产出什么，以及模块之间如何交接。
具体代码、算法和验证命令放在各模块实现文档中。

## 实验边界

实验固定代码与规格端点、父 sessions、目标 contract 集合和 contract 所属 theory
槽位。普通的人类 refinement theorem/helper proof layer 被删除；为了让 contracts
具有确定含义而必须保留的 command 作为 trusted semantic substrate 封存。

这些 retained commands 导出的 facts 和 automation memberships 不是生成结果，也
不能因为没有进入显式 premise 列表就视为未使用。环境模块先记录它们带来的
`facts/simp/wp/cong/named collections`；每个 theory 组装后、发布前，在一次
complete-theory Isabelle process 中批量导出所有 generated theorems 的 proof-term
dependencies，并导出一份共享的 active automation context，
再与 `retained_semantic_export` 对照。只有出现在 Isabelle certificate dependency 中的
fact 才计为实际使用；仅存在于 automation pool 不计为使用。若 certificate dependency
同时属于 active pool 且 proof 调用了相应 automation，只报告为 automation-associated，
不夸大为已证明的 tactic 内部因果。整个 session 完成后汇总受影响 theorem 数量和
具体依赖。最终报告必须区分 retained human-certified proof power 与 generated proof
layer，并说明前者实际帮助了哪些证明。

Refine 必须完整生成并封存后，CRefine 才能开始。部分成功的 Refine campaign
只能产出 partial report，不能用 stock human Refine heap 补齐。

## 总体流程

```text
1. 环境准备与冻结
   ↓
2. Repo / Session 级规划
   ↓
3. 单 Theory 的证明结构规划
   ↓
4. 单 Node 的证明生成与修复
   ↓
5. Theory 组装、审计、封存与发布
   ↓
6. Repo Session 完成与 Refine → CRefine 交接
```

## 1. 环境准备与冻结

### 实现说明

- [环境准备与冻结实现说明](ENVIRONMENT-PREPARATION-IMPLEMENTATION.md)
- 主要入口：[environment_preparation.py](environment_preparation.py)

### 功能

建立证明生成可以信任的 Isabelle 环境。该模块固定实验设置，提取全部 fixed
contracts，移除普通人类 proof layer，构建 contract 所需的 semantic substrate，
发布合法 Premise Catalog，并给下游提供唯一的环境 gate。

### 输入

- repository revision、architecture 与目标 session；
- frozen parent sessions、规格与实现端点；
- contract selection policy；
- 模型、预算与运行策略；
- 原始 Refine sources，仅限可信准备阶段读取。

### 处理

- 冻结 campaign 设置；
- 提取 exact contract statements 与 contract-owned attributes；
- 建立 contract/command inventory；
- 选择并保留 contract semantic environment 必需的 commands；
- 构造不含原始目标 proof sources 的运行环境；
- 用 Isabelle clean-build semantic substrate 和全部 contract shells；
- 导出合法 facts、definitions 与 automation memberships；
- 封存 Semantic Base、Premise Catalog 和 environment gate。

### 输出

- campaign manifest；
- public contract packages 与 private command inventory；
- sanitized runtime；
- Semantic Base heap 与 seal；
- Premise Catalog snapshot/database；
- completed context-effect disclosure，其中列出 retained exports、`simp/wp/cong`、
  classical intro/elim 与 named-collection memberships；
- 允许或拒绝 proof generation 的 environment gate。

### 交接条件

只有 Semantic Base seal 通过独立 runtime/source 复查、Premise Catalog 的全部
数据库列可从 snapshot 重建、Context Effect Disclosure 为 `complete` 且覆盖规定的
automation surface 后，environment gate 才能 PASS。Generated proof 对 retained
effects 的实际使用量不在此时猜测，而是在每个 theory seal 前作为硬 gate 检查。
下游必须使用 gate 指定的 Semantic Base、contract packages 和 Premise Catalog。

## 2. Repo / Session 级规划

### 实现说明

- [Repo / Session 级规划实现说明](SESSION-PLANNING-IMPLEMENTATION.md)
- 主要入口：[session_planner.py](session_planner.py)

### 功能

为整个 Refine session 生成固定的 theory 执行计划，并维护 session 进度。该模块
只处理 theory 间的生成优先级，不设计任何 theory 内部 theorem、helper 或 proof。

### 输入

- 已验证的 environment gate；
- 全部 contract-owning theory slots；
- 每个 theory 的 exact contracts；
- Premise Catalog 的身份与规模信息；
- frozen Session Planning policy。

### 处理

- 分析 contracts 暴露出的跨 theory 组合或调用证据；
- 区分强生成优先级与弱同层优先级；
- 验证候选关系并拒绝无依据、重复或非法关系；
- 生成分层 theory 列表和确定性串行顺序；
- 初始化每个 theory 的执行状态；
- runner 绑定并持续更新这一份 canonical session progress ledger；
- `run-session` 按 serial fallback 遍历，单 theory 失败后继续，并汇总为
  `partial` 或 `complete`。

同层只表示没有足够证据将 theories 分到不同层，不等于它们语义独立，也不强制
并行执行。某个 theory 失败后可以继续尝试列表中的其他 theory，但完整 session
封存仍要求所有必需 theory 成功。

### 输出

- dependency evidence；
- immutable file-generation list；
- session planning audit；
- mutable session progress ledger；
- 完整 planner transcript。

### 交接条件

Theory runner 必须验证整个 planning bundle 与当前 environment gate、contract
packages 完全对应，并且只能更新 Session Planner 创建的 canonical ledger，不得另建
第二份文件状态表。单独复制一个旧文件列表不能进入下一模块。

## 3. 单 Theory 的证明结构规划

### 实现说明

- 规划入口：[file_planner.py](file_planner.py)
- theorem DAG 与结构认证：[proof_graph.py](proof_graph.py)
- statement shell 检查：[isabelle_node_checker.py](isabelle_node_checker.py)
- 本模块将在当前 campaign 首次 vertical slice 后补充独立实现分册。

### 功能

针对一个 theory 的全部 fixed contracts，设计本次需要生成的 theorem DAG。
该模块规划证明结构，不写 proof body，也不把 external premises 伪装成图节点。

### 输入

File Planner 只接收：

- 当前 theory 的全部 fixed contracts 和 contract-owned contexts；
- theorem/helper/contract graph schema；
- 当前 generation、Environment Gate、Session Planning list 与 contract package identity；
- 前一次规划被拒绝时的结构或 Isabelle statement-shell diagnostics。

File Planner 不接收 Premise Catalog records、premise 名称、premise statements、
retrieval results 或 sealed predecessor facts。External premise 是否存在以及选择哪个
premise，完全属于后续 Lemma Prover 的 node-specific retrieval。

### 处理

- 以 fixed contracts 为根；
- 按需要增加 obligation theorems；
- 按需要增加可独立封装的 helper leaves；
- 为每个 generated node 给出精确 statement、role、dependencies 与 consumers；
- 确定性检查 fixed contracts、角色、依赖、消费者、重复命题与无环性；
- 在 sealed Semantic Base 与当前已 sealed generated theories 构成的 Isabelle context
  中检查全部 statement shells；
- statement-shell RED 时把精确 diagnostic 返回下一次 File Planner 调用；只有 GREEN
  图才写入 canonical `graph-v1.json`，并把 session ledger 标记为 `planned`。

DAG 不强制出现 obligation 或 helper。contract 可以直接证明，也可以在 Lemma Prover
运行时调用合法 premises。External premises 不是 generated DAG nodes。图的 node 数量
不设预先上限；实际 theorem/helper 数量、图深度和膨胀程度属于最终评价指标。

### 输出

- 绑定 campaign、Environment Gate、Session Planning list、contract package 和 planner
  transcript 的 immutable theorem DAG；
- Isabelle GREEN statement-shell certificate；
- executable node order 与 graph identity；
- 被拒绝规划的 statement/structure diagnostics 和完整模型日志。

### 交接条件

只有 statement 可 elaboration、graph 无环、fixed contracts 未改变、依赖关系完整且
上游 identities 一致的图，才能被标记为 `planned` 并进入单 Node 证明。

## 4. 单 Node 的证明生成与修复

### 实现说明

- premise retrieval：[premise_retrieval.py](premise_retrieval.py)
- automation advice：[automation_advisor.py](automation_advisor.py)
- proof outline 与 salvage：[local_proof_outline.py](local_proof_outline.py)、
  [proof_outline_salvage.py](proof_outline_salvage.py)
- lemma generation：[lemma_prover.py](lemma_prover.py)
- graph revision：[graph_revision_agent.py](graph_revision_agent.py)
- Isabelle checking：[isabelle_node_checker.py](isabelle_node_checker.py)
- 本模块将在单 node baseline 重新验证后补充独立实现分册。

### 功能

为一个 certified theorem node 生成 Isabelle proof，并根据真实 residual 修复。
proof body 修复和 theorem graph 修订由不同组件负责。

### 输入

- 当前 node 的不可修改 statement；
- 已 GREEN 的 generated dependency closure；
- 当前合法 premises；
- Automation Advisor 给出的局部 tactic probes；
- local proof outline；
- 历次 attempts、Isabelle residuals 与 budgets。

### 处理

- runner 把一次 node 证明视为可替换的黑盒；黑盒只接收 fixed statement、GREEN
  dependency closure、合法 premise 查询接口和上一轮反馈，并返回 GREEN 或结构化 RED；
- 当前冻结 baseline 是 `Whole-proof + Salvage`，Stepwise 保留为后续可比较 backend，
  不在本轮 production 默认路径中；
- 一个 logical round 最多产生 5 个真正交给 Isabelle 检查的 Whole-proof candidates；
  provider/transport failure 单独记录，不消耗这 5 次 proof candidate 机会；
- 每个 Isabelle RED candidate 后调用一次 local Salvage。Salvage 只能生成当前 theorem
  内部的局部 outline，不能修改 statement、增加 theorem node 或修改 DAG；
- 五个 candidates 全部 RED 后，runner 只调用一次 Graph Revision Agent；
- GRA 返回 `RETRY_PROOF` 时必须给出非空且相对上一条已消费 directive 有实质变化的
  retrieval、outline、frontier、automation 或 repair 决策；该 directive 绑定 active graph、
  node statement 与 dependency closure，并由 immutable consumption artifact 指定唯一的
  下一 logical round。其 guidance、上一轮候选、residual 与最后 Salvage outline 一起进入
  该轮；返回 graph revision 时先重新认证 statements/DAG；返回 abandon 时 node 保持 RED；
- 每个 node 最多 5 个 logical rounds；该上限与每轮 5 个 candidates 是两个不同预算；
- Lemma Prover、Salvage 与 GRA 的模型调用均在 audited clean runtime 中执行，任何
  non-sealable 调用不得形成 GREEN node 或 graph transaction。
- `init`、`plan-file`、`run-file` 与 `run-session` 的所有可变操作由同一 run-level
  non-blocking lock 串行化。该锁覆盖 theory artifacts 与 canonical Session Planner ledger，
  因此不同 theory 的独立进程也不能并发覆盖同一账本；`run-session` 在完整 serial
  traversal 期间持锁。
- 中断恢复除 canonical progress 外，还检查 immutable checker/GRA artifacts。一个 RED
  attempt 只有在 checker source 仍精确包含 active node statement、dependency statements
  与 candidate proof，且 graph payload identity 未变时才进入下一轮 feedback。一个已接受
  但未消费的 `RETRY_PROOF` 也必须重新通过当前 deterministic validator；陈旧、重复或已有
  consumption artifact 的 guidance 不得恢复。

Premise Retrieval 回答当前有哪些合法 facts。Automation Advisor 与 Stepwise 属于可替换
backend 的局部能力，不改变上述黑盒边界；当前 Whole-proof baseline 不依赖 Stepwise
tree search。

Lemma Prover 不能修改 statement 或私自增加节点。Graph Revision Agent 的修改必须
重新经过 statement/graph 检查。一个节点可以最终为 GREEN 或 RED；系统不假设
任何模型能够百分之百证明全部节点。

### 输出

- GREEN proof body 与实际依赖证据；或
- RED result、最佳 frontier、residual 和失败分类；或
- 经批准的新 graph revision；
- 中断恢复时的 checker-bound feedback，以及每条 GRA RETRY directive 的
  `gra-feedback-consumed.json` 一次性消费记录。

这些机制及其 policy 是 frozen harness inputs。对已有 run 启用它们时必须创建 successor
campaign 并重新冻结 identity；不得在原 campaign identity 下静默改变执行或恢复语义。

运行时监控不构成新的 proof-state source。runner 将全部既有 progress events
追加写入 `runner-events.ndjson`，并每 30 秒原子更新 `runner-status.json`；
后者记录 PID、当前 theory/node/round/stage、最近 transition/heartbeat 和运行状态。
即使控制终端或对话停止轮询，也可独立判断 runner 是否存活以及正在等待 LLM、
Isabelle、Salvage 还是 GRA。证明恢复读取 canonical session ledger、
`theories/<Theory>/progress.json` 和与 active graph 严格绑定的 immutable checker/GRA
artifacts，绝不读取监控快照。

### 交接条件

只有 Isabelle GREEN 且来源、transcript 与依赖可审计的节点可以供本 theory 的
后续节点使用。RED 节点不能发布为 premise。

## 5. Theory 组装、审计、封存与发布

### 实现说明

- assembly stabilization：[assembly_stabilizer.py](assembly_stabilizer.py)
- theory transcript audit：[theory_audit.py](theory_audit.py)
- retained semantic 实际影响审计：[retained_semantic_impact.py](retained_semantic_impact.py)
- checkpoint 与发布：[theory_checkpoint.py](theory_checkpoint.py)
- export planning：[theory_export_planner.py](theory_export_planner.py)
- orchestration：[proof_session_runner.py](proof_session_runner.py)
- 本模块将在当前 campaign 产生首个 theory seal 后补充独立实现分册。

### 功能

把一个 theory 的全部 GREEN nodes 组装成完整 `.thy`，处理单节点环境与完整 theory
环境之间的差异，并决定该 theory 是否可以跨文件发布。

### 输入

- fixed contracts；
- 最终 certified graph；
- 全部 GREEN proof bodies；
- 实际 local 与 cross-theory dependencies；
- attributes 与 automation effects；
- generation ledger 和完整 transcripts。

### 处理

- 按依赖顺序组装完整 theory；
- 根据实际跨文件使用形成候选 imports；
- 在完整 theory context 中 clean-build；
- 只修改 proof body 以稳定 assembly residual；
- 由 Isabelle 导出 proof-term dependencies 与 active automation context；
- 区分 source-name mention、certificate-level actual dependency 和
  automation-associated dependency；pool membership 本身不计为实际使用；
- 将实际依赖按 frozen parent、retained semantic export、local generated node 和
  sealed generated theory 分类；
- 对 retained semantic facts 与 automation memberships 记录使用它们的 theorem；
- 检查无 `sorry`、`oops`、新 axioms、contract 弱化或非法来源；
- 最小化并重新检查 imports；
- 生成 theory seal。

### 输出

- generated `.thy`；
- final imports 与 attributes；
- assembly/audit results，包括 retained semantic proof-power use report；
- immutable theory seal；
- 可发布的 generated premise records。

### 交接条件

只有 sealed theory 的 declarations 可以进入持久 Premise Catalog，供后续 theories
检索。仅单节点 GREEN 或完整 theory GREEN 但不可审计的结果都不能发布。

## 6. Repo Session 完成与 Refine → CRefine 交接

### 实现说明

- session assembly 与 seal：[session_seal.py](session_seal.py)
- retained semantic theory/session 聚合：[retained_semantic_impact.py](retained_semantic_impact.py)
- session 状态与执行入口：[proof_session_runner.py](proof_session_runner.py)
- 本模块将在完整 Refine campaign 前补充独立实现分册。

### 功能

汇总所有 theory 结果，判断整个 Refine session 是否完成，并在成功时把唯一合法的
generated Refine heap 交给 CRefine。

### 输入

- immutable session file list；
- mutable session progress ledger；
- 全部 theory seals；
- 最终 generated premise snapshot；
- frozen Semantic Base；
- 全部 generation 与 audit evidence。

### 处理

- 检查每个必需 theory 已 sealed；
- 检查每个 fixed contract 恰好证明一次；
- clean-build 完整 generated Refine；
- 审计跨 theory imports、premise provenance 和 retained semantic effects；
- 汇总 retained facts 对 generated theorems 的 source mention、certificate dependency
  与 automation-associated dependency；
- 生成 coverage、失败与工程质量报告；
- 封存 Refine heap 和 session identity。

### 输出

成功时：

- generated Refine session seal；
- generated Refine heap；
- final Premise Catalog snapshot；
- complete coverage/dependency/leakage audit；
- retained semantic proof-power impact report；
- CRefine handoff package。

不完整时：

- partial-campaign report；
- sealed/failed/skipped theory 列表；
- contract coverage gaps；
- RED evidence 与停止原因。

### 交接条件

只有完整 Refine session seal 才允许启动生产 CRefine。CRefine 必须使用该 exact
generated Refine heap，不能回退到 stock human Refine heap。

## 跨模块不变量

- fixed contract statements 与 contract-owned attributes 不可修改；
- 模型不可读取原始 contract-owning Proof source；
- planned、RED、abandoned 或 unsealed nodes 不是合法 premises；
- Isabelle 决定 statement 与 proof 是否成立；
- 模型输出是候选，确定性检查器决定是否合法；
- 每次模型调用保留输入、原始输出、provider metadata 与失败证据；
- 同一 proof run 的任何 ledger/theory mutation 必须持有 run-level mutation lock；
- 恢复证据必须绑定 active graph、statement 和 dependency closure，GRA RETRY guidance
  必须有实质变化并且只能消费一次；
- GREEN、sealable、publishable 是三个不同状态；
- 任何 partial result 都不能被表述为完整 repo session 成功。

## 文档导航

- 环境模块实现：`ENVIRONMENT-PREPARATION-IMPLEMENTATION.md`
- Session 规划实现：`SESSION-PLANNING-IMPLEMENTATION.md`
- 当前设置、进度与评价：`REFINE-CREFINE-EXPERIMENT.md`
- 历史问题与修改经验：`HARNESS-EVOLUTION.md`
