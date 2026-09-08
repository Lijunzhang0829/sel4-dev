# Repo-Level Proof Regeneration Harness — 导航与现状

本文是当前 Semantic Base 实现的流程索引，不再重复定义另一套规范。
任务设计见 [Harness Design](UNIFIED-PROOF-HARNESS-DESIGN.md)，研究问题见
[Architect Experiment](UNIFIED-PROOF-ARCHITECT-EXPERIMENT.md)，模块地图见
[Module Implementation](UNIFIED-PROOF-MODULE-IMPLEMENTATION.md)。

当前实现针对 Refine；独立 CRefine 或两 session 联合任务待确定。只有选择联合任务时，
CRefine 才要求绑定本次完整 generated Refine。一个 session 内的多文件规划、证明复用和集成
可以构成有明确范围的仓库级任务，不声称覆盖整个 seL4 仓库。

## 实际流程与设计差距

| 步骤 | 本分支实际入口 | 已核对行为与限制 |
|---|---|---|
| 环境准备 | [environment_preparation.py](environment_preparation.py)、[environment_gate.py](environment_gate.py) | 全 Refine scope 的 command 级筛选、Semantic Base、shell、catalog 和环境 gate |
| Session 调度 | [session_planner.py](session_planner.py) | contract-based scheduling；尚非全局 obligation/helper 架构规划 |
| 文件规划 | [file_planner.py](file_planner.py)、[proof_graph.py](proof_graph.py) | fixed-contracts-only payload 产生局部精确 graph；尚未消费 Planning Substrate |
| 证明与修订 | [proof_session_runner.py](proof_session_runner.py)、[lemma_prover.py](lemma_prover.py)、[graph_revision_agent.py](graph_revision_agent.py) | node proof/check/retry/revision；拟议 helper 撤销政策尚未实现 |
| Theory 验收与发布 | Runner `create_theory_seal()`、`publish_generated_records()` | 完整 theory check、retained impact、accumulated build 后建立 seal 与生成事实记录 |
| Session 遍历结果 | Runner `run_session()` | 汇总 ledger 为 complete/partial；不等同于额外最终 clean-build/Session Seal gate |

## 环境边界

目标选择与删除政策分离。普通人类 theorem/helper 和独立证明注册移除；固定语义 core
原文保留，附带 facts/automation effects 披露。Baseline 验收只说明语义环境与目标 shells 可构建，
不证明每个 command 不可删除，也不证明 contracts 成立。

Semantic Base 当前保留原 headers/imports；public contract packages 不包含人类 proof import
图。完整可见性边界仍待明确，不将二者混为“imports 全部已删除”。详细规则只维护在
[环境实现说明](ENVIRONMENT-PREPARATION-IMPLEMENTATION.md) 和 Harness Design。

## 证明结果与发布

[isabelle_node_checker.py](isabelle_node_checker.py) 构造检查 source 并调用 Isabelle。
[assembly_stabilizer.py](assembly_stabilizer.py) 的 `prepare_assembly_stabilization()` 只生成
定位／诊断结果；当前 Runner 在 complete check 失败后记录结果并报错，没有自动 proof-body
修复闭环。不能在说明中将诊断模块写成已经执行修复。

[retained_semantic_impact.py](retained_semantic_impact.py) 记录保留语义事实的实际依赖及其
与 automation pools 的关联。Pool membership 本身不算实际使用，也不构成 tactic 因果证据。

旧 [session_seal.py](session_seal.py)、[theory_checkpoint.py](theory_checkpoint.py)、
[theory_export_planner.py](theory_export_planner.py) 和 [theory_audit.py](theory_audit.py)
不作为本文件的当前 Runner 验收入口；需要复用其行为时应先核对调用链。
目标设计中的 typed contract identity、完整 export legality 和最终 session 验收不能仅凭
存在同名文件就称为已实现。

## 阅读顺序

1. [README](README.md)：分支、来源、文档分工和未决项。
2. [环境实现](ENVIRONMENT-PREPARATION-IMPLEMENTATION.md)：command 级输入与 gate。
3. [Session 调度实现](SESSION-PLANNING-IMPLEMENTATION.md)：现有调度政策及代码入口。
4. [Planning Substrate 计划](ENVIRONMENT-PLANNING-SUBSTRATE-PLAN.md)：拟议输入扩展。
5. [实验历史记录](REFINE-CREFINE-EXPERIMENT.md)：不同 campaign 的历史结果，非当前 PASS。

本次文档核对没有修改代码或配置，也没有执行模型调用、Isabelle 构建或生产验收。
