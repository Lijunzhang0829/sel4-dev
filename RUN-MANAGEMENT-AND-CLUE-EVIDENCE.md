# 运行管理器与线索管理：基线反馈证据

本文整理“仓库级证明生成中，运行 manager 与持久线索/证据管理是否必要”的代码与
运行日志证据，供论文正文或附录引用。范围限于 Isabelle / seL4 仓库级证明生成。

## 1. 目的与范围

- 记录 baseline（VERO / official-Codex 共享 harness）在生成阶段的运行状态管理现状。
- 从 baseline 自身运行日志中提取可复现的失败模式。
- 说明 ours 的 manager / 线索管理机制针对的是哪些已观察到的工程低效，而不是未经验证的设计偏好。
- 明确“必要”的边界：不是要求 baseline 复制 ours 的完整体系，而是要求其达到可公平比较的最低运行管理能力。

## 2. baseline 已有的运行管理（避免夸大）

baseline 并非完全没有 manager，已有：

- `ContractRetries`：per-contract 初始尝试 + 有限重试，见
  [retry_policy.py](vero-isabelle/src/vero/isabelle/retry_policy.py)。
- resource-v2 控制器：`ResourcePlanLedger` 管理 plan / seal / integrate，见
  [resource_plan.py](vero-isabelle/src/vero/isabelle/resource_plan.py) 与
  [resource_controller.py](vero-isabelle/src/vero/isabelle/resource_controller.py)。
- `progress` / `feedback` / `attempts` / `sampling`：候选占用、诊断与快照。

真正缺的是**仓库级、细粒度的运行 manager**，以及**跨轮次持久化的线索/证据 ledger**。

## 3. baseline 运行日志暴露的失败模式

以下均来自 [WORKLOG.md](vero-isabelle/WORKLOG.md) 与
[TODO.md](vero-isabelle/TODO.md)，不是推测。

### 3.1 目标污染

> all 287 target proof bodies imported from upstream/old l4v by agent; run is
> contaminated for from-scratch comparison.

后果：agent 拿到原始人类证明，运行结果不能作为 from-scratch 对比证据。
说明缺少运行期“只能看合法输入、不能取原始证明”的持续管理。

### 3.2 粗粒度失效与冷检

> reproduced whole-theory invalidation of unchanged earlier GREEN in an isolated ledger

> repository-wide declaration invalidation, forced cold checks

后果：一处修改导致整个 theory 失效，已 GREEN 节点被重复冷检，预算被结构性浪费。
说明缺少细粒度依赖图、prefix 缓存和按传递依赖失效的机制。

### 3.3 状态写放大与轮询失控

> 67 MB repeated controller persistence, >59 GB runtime writes

> excessive model-driven status polling

后果：运行状态管理本身成为主要开销，模型反复轮询状态而不是利用增量反馈。
说明缺少 compact manager、增量日志和有界状态查询。

### 3.4 大量无增分尝试

> 952 charged attempts followed the final success without increasing the score

> 93 Codex targets above six native checks

后果：大量尝试没有推进任何目标，候选去重和反馈复用不足。
说明缺少“候选是否变化、反馈是否已消费”的线索管理。

### 3.5 规划超时与低于既有结果

> first PLAN exhausted 600 seconds at 207 contracts

> independent evaluation proved 35, with 3 module build errors and 249 unfilled.
> This is below the prior fresh 39 result.

后果：规划阶段失败后缺少结构化修正路径，生成成绩反而下降。
说明缺少 plan review、revision decisions 和 proof direction 建议等修正线索。

### 3.6 审计失败集中且难定位

> 138 charged audit-stage rejections across 23 targets

> assume_tac cannot handle non-atomic assumptions

后果：失败集中在少数目标，且根因需要额外审计定位。
说明缺少 proof-dependency 与 automation-context 审计线索。

## 4. 缺口 → 机制 → ours 实现映射

| 基线反馈 | 需要的机制 | ours 对应实现 |
| --- | --- | --- |
| whole-theory invalidation / forced cold checks | 细粒度依赖图 + prefix 缓存 + 传递失效 | `proof_graph`、persistent prefix worker、revision invalidation |
| 67MB / 59GB 状态写放大与轮询 | compact manager、增量日志、资源 lease | `incremental_log`、`check_executor`、`check_protocol` |
| 952 次无增分尝试 | 候选去重、反馈复用、checkpoint candidates | GRA feedback、`checkpoint_candidates`、evidence 复用 |
| 规划超时/低分 | plan review + revision decisions + 方向建议 | `plan_review`、`revision_decisions`、`proof_direction_suggestions` |
| 审计失败无法定位 | dependency + automation 审计线索 | `audit_complete_theory`、`automation_context_audit_ml` |
| 目标污染 | 运行期隔离 + owner-blind 输入 | sanitized runtime、owner-free canonical input、model isolation |

主要实现位置：

- [proof_session_runner.py](regen/execution/proof_session_runner.py)
- [repository_execution.py](regen/execution/repository_execution.py)
- [proof_graph.py](regen/planning/proof_graph.py)
- [incremental_log.py](regen/infrastructure/incremental_log.py)
- [check_executor.py](regen/execution/check_executor.py)
- [check_protocol.py](regen/execution/check_protocol.py)
- [plan_review.py](regen/planning/plan_review.py)
- [revision_decisions.py](regen/planning/revision_decisions.py)
- [retained_semantic_impact.py](regen/analysis/retained_semantic_impact.py)
- [isabelle_node_checker.py](regen/execution/isabelle_node_checker.py)

## 5. 必要性结论

1. **必要**：基线日志显示，缺少细粒度依赖管理和持久线索/证据管理，直接导致重复冷检、
   状态写放大、无增分重试和规划失败无法有效修正，拉低有效吞吐和可归因性。
2. **但有边界**：必要不等于要把 ours 的完整 manager 体系搬到 baseline。对公平对照而言，
   最低要求是让 baseline 具备：
   - 细粒度失效（不要 whole-theory 全量失效）；
   - 候选去重和反馈复用（不要 952 次无增分尝试）；
   - compact 状态与有界查询（不要 59GB 写放大和轮询失控）；
   - 结构化规划修正与审计定位（不要 plan 超时后低效重来、审计失败难定位）。
3. **论文表述建议**：将 baseline 的差距表述为“工程运行管理成熟度差异”，而不是“方法能力差异”；
   同时说明 ours 的 manager / 线索管理是面向已观察到的 baseline 失败模式设计的，而非事后装饰。

## 6. 证据来源

- 运行日志：[WORKLOG.md](vero-isabelle/WORKLOG.md)
- 待办与已知限制：[TODO.md](vero-isabelle/TODO.md)
- baseline 管理代码：[retry_policy.py](vero-isabelle/src/vero/isabelle/retry_policy.py)、
  [resource_plan.py](vero-isabelle/src/vero/isabelle/resource_plan.py)、
  [resource_controller.py](vero-isabelle/src/vero/isabelle/resource_controller.py)
