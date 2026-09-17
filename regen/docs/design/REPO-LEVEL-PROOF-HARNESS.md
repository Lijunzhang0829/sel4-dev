# Repo-Level Proof Regeneration Harness

本文统一维护任务 setting、拟议仓库规划和正确性边界；具体代码现状分别见
[环境实现](../implementation/ENVIRONMENT-PREPARATION-IMPLEMENTATION.md)、
[规划实现与扩展](../implementation/REPOSITORY-PLANNING-IMPLEMENTATION.md) 和
[证明生成实现](../implementation/PROOF-GENERATION-IMPLEMENTATION.md)。设计要求不代表已经实现，历史结果见
[实验记录](../history/REFINE-CREFINE-EXPERIMENT.md)。

## 1. 研究任务与评价单位

在固定程序、规格、selected contracts 和 command 级 Semantic Base 上，探索全局规划、
文件内细化和经 prover 验证的跨文件事实复用，能否完成普通人类证明层删除后的目标集合。
多文件依赖重建与集成验收使它成为有明确范围的仓库级任务；不声称重建整个 seL4 仓库。

当前实现针对 Refine。独立 CRefine 或联合 Refine→CRefine 是否纳入首版尚未决定。
若选联合任务，CRefine 必须绑定实际生成且完整验收的 Refine；独立 CRefine 则需另行冻结上游基础。
未来 Python 库任务还需定义程序语义与形式化目标的映射，不在当前提前泛化实现。

研究目标是 seL4 的 spec、Haskell 和 C 三层语义的一致性，Refine 与 CRefine 的 contracts
是相应的本质证明目标。无 contract 的 theory 不自动成为独立验收目标，但可以承载 AI 为
contracts 拆出的辅助证明。原 contract owner 分组记录目标来源，不限制最终生成 theory 的
划分或证明节点图；重新组织时仍须保持每个 contract 的命题与语义上下文。

Contract 是外部固定目标；系统自建 obligation/helper 是普通辅助证明节点。Selector coverage 只证明
选中项不遗漏，不证明 selector 已找到全部预期端点，目标清单仍需独立审核。
没有冻结对照实验时只报告可行性、结果和成本，不声称成功率提升或模块因果贡献。

## 2. 输入和删除政策

冻结程序／规格、parents、selector、exact contracts、contract owner 分组和合法环境身份。
目标选择与删除范围分开：整个 Refine scope 进行 command 级处理，不因某 theory 没有 contract
就整体保留。普通人类辅助 lemma 的 statement、attributes 和 proof body 一并移除。

保留的语义 command 原文及其附带 facts、automation effects 属于可信基础，必须披露，不能
计作机器新生成的证明。当前默认是固定 semantic-core baseline，不保证逐项必要或最小。
Baseline 和 contract shells 构建通过只证明环境与目标可 elaboration，不证明目标成立。
具体白名单、删除分类、shell、catalog 和代码路径只在环境实现文档维护。

原始 sources 和完整 command inventory 仅供可信 preparation。生成模块不得通过文件、历史、
缓存、heap、其他副本或检索接触被删除的证明层。Semantic Base 当前仍保留原 headers/imports；
public package 不包含原 proof import graph。两者的完整可见性边界尚待明确，不能声称所有
原 imports 已删除或全部访问渠道已验证隔离。

## 3. 仓库规划与文件内迭代

目标流程：

```text
准备后的 Refine contracts + 严格父 session premise 库
  -> bounded inspect/retrieve -> repository plan + theory DAG
  -> 按 DAG ready frontier 处理生成 theory:
       冻结 plan revision、合法父上下文及显式已 seal providers
       -> file refinement + statement elaboration
       -> whole-proof 多轮尝试；结构失败反馈 GRA，失败 helper 保留在图和日志中
       -> 证明 contracts，自主选择合法已证 premises，失败可继续局部修订
       -> complete theory + accumulated build + export 验收
       -> publication
  -> 选定任务的完整构建与结果报告
```

| 规划尺度 | 决策 | 验证 |
|---|---|---|
| Repository pass | 生成 theory 分组、固定 contract 分配、共享辅助意图、直接 theory 前驱和优先级 | exact-once 覆盖、字段完整性、前驱无环；理由不作数学认证 |
| File refinement | 精确 helper 命题、上下文和局部证明分解 | graph 检查及真实 statement elaboration |

初版以一次初始全局规划启动，随后在证明过程中修订普通节点图。生成 theory 可以重新拆分，
也可以只承载辅助节点；不沿用“只能在原 contract owner 文件内生成”的目标限制。
已 seal 的 theory 是当前运行可复用的已验证产物，不静默修改后继续沿用旧证据。
仓库层的图以 theory 为节点，允许多个前驱，是 DAG 而非必须为树。前驱表达可调整的顺序建议，
不自动转成局部证明边、imports 或强制引用；同时可执行时按模型数组给出的优先级选择。
仓库层不建立 contract/helper 全局 DAG；精确辅助命题和局部图交给 File Planner。
共享辅助意图可为空，但需要解释分组与排序选择，不设置 helper 数量配额。
当前仓库规划默认消费不含 environment ID 的独立 Refine planning input，并输出上述 theory 方案和文件投影。
File Planner / Runner 已接通该投影，并以单 theory 提交日志管理接受、修订和恢复。
规划产物仍是待细化方案，不代表 proof 已通过；实现和验证范围见证明执行文档。

### 单节点直接反馈与固定前缀检查

