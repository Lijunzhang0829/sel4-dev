# 证明体编辑、声明事实审计与超时迁移

## 证明体编辑

document 接口提供 `read_entry` 和 `edit_proof`，并行 File Planner 和 generic Codex agent 使用同一接口契约。

```json
{"action":"read_entry","entry_id":"goal:..."}
```

读取返回声明、context、当前 proof body、位置与 `expected_hash`。该 hash 绑定整个当前文档；任何其他 entry 的修改也会使它失效。读取不提交候选证明检查，但冷工作区可能需要初始化。

```json
{"action":"edit_proof","entry_id":"goal:...","proof_body":"by simp","expected_hash":"<read_entry 返回值>"}
```

框架只替换指定 proof span，逐字保留声明、context、边界标记及其他条目。编辑仍经过原来的完整解析、固定 contract 校验、helper/sketch 元数据检查与评审门控，成功后才原子保存。保存草稿不发布 GREEN；仍需原生检查及既有提交流程。

`edit_theory` 保留给结构调整。精确文本匹配失败返回 `DocumentEditError` 的结构化详情，包括从零开始的 `edit_index`、`match_count`、匹配位置、关联 entry IDs 和当前文档 hash。多项编辑中后续失败不会保存前面的临时修改；`positions_basis` 指明位置基于原稿还是顺序应用后的临时稿，offset 单位为 Unicode code point。

旧工具 registry 仅通过精确的历史 schema 变换兼容，未知差异仍拒绝。待封存时允许读取 entry，仍阻止修改。

## Isabelle 事实审计

目标声明前记录 global facts 快照，在同一声明 context 内完成证明后解析新导出的事实。优先候选来自 Isabelle 自身的 `Local_Theory.full_name` / `Sign.full_name` 和 `Facts.intern`；只允许相对快照新产生且唯一的 global binding。没有短名后缀搜索或同名旧事实兜底。

审计使用 `Global_Theory.get_thms` 的导出结果：locale 参数与 assumptions 由 Isabelle 正常导出，目标不得残留局部 hypotheses。多定理 bundle 全部检查；缺失、旧绑定、歧义和空 bundle 均拒绝。完整封存仍包含全局 automation snapshot 以及原有依赖、oracle 等检查。

`check_sketch` 使用相同解析器，但其 helper admission 仍只存在于隔离的 conditional transaction，不能发布 GREEN。新 receipt 标记 `audit_policy=declaration-export-audit-v1`；旧策略 certificate 在完整性校验后视为过期，触发重新检查，保留历史 receipt。当前策略的 source 不匹配仍报错。

新封存凭据也绑定 `audit_policy=declaration-export-audit-v1`，只接受新审计源码。已有、完全不含该字段的 v3 封存凭据仅能按冻结旧 renderer 逐字回放；未知或空策略值拒绝，原有状态、imports、原生 source/log、依赖收据和 build 校验全部保留。这是复用历史验收证据，不代表重新运行了新审计。更早的弱格式凭据仍需显式重新封存。

## 300 秒候选检查及已有预算续接

本机 CRefine 入口 `regen/config/crefine-owner-blind-sol-xhigh-local.sh` 的 `--candidate-timeout` 为 **300 秒**。外层 `--check-timeout` 仍为 900 秒，provider build 为 1800 秒；这些计时器对应不同阶段。

已有运行通过 `regen.tools.parallel_repair_continuation` 准备新的冻结应用续接：先停止并核验源服务，核验 owner-blind 数据摘要、Sol/xhigh 配置、历史时钟审计与已结算检查任务，再完整 fork 工作区。目标目录末级名必须提供新的 run identity。

预算迁移记录 120→300 的授权、源账本尾及新旧配置 hash，保留原有有效用时、规划费用、失败请求退款和 172800 秒上限。继承的历史时钟审计须逐条对应已验证祖先，不重新退款。该准备工具不自动启动服务；启动使用新冻结应用的 `parallel_workspace_service`。

## 验证

- 编辑和传输：`test_document_proof_edits`、`test_document_edit_transport`、`test_document_registry_compatibility`。
- 审计与 certificate：`test_native_fact_audit`、`test_multifact_audit`、`test_sketch_check`。
- 续接与历史账本：`test_parallel_budget_continuation`、`test_parallel_repair_continuation`、`test_provider_feedback_continuation`。
- 独立原生验证入口：`python3 -m regen.tools.native_fact_audit_smoke --help`。2026-10-27 本机验证覆盖 21 项原生正反例及 2 项空名入口拒绝；完整 CRefine automation 封存检查耗时约 147 秒，使用 900 秒外层上限。证据见 `reports/regen/native-fact-audit-smoke-20261027-004/validation-summary.json`。这些是 harness 验证，不增加实验 GREEN 数量。
