# Repo / Session 级规划：现有实现与扩展边界

状态：静态核对本分支代码；历史模型运行结果见 [实验记录](REFINE-CREFINE-EXPERIMENT.md)。
本文件描述现有调度器，[Architect Experiment](UNIFIED-PROOF-ARCHITECT-EXPERIMENT.md)
描述待扩展的仓库证明规划，两者不能混作同一已实现能力。

## 1. 当前做什么

[session_planner.py](session_planner.py) 从固定 Refine contracts 推断 theory 生成优先级，
输出分层、串行 fallback 和 session ledger。它不产生全局 helper、精确 theorem statement、
imports 或完整 repository proof architecture。

当前模型输入包含 public contracts、owner slots、environment gate 和 catalog 身份／规模，
不包含 individual premise records、retrieval results、人类 helpers 或 import graph。
规则来自 [session-planning-policy.json](config/session-planning-policy.json)，当前配置的模型为
`gpt-5.6-sol`；这里仅记录仓库配置，不对外部 provider 的实时可用性作判断。

拟议的 Planning Substrate 会让仓库规划读取合法语义记录，这是对当前输入政策的明确扩展。
本次只更新文档，没有更改配置、prompt 或接线。File Planner 的 `planning_environment()`
目前也仍为 fixed-contracts-only，未开始消费 substrate 投影。

## 2. 候选、验证与顺序

模型提出两类候选：`contract-composition` 和 `operation-call`。每项包含 provider、consumer、
contract citations、shared symbols、confidence 和 reason。确定性代码检查当前 endpoints、
两端 citations、完整符号 token、重复边及 policy 限制，再形成最终 schedule。

- `contract-composition` 是用于分层的 strong scheduling edge。
- `operation-call` 是同层排序的 weak scheduling edge。
- 两者都是从目标表面获得的调度证据，不是已证明的 theorem dependency。

SCC 折叠用于生成顺序，不代表环形证明依赖合法。没有分层证据时可能只有一层，但这不能证明
文件独立或可安全并行。当前 Runner 使用 serial fallback，并在单 theory 失败后继续遍历。
未来仓库规划的 hard proof dependencies 必须单独约束可用性，不能直接沿用 SCC 折叠来消除它们。

## 3. 代码定位

| 责任 | 当前文件与入口 |
|---|---|
| Policy binding | [session_planner.py](session_planner.py) `verify_policy_frozen()` |
| 目标与 payload | [session_planner.py](session_planner.py) `generation_tasks()`、`planner_payload()`、`planner_prompt()` |
| JSON 与候选验证 | [session_planner.py](session_planner.py) `parse_model_json()`、`symbol_is_grounded()`、`validate_candidate_edges()` |
| 分层与顺序 | [session_planner.py](session_planner.py) `strongly_connected_components()`、`component_depths()`、`weak_order()`、`build_layers()` |
| Bundle 生成 | [session_planner.py](session_planner.py) `build_artifacts()`、`write_artifacts()` |
| 验证与重放 | [session_planner.py](session_planner.py) `validate_file_list()`、`validate_planning_bundle()`、`verify_existing()` |
| Ledger | [session_planner.py](session_planner.py) `initial_progress_ledger()`、`validate_progress_ledger()` |
| Transcript prefix | [llm_transcript.py](llm_transcript.py) `allocate_artifact_prefix()`、`reconcile_transcript_family()` |
| Transcript 接受 | [session_planner.py](session_planner.py) `select_completed_transcript()`；[llm_transcript.py](llm_transcript.py) `validate_call_artifact()` |
| Runner schedule 绑定 | [proof_session_runner.py](proof_session_runner.py) `schedule_entries()`、`schedule_binding()`、`init_run()` |
| Runner session 遍历 | [proof_session_runner.py](proof_session_runner.py) `_run_session_unlocked()`、`run_session()` |
| File Planner 输入政策 | [file_planner.py](file_planner.py) `planning_environment()` |

## 4. Transcript 与产物

一个逻辑规划调用可能包含多个 provider attempts，每次使用独立 prefix。已完成 transcript 的
复用需匹配实际 prompt、model 和审计条件；失败记录不覆盖。实际是否允许新外部调用，由入口
参数和冻结政策决定。

Session Planning bundle 包含：

| 产物 | 用途 |
|---|---|
| `dependency-evidence.json` | accepted/rejected candidates、调度边与 SCC |
| `file-generation-list.json` | 完整目标文件集合、层次和 serial fallback |
| `session-planning-audit.json` | 输入、coverage、policy 和 transcript binding |
| `session-progress-ledger.json` | Runner 绑定并更新的 canonical session 状态 |
| `transcript/session-planner.*` | 实际模型输入输出与审计证据 |

Runner 不能只接收孤立 schedule；它核对 bundle、environment gate、contracts 和 policy。
文件／contract 数量从本次 packages 重建，不把历史的 21/287 当成通用常量。

## 5. 完成状态的限制

`_run_session_unlocked()` 根据 ledger 中所有文件是否为 `sealed` 设置
`session_status=complete|partial`，输出的是 `session-traversal-result`。
它没有在这个末尾另外执行完整 session clean build 或产生新的统一 Session Seal。
因此不能把 traversal complete 直接称为满足目标设计的最终 session 验收。

仓库根部的 [session_seal.py](session_seal.py) 面向旧 `refine-arm-session` root-plan 流程，
不是当前 Semantic Base Runner 的最终验收入口。后续需要新验收接线，不能凭同名文件认为已完成。

## 6. 验证与历史证据

相关测试为 [test_session_planner.py](scripts/test_session_planner.py) 和
[test_environment_gate.py](scripts/test_environment_gate.py)；本次未运行。

历史 `d6c...` 与 `e7f...` 规划采用不同 environment identities，不能混称一个当前运行。
旧 d6c 的一层结果不能推出 contract-only 输入永远无法产生 strong scheduling edges；
e7f 的历史记录已有两层。历史记录均未在本次重新验证，也不证明完整 proof DAG 已找到。

当前代码范围为 Refine。独立 CRefine 和联合任务尚未选择；只有选择联合任务后，才施加
“下游绑定本次完整 generated Refine”的交接条件。
