# Unified Proof Architect Experiment

状态：选择性迁移的研究设计草案。
来源：`session-planner-global-proof-plan@bacfdb5` 的同名文档；按本次讨论收窄规模。
实现与输入边界见 [模块地图](UNIFIED-PROOF-MODULE-IMPLEMENTATION.md) 和
[Harness Design](UNIFIED-PROOF-HARNESS-DESIGN.md)。

## 研究问题

在固定程序、规格、selected contracts 和 command 级语义环境上，仓库范围的规划、文件内
细化及经过 prover 验证的跨文件事实复用，能否完成普通人类证明层删除后的目标集合？

一次任务需要在多个 theory 之间建立生成依赖和复用并完成集成验收，不以独立 lemma 的通过率
替代整体结果。当前实现面向 Refine，一个 session 可以作为有明确边界的仓库级任务范围；
不声称重建整个 seL4 仓库。是否增加独立 CRefine 或 Refine→CRefine 联合任务尚未决定。
联合任务若采用，CRefine 必须绑定实际生成的 Refine；独立 CRefine 则需明确冻结的上游基础。

selected contracts 是预先冻结的目标，而非所有原有人类 lemma。selector 仍需独立审核其目标
覆盖，不能用“选中项 exact coverage”证明它找到了全部预期端点。

没有 comparison arm 时报告 pipeline feasibility、证明结果与成本，不声称成功率增益或因果。
未来 Python 库任务还需独立定义程序语义与形式化目标的映射，不在本次提前泛化实现。

## 两个规划尺度

| 尺度 | 决定什么 | 依据与验证 |
|---|---|---|
| Repository pass | 全部目标的路线、helper 意图、owner、跨文件需求与顺序建议 | 合法语义视图；coverage、owner 和依赖结构检查 |
| File refinement | 当前文件的精确 helper 命题、上下文、局部证明分解 | 合法的具体接口；statement elaboration 与 graph 检查 |

原始 contracts 固定；全局规划产生的 cuts 是搜索方案，不能变成不可撤销的额外外部目标。
每个规划项有稳定来源记录，局部修订给出原因。稳定 ID 不意味着自然语言意图已经被形式化验证。

初版采用一次仓库规划和文件内迭代，不实现完整跨文件回修、owner 迁移或新建共享 helper theory。
完整开发式仓库重构留待后续。冻结 Architecture 与动态 Exact Graph 如何组织和对应仍待用户
进一步决定，本次不固定其 schema、版本关系或把二者合并成同一图。

## Helper 创建、采用与撤销

Repository pass 可提出共享 helper；File Planner/GRA 可根据局部证据提出、拆分或替换 helper。
Lemma Prover 可以在一个证明体内使用局部步骤，但不自行增加 top-level graph 节点。

intended consumers 表示预期用途，不保证实际采用，也不自动成为 required dependency。
同 theory 的合法 GREEN facts 可以供后续节点使用；跨 theory facts 经过完整构建与发布后才可用。
Prover 自行选择是否使用提供的事实，实际采用以 prover 的依赖证据认定。

拟议支持撤销不再需要的未证明 helper：它不是 contract，不曾 GREEN 或发布，撤销不会改变
任何已接受证明的上下文。可同时撤销尚未 GREEN consumer 对它的计划依赖，但 consumer 的
原始目标仍必须证明。保留撤销原因、来源、失败记录和累计成本；不能借此删除原始 contracts。
跨文件承诺的 helper 不在首版自动撤销范围内。

本次不要求正式 witness 证明 replacement 保持自然语言 intent。Codex 对话监视记录其合理性；
自动检查保留目标身份、图结构、合法上下文和最终证明。修改后的 helper 即使更容易证明，
consumer 仍须独立完成原目标，不能把待证结论或未证额外前提混入 trusted environment。

撤销是待实现方向，范围与工作量估计只在 [模块地图](UNIFIED-PROOF-MODULE-IMPLEMENTATION.md) 维护。

## 观察与评价

区分计划依赖、真实证明依赖和来源演化；这三种关系不能互相冒充。正式图表示仍待设计，
尤其不把 intended consumers 或检索结果直接算作真实复用。

报告至少包含：

- frozen contracts 完成比例、整个选定任务是否通过最终构建；
- helper 的创建、撤销、替换、未采用及真实同／跨文件采用；
- parent facts、retained semantic exports、generated facts 的使用来源；
- 模型、checker、wall time、失败和重新尝试的完整成本。

保留语义 command 产生的 facts 及 automation effects 是合法基础，但不计为机器新生成的证明。
仅位于 automation pool 的事实不能算已使用；依赖证据与 automation 关联也不等同于 tactic
内部因果归因。原始目标完成、实验协议合规与证据完整性分别报告。
