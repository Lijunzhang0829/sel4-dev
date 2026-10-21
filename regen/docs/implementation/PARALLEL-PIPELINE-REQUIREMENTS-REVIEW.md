# 并行证明流程需求复核

> 后续设计已收敛为单 File Planner 工具串行、跨 theory 并行。下文保留历史异步问题及修复记录；环境冻结和强杀恢复均已退出当前验收要求。当前流程见 [主设计](../design/PARALLEL-PROOF-PIPELINE.md)。

日期：2026-09-25（UTC 日志日期）。基线：当前 `shared-proof-workspace` 工作区；这是检查与轻量实验，没有修改实现、旧运行或调用生成模型。

最新状态：环境冻结要求由用户撤回；下面两项工作记忆问题已修复并重跑原始案例。SIGKILL 后进程回收仍是明确 TODO。原复核与本次修复均未调用生成模型，不代表完整模型实验验收。

## 确认问题

### 环境内容冻结要求已撤回

用户明确由人工保证实验环境不变，不要求代码冻结或检测父资产漂移。原 P1 不再作为问题或待实现功能；运行时环境内容扫描已删除。已有环境身份字段保留作来源记录和旧产物兼容，路径配置、数据库完整性和证明验收仍需检查。

### P2：中断恢复回滚已持久化的较新 workspace（M5/M7）

位置：`planning/batch_file_planner.py:437`；`planning/async_observations.py:50`。

复现顺序：proof 写 old → observe 写 new → 下一次 proof 持久化 new → KeyboardInterrupt 记录 → 继续 proof。恢复后 workspace 从 new 回 old，而较新的选择版本与已应用 task 集合仍保留，故观察归并不能自行纠正。

根因：旧 action 的恢复 reducer 仍按证明 request 编号覆盖选择，没有遵守新的异步逻辑顺序。需让正常执行与恢复使用一致的字段顺序规则。

### P2：成功提交未统一保护 workspace/notes 的顺序（M5）

位置：`planning/batch_file_planner.py:1095–1102, 1139–1140`；`planning/async_observations.py:46–78`。

慢 observe 提交 old notes，后发 proof 提交 new notes 并完成；observe 最后返回，notes 回退到 old。成功 GREEN 分支在 1095 行 apply_feedback 后提交并提前返回，绕过 1139 行顺序标记；后者也只为 workspace 标记，漏掉 notes。反向交错同样复现：先提交的 proof 暂未启动，新 observe 完成并选择 new workspace，旧 proof 随后启动并成功提交后，workspace 又变 old，而顺序仍指向新 observe。需在成功、失败与恢复路径统一对全部工作记忆字段排序。

以上两项通过真实 TheoryController、线程、journal 和任务收据复现，native proof evidence 使用单元测试合成夹具；它们不是新的真实数学证明实验。

### P2：强杀后遗留检查进程不计入恢复资源额度（M6）

位置：`execution/check_executor.py:124–126, 174–186`。

临时协调器通过 native_run 启动本地子进程，协调器被 SIGKILL 后，子进程仍存活。重启 executor 把任务标为 interrupted，新的 NativeResources 空表立即允许新 lease；配置 native_slots=resident_limit=1 仍无法约束实际遗留负载。

应持久化可验证的进程/容器归属，恢复时先核对与清理或纳入占用，再开放新额度。清理需校验归属，不能仅凭可复用 PID 杀进程。本例仅真实本地 Python 子进程，未冒充 Docker/Isabelle 强杀实验；实验子进程已清理。普通受控取消的测试通过，问题特指无法执行 finally 的强杀恢复。

## 已通过与验证边界

- 全量回归：721 项，31.713 秒。
- 冻结全集删改拒绝、不可变 contract 与已接受 proof、batch evidence 校验、seal 与集成分层均有测试覆盖；正常 agent 工具路径未发现绕过。
- 真实 Isabelle 通用混合检查：不同上下文、同名 lemma，native slots=1 用时 24.307 秒，slots=2 用时 13.306 秒，均通过。只说明该组冷检查能并行隔离，不能外推整个生成流程加速。
- 该案例零模型调用、零目标 benchmark contract，不是两个 File Planner 从零生成实验。未重跑完整 native seal 生命周期。
- 未接受 raw check 的独立重新提交、journal 索引纯增量化、完整模型实验与 RSS/CPU 评价仍为原设计明确保留的 TODO，不当作新发现。
- seal replay 的独立证据验证可进一步加固，但目前需要伪造内部证据或改写 journal 才能触发，未作为本次正常运行缺陷。

## 证据

目录：`reports/analysis/parallel-pipeline-requirements-review-20260925/`。

包含全量回归日志、native-smoke/report.json、五个可重复的临时案例脚本及日志、被审代码 SHA256。旧复验报告记录当时状态；本报告补充其测试未覆盖的条件，不把此前正常路径修复视为无效。

## 后续小修

`apply_feedback` 为成功/失败提交及恢复统一应用任务顺序，workspace 与 notes 使用相同规则；每次证明动作的顺序随 journal 状态记录。无任务顺序的旧反馈不能覆盖已有异步选择。三项原始复现均不再回滚：迟到 notes、先提交后完成的 proof workspace、持久化后中断恢复。它们已纳入 `test_controller_memory_ordering.py`。证明计数、图与 Isabelle 验收标准未变。

本次修改后完整回归：723 项通过，31.983 秒；日志与三项原始复现修复后的输出见 `reports/analysis/operator-environment-memory-fixes-20260925/`。未重新调用模型或运行 native seal 实验。


## 2026-09-25 串行化后的文档与代码复核

当前结论：正常串行控制流符合最新设计；不同 theory 继续并行。全量 723 项回归通过（31.830s），另用真实控制器与执行器、模拟检查体证明：Target 第一项阻塞时 Source 可执行，Target 第二项必须等待第一项结束，提交均在终态返回。没有启动生成模型或新的 Isabelle 实验。

主设计与实现记录一致。已修正四处旧导航/状态：共享 workspace 页不再把新版功能标为 TODO；历史 TODO 索引改为当前串行/跨 theory 并行；README 撤除重新冻结环境要求；仓库规划页标记 owner-visible prompt 已修复。未修改本轮检查的执行实现。

仍有两个反馈接口缺口：

- **P2：statement 长诊断无法完整读取。** `parallel_file_planner.py:107–111` 对提交和 get_check_result 都压缩诊断，超过 12000 字符只返回前缀，提示 read_diagnostic。但 `batch_file_planner.py:631` 仅接受历史证明 request_number；statement 只有 task ID，不进入该证明历史。复现中重复读取仍截断，分页返回 unavailable。需要允许按 task ID 分页读取收据中的诊断，校验归属与结果摘要，不增加证明决策编号。
- **P3：statement 的 notes/workspace 静默丢失。** 提示说明任意 action 可携带持久选择；statement 分支也接受且验证 workspace，却被 `_observation_state` 的 task kind 过滤排除，read_theory 不显示这两个字段。应让串行声明检查的显式工作记忆也进入统一记忆投影；它仍不提交候选图或建立证明事实。

两项均用 synthetic checker 和真实 Controller/task 收据复现，没有造成虚假 GREEN。另观察到显式选择旧事实后，新查询事实只在索引中保留；模型已经收到 query 原始返回且可显式读取，暂列为工作集选择的一致性优化，不列阻塞缺陷。

证据：`reports/analysis/serial-doc-code-review-20260925/`，包含回归、两个复现脚本和输出、代码摘要。环境内容冻结、强杀资源回收仍按用户决定不属于当前验收要求。
