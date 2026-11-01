# intervaltree Ours 重新生成实验

当前阶段：生成阶段已在 2026-10-29 18:41:26（北京时间）达到 18,834 秒实际时间预算并暂停；26/91 个目标 GREEN、2 个辅助引理 GREEN、1/12 个理论封存，仓库验证未完成。

- 实验编号：`intervaltree-project-astra-low-20261029-001`，机器 B 独立目录。
- 所有规划及证明角色：`gpt-6-astra / low`。
- 固定 91 个目标，与原 VERO / Ours 输入逐项一致；重新进行仓库规划，从零生成，不复用旧的 13 条证明或旧规划。
- 使用已验证的封存适配修复发布包；原实验与预算记录保留。
- 新实验生成阶段独立实际时间预算为 **18,834 秒（5 小时 13 分 54 秒）**，与 VERO 对齐；规划单独计时。生成开始后截止时间包含停机，不退还预算。
- `sketch-reviewed-v1`，document 接口，1 个理论工作进程；原生检查 1 槽位、2 线程、4 CPU / 8 GiB，ML 堆 2 GiB。
- 重用且验证了 12 个冻结父会话堆及日志文件；没有复制任何目标证明、生成堆或旧的规划输出。模型工作容器不挂载旧实验。
- 本次验证固定形式化模型中的性质，不等价于 Python 源码与模型之间已建立精化证明。

记录见 [来源与输入验证](fresh-run-provenance.json)、[模型配置](model-config.json)、[规划策略](planning-policy.json)。远端完整路径：`/data/zljj/regen-runs/intervaltree-project-astra-low-20261029-001`。

完整启动前检查覆盖 91 个目标声明、通用 HOL 测试的完整检查、原生依赖审计、语义影响审计、封存构建及证书重放，均通过；未人工证明任何数据集目标。详见 [预检查结果](adapter-preflight.json)。

## 启动状态：认证阻塞

首次规划在模型认证阶段返回 `401 token_expired`，随后刷新失败为 `refresh_token_reused`。规划进程约 21.66 秒后退出，未提交有效规划；没有启动证明生成或注册 18,834 秒生成预算。完整原始调用保留在机器 B `planning/`，摘要见 [规划调用记录](planning-auth-failure.json)。

自动审批拒绝了将本机当前 Codex 登录凭据复制到机器 B 的动作：跨主机传输敏感凭据未得到明确授权。传输未执行，等待用户授权或自行更新机器 B 登录。旧实验仍保持暂停。本次不是证明失败或封存回归。

## 认证恢复与重新启动

用户明确授权传输当前 Codex 凭据后，已核验同账号、更新实验专用认证文件并设置 0600 权限；未输出令牌。新服务为 `intervaltree-project-20261029-001-launch-002.service`，规划输出写入 `planning-002/`。原 `planning/` 认证失败记录不覆盖。模型与预算配置不变，详见 [认证恢复记录](auth-recovery.json)。

已确认更新凭据后的 `gpt-6-astra / low` 返回有效规划响应，并开始读取固定目标与查询语义上下文；服务处于运行状态。当前仍为仓库规划阶段，证明生成尚未开始。

## 2026-10-29 20:06 后结果核查（北京时间）

新规划已完成并冻结：耗时 623.84 秒（10 分 24 秒），91 个目标分为 12 个理论；第一次认证失败调用约 21.66 秒额外保留，不计入本次成功规划耗时。规划与原失败实验独立。生成从 13:27:31 开始，18:41:26 达到实际时间预算并停止，账本原因 `generation_wall_time_budget_exhausted`；服务 PID 为 0。`phase-status.json` 的 generation 只是启动交接记录，当前状态以预算账本及服务实况为准。

| 分组 | GREEN / 总目标 | 封存情况 |
|---|---:|---|
| VERO 基线目标 | 22 / 56 | 全部来自 Interval_Normalization，22 个目标全部封存 |
| 新增抽象扩展 | 0 / 9 | 尚未产生通过结果 |
| 具体实现／精化层 | 4 / 26 | Tree_Execution 的 4 个通过，所在理论未封存 |
| 合计 | 26 / 91 | 1 / 12 个理论封存 |

另有 2 个辅助引理 GREEN，不计入 26 个固定目标。Tree_Execution 共 8 个目标，通过的是 `tree_remove_content`、`tree_add_content`、`tree_metadata_depth_exact`、`tree_rotation_content`。未完成的是 `tree_add_fuel_adequacy`、`tree_remove_fuel_adequacy`、`tree_add_preserves_wf`、`tree_remove_preserves_wf`。其他 10 个理论共 61 个目标尚未启动。未完成不能据此判定 spec 为假或条件不足。

账本中的有效时间分别约为 Interval_Normalization 2,338.27 秒、Tree_Execution 16,173.58 秒；总有效时间 18,511.85 秒，不能与包含全部停机及排除阶段的实际预算 18,834 秒混同。Tree_Execution 期间记录了 5 次 HTTP 429，5 次对应重试均成功，相关成本保留。本次确认封存适配已正常工作，但仓库集成尚未完成，不能将 26 个局部 GREEN 称为仓库证明完成。

只读核查了不可变接受记录和预算账本，未恢复实验、延长预算、修改目标或注入证明。状态快照见 [budget-exhausted-status.json](budget-exhausted-status.json)，规划见 [冻结方案](planning/frozen-plan.json)和[目标分配](planning/planner-proposal.json)。

## 对比审计补充

进一步检查会话发现，此前“主要耗时在树操作证明上”的概括遗漏了关键问题：模型已在 Isabelle 局部检查中构造删除保持良构性的反例，但控制器持续催促同一理论完成，末次工具调用后的重复交互尾段合计约 2 小时 32 分。VERO 多证明的 56 条全部未在 Ours 中启动，而 Ours 另证明了 VERO 未完成的两条。反例尚未发布为独立证伪证书，正向结果仍为 26/91。完整分析见 [对比审计](comparison-audit/REPORT.md)。