新执行绑定默认把 checker 诊断直接反馈给下一次 Prover，不额外调用独立 salvage；
独立 salvage 可显式启用，重复/结构性失败仍交由 GRA，累计预算不能清零。
同一目标的候选从相同、有序的 GREEN 前缀 Toplevel 状态执行，失败分支不进入下一候选。
前缀变化时重建 worker，当前不跨目标增量追加。worker GREEN 必须由原冷 checker
完整复核才能提交，audit、完整 theory 检查和 build 保持独立。显式 ML/setup 等候选
及 worker 故障退回冷检查。新运行冻结 Python/ML 实现和 checker_policy，旧运行不原地修改。

worker 对候选的每条命令默认设置 60 秒超时，失败时仍记录命令耗时并返回 RED；
已加载的不可变前缀可继续服务下一候选，不因这类正常超时重复冷跑同一证明。
前缀加载与独立冷复核使用进程级时限，不与单条候选命令时限混为一谈，全部耗时仍计入预算。

分别统计初始化、候选执行、模型/可选 salvage、GREEN 冷复核和最终集成成本。
候选数不等于 checker 调用数，声明块标记也不等于异步证明完成。该机制降低连续失败成本，
不承诺成功率提高；首候选成功可能增加检查成本。实现见
[证明生成实现](../implementation/PROOF-GENERATION-IMPLEMENTATION.md)。

## 4. 普通辅助节点、失败保留与事实复用

helper 和自建 obligation 统一作为普通辅助证明节点，不再设置 helper 叶节点、专用生命周期、
跨文件承诺或 intended-consumer 特权。节点可以继续拆分和依赖其他合法已证节点；原始
contract 是不能删除、不能改变命题的固定目标。Lemma Prover 生成 proof body，GRA 修订计划。

处理一个 theory 时，按 accepted DAG 的 ready frontier 尝试节点。失败在多轮中反馈给 GRA，
用于修改证明指导或追加图 revision。最终仍失败的辅助节点不会自动删除：节点、失败证据与累计
成本都保留，theory 明确停止为 failed。GRA 可以追加缺失 helper、调整未提交节点的调度边，
或以保留父节点的方式 split；不能删除 fixed contract、失败 helper 或 GREEN 内容，也不能把未证明
结论当作假设。已验证 proof 必须保持有效，不能用图编辑伪造通过。

证明节点时，agent 自主决定是否引用以及引用哪些合法 premises：

- 同 theory 中已证明、在实际 Isabelle 上下文中可访问的 lemma；
- 当前运行已 seal 的其他生成 theory 中可访问的事实；
- 冻结父 sessions 和合法 Semantic Base 中的事实。

图上的预期引用不强制 agent 使用某条 lemma，也不让未证节点成为可信 premise。若选择了
同 theory 的已证 lemma，checker 必须实际包含该声明及其所需的已证上下文；其可选范围不应
仅限于最初规划的依赖边。跨 theory 的候选仍必须已 seal 并实际可访问。

建议模型显式引用对应 premise，以便解释和检查。允许模型使用 `simp`、`wp` 等 tactic，只要
在合法环境中通过 Isabelle 检查；不要求准确恢复 tactic 使用的全部 premises，也不增加专门的
automation 发布审批。显式引用、模型自报与 prover 能导出的依赖可以记录，但不能冒充完整引用集合。

## 5. 正确性、证据和完成定义

目标合同要求：

1. Contract 的命题、types、assumptions 及引用的程序／定义语义不变。
2. LLM 提方案，真实 prover 判断 elaboration 和 proof；每次 attempt 中作为可信 premise
   展示的事实必须在 checker context 中可用。待证目标和辅助节点必须明确标为待证。
3. GRA 不修改已 GREEN statement、proof 或上下文；旧证据失效后不能维持 GREEN。
4. 完整生成输出不得用 `sorry/oops` 等跳过证明，不引入未允许的 axiom/oracle 或隐藏 facts。
5. Theory seal / 发布绑定最终 source、有效检查及合法上下文。组装检查仍须通过；组装失败后的
   自动修复暂缓设计，当前只保存错误并停止该次完成流程。
6. 任务完整成功要求全部冻结 contracts 在最终生成产物中得到证明，承载这些证明及其依赖的
   生成 theories 完整构建通过；不要求为每个无 contract 的原 theory 单独生成证明。

至少保存数据集和目标身份、plan revision、精确 graph、attempt 输入输出、checker 结果、可获得的
依赖记录、接受的 proof、publication 和失败 helper 历史。恢复不能凭 live summary 自报成功。

计划边、显式引用、模型自报和 prover 导出的依赖不能混用；无法完整恢复的 tactic 依赖
不作为独立验收阻断项。初始规划与运行图之间的存储关系不预先固定。

分别报告原始目标覆盖与正确性、实验协议合规、证据完整性；helper 数不是稳定完成度分母。
同时记录生成与失败保留成本，以及能确认的同／跨文件采用和 premise 来源。
仅在 automation pool 中出现不算实际使用；依赖与 automation 的关联也不等于 tactic 内部因果。

## 6. 尚未确定的政策

- 初始规划与动态节点图的实现映射，以及自由 theory 拆分的运行接线；
- CRefine 独立数据集的后续准备实现；Refine 与 CRefine 不构成联合任务；
- 语义构建保留 imports 与模型输入边界；
- 统一预算、停止规则，以及失败后是否继续无依赖文件。

现有配置的行为由模块说明记录，不当成上述问题的最终答案。不复制旧分支的固定 top-k、
调用次数或大型 WAL/CAS 合同，也不把 Assembly 诊断写成已实现自动修复。

规划器升级可以建立独立规划冻结，引用已验收环境的身份并保存新的政策、预算和代码快照。
这种复用不修改旧环境，也不把新政策追溯归入旧验收；真实新方案仍须经过调用审计和完整
结构校验。仅完成输入准备不得报告为规划成功。
