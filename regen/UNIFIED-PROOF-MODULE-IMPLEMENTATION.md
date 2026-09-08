# Unified Proof Pipeline — 模块地图与迁移边界

状态：基于 `4de882f` semantic-base 快照及本次静态调用链核对；未运行新的生产验收。
来源：`session-planner-global-proof-plan@bacfdb5` 的同名文档，保留职责与证据结构，不继承
来源分支的实现 PASS/FAIL、测试数量或运行结果。

以下“已有文件”表示入口存在，并不表示满足全部
[目标设计](UNIFIED-PROOF-HARNESS-DESIGN.md)。不要求复制来源分支的目录布局。

## 1. Semantic Base 的实际输入链

| 责任 | 当前平铺路径 | 与旧 Support/Proof 路径的区别 |
|---|---|---|
| 环境冻结 | [environment_freeze.py](environment_freeze.py)、[refine_campaign.py](refine_campaign.py) | 使用 Semantic-Base profile |
| 目标提取 | [contract_extractor.py](contract_extractor.py)、[scripts/regen_extract.py](scripts/regen_extract.py) | selector 决定最终目标；不是整份 theory 的保留规则 |
| Command inventory | [contract_command_inventory.py](contract_command_inventory.py) | 全 Refine scope 分成 fixed-contract、ordinary-proof、semantic-candidate、structure |
| 语义环境构建 | [semantic_base_builder.py](semantic_base_builder.py) | semantic-core seed + scope/header 重建 + 独立 contract shells |
| Core 验收／诊断 | [command_necessity.py](command_necessity.py) | 支持整体 baseline 验收，不宣称 command minimality；另有删除反事实 |
| 运行隔离与环境验收 | [environment_preparation.py](environment_preparation.py)、[environment_gate.py](environment_gate.py) | 删除运行副本原始目标 Refine，保留 parents 并绑定语义环境 |
| 合法事实和效果 | [premise_export.py](premise_export.py)、[premise_catalog.py](premise_catalog.py) | 区分 parent 与 retained semantic exports |
| 残留证明能力使用 | [retained_semantic_impact.py](retained_semantic_impact.py) | 分析生成证明对 retained facts 的实际依赖 |

配置见 [refine-semantic-base-campaign.json](config/refine-semantic-base-campaign.json)。
本次没有调整其中的模型、预算、首个 theory 或其他运行参数。

## 2. 仓库规划扩展的接入点

| 责任 | 当前入口／迁移方向 | 验证重点 |
|---|---|---|
| Planning Substrate | 待实现或适配；不直接复制旧 preparation 输入 schema | 合法 source classes、contract coverage、符号解析和投影身份 |
| Repository planning | [session_planner.py](session_planner.py) 当前仅 contract-based scheduling；架构规划待扩展 | 新语义视图接入前需修改现有输入政策 |
| File refinement | [file_planner.py](file_planner.py)、[proof_graph.py](proof_graph.py) 当前为 fixed-contracts-only | 接入 architecture projection 属于待实现改动 |
| 执行和检查 | [proof_session_runner.py](proof_session_runner.py)、[lemma_prover.py](lemma_prover.py)、[isabelle_node_checker.py](isabelle_node_checker.py) | 实际 context、proof、依赖证据一致 |
| Local revision | [graph_revision_agent.py](graph_revision_agent.py)、[proof_graph.py](proof_graph.py) | 仅修改未证范围；helper 撤销需另行实现 |
| 组装诊断 | [assembly_stabilizer.py](assembly_stabilizer.py) `prepare_assembly_stabilization()` | 当前只定位，不自动修 proof body |
| Theory seal / publication | [proof_session_runner.py](proof_session_runner.py) `create_theory_seal()`、`publish_generated_records()` | 完整 theory check、accumulated build 和 retained impact；完整目标合同仍需验证 |
| Session 汇总 | [proof_session_runner.py](proof_session_runner.py) `_run_session_unlocked()` | 只按 ledger 汇总 complete/partial，不新建最终 Session Seal |
| 审计 | [llm_transcript.py](llm_transcript.py)、[module_run_log.py](module_run_log.py)、[progress_events.py](progress_events.py) | 实际输入输出、失败、成本与恢复依据 |

旧 planning 开发线已删除，不把其中的 `planning/`、`execution/`、`publication/` 等路径
列为本分支代码入口。若实现独立 architecture、lineage、fact input 或图报告模块，需按本分支
需求设计；本次没有恢复旧代码。

[session_seal.py](session_seal.py) 属于旧 `refine-arm-session` root-plan 流程，不能替代当前
Runner 所缺的最终验收接线。有关现有调度限制见 [Session 实现说明](SESSION-PLANNING-IMPLEMENTATION.md)。

## 3. 最小增量与工作量

先完成输入适配与只读 substrate，再接入仓库规划；File Planner 的投影消费作为独立步骤。
不把整个旧分支的目录重组、logging compliance 和比较实验一起迁入。

helper 撤销最小版本约 3–5 个工程日，是静态范围估计：

- 增加可审计的撤销 disposition，禁止删除原始目标或已 GREEN 内容；
- 调整来源追踪，允许 origin 以明确撤销记录终止，不要求伪造 replacement；
- GRA 同步更新未 GREEN consumers 的计划边，验证无悬空依赖；
- Runner、恢复和结果报告不再把已合法撤销 helper 作为 active blocker，成本仍累计；
- 覆盖拒绝删除 contract/GREEN、撤销成功、恢复及最终目标必须完成等测试。

估计不包括跨 theory 回修、已发布事实撤销或补齐来源文档的完整事务／seal 体系。目标分支的
具体改动点需实现前再核对；不能把来源分支现有 pruning 函数当成本分支已具备的能力。

## 4. 证据与验证清单

| 边界 | 需要的证据 |
|---|---|
| 环境 | selector、command selection、baseline/elaboration、合法 catalog、残留效果与身份 |
| 规划 | 展示给模型的合法输入、response、coverage/结构检查、修订原因 |
| 证明 | exact target、checker context、实际 facts、candidate、真实 checker 结果和依赖 |
| 修改／撤销 | 前后 graph、保持的目标与 GREEN 内容、原因、成本、可恢复结果 |
| 完成 | 完整 theory/session build、export/publication、目标覆盖、retained semantic impact |

结构测试验证 coverage、引用和状态；真实 prover 测试验证命题、证明和导出；小型流程验证
交接。三类证据不互相替代。Codex 对话监视可评估 helper intent，不代替证明正确性判断。
旧文档中的 324 tests、PASS/FAIL、旧 `plan_run_file` 缺陷和绝对产物路径不在本分支复用。

本次仅检查文档逻辑、相对链接、函数入口和 Git 差异，不执行模型生成、Isabelle build 或 Python 回归。
