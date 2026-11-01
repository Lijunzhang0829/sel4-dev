# intervaltree 项目生成实验

当前阶段：已按用户要求暂停，封存适配修复已在机器 B 的隔离副本中通过完整验证。原实验保留 13/91 个局部 GREEN、0/13 个已封存理论；隔离验证未发布到原实验，未恢复生成。

## 固定配置

- 数据：与 VERO 相同的 91 个目标，陈述及 sanitized 输入文件逐项哈希校验。
- 模型：所有角色均为 `gpt-6-astra / low`。
- 方法：项目现有 `reviewed_planning` → `proof_session_runner parallel-workspace`，`document` 接口和 `sketch-reviewed-v1`。
- 比较口径：生成阶段实际时间上限 **18,834 秒（5 小时 13 分 54 秒）**；参考 VERO 的实际生成时间为 18,834.104907751083 秒。仓库规划、审阅和修订单独计时。
- 局部规划、辅助引理、修订、模型请求、原生检查及等待均消耗实际时间；原有有效耗时账本另行保留。截止检查同时约束新 GREEN 发布。
- 一个理论工作进程；原生检查一个槽位、2 个线程、4 CPU / 8 GiB、ML 最大堆 2 GiB。
- 不声称尝试次数相等。两套方法的 helper/修订组织不同，本次按时间匹配。
- 资源差异：项目模型进程另有 2 CPU / 2 GiB 容器限额；VERO 是模型与检查器共用 4 CPU / 8 GiB 容器。

## 隔离与适配

机器 B 路径：`/data/zljj/regen-runs/intervaltree-project-astra-low-20261028-001`。

只迁移 sanitized 定义、固定目标和项目程序；模型容器仅能访问公开输入及受审计工具，不能访问准备阶段证明或 VERO 的结果。父堆从 Pure/HOL 重新构建，未复制镜像中的 Refine/CRefine 堆。

新增 `Intervaltree_Project_Base` 仅导入原父会话并声明空 `l4v_word_context` bundle，兼容既有渲染器，不增加目标定理或公理。原生静态事实导出保留 25,353 条；它是经过静态来源筛选的查询目录，不能与 VERO 的未同样筛选目录条数直接比较。

初次适配检查通过：91 个目标声明、通用 HOL 基础测试的原生证明审计和封存构建；当时未覆盖语义影响审计及完整封存链，后续暴露了元数据遗漏。基础测试不进入数据集或生成器上下文。相关 96 项回归测试通过，人工未补任何数据集目标证明。

## 规划与运行记录

远端保留：

- `planning-002/draft/`：模型输入、查询、原始响应、初稿和原生预检查。
- `planning-002/review/`、`revision/`、`final-review/`：审阅、修订及最终风险。
- `planning-002/frozen-plan.json`、`comparison.json`：冻结方案、目标分配及修改对比。
- `planning-002-timing.json`：规划总耗时；`proof/experiment.json`：生成调用配置。
- `proof/effective-generation-budget/`：有效耗时和独立实际截止时间的持久化账本。

以上后续阶段文件由流程完成相应阶段后生成，不代表目前均已存在。

初次规划在补充回归发现封存回放适配遗漏后被中断；原调用及程序版本保留，未复用部分输出，未启动证明生成。修复后以 `planning-002` 新目录重启，详见 [operator-adjustments.json](operator-adjustments.json)。原规划成本应作为额外准备/中断成本列出，不能从记录中删除。

## 已完成的规划结果

规划、审阅、修订及最终审阅共耗时 **641.98 秒（约 10 分 42 秒）**，不包含首次中断的规划调用。初稿 12 个理论，最终 13 个，91 个目标恰好分配一次。

| 理论 | 目标数 | 必须先封存的理论 |
|---|---:|---|
| Interval_Normalization | 13 | 无 |
| Canonical_Measure | 1 | 无 |
| Tree_Executor | 8 | 无 |
| State_Update_Interface | 7 | 无 |
| Interval_Cutting | 11 | 无 |
| Tree_Queries | 6 | 无 |
| Normalization_Algebra | 11 | Interval_Normalization |
| Interval_Run_Geometry | 15 | Interval_Normalization |
| Interval_Measure | 3 | Interval_Normalization, Canonical_Measure |
| Normalized_Cutting | 5 | Normalization_Algebra, Interval_Cutting |
| Repeated_Cutting | 3 | Interval_Cutting, Canonical_Measure |
| Interval_Components | 3 | Interval_Run_Geometry |
| State_Invariants_And_Traces | 5 | Tree_Executor, State_Update_Interface, Tree_Queries |

修订新增独立的 `Canonical_Measure`，让重复切割不必等待整个 normalization/merge 度量分组。树操作与燃料充分性暂时共同封存；最终审阅还记录了 touching 连通关系与严格 overlap 之间需要建立桥接的局部证明任务。这些是规划风险和待证义务，不是已证结论。

详见 [planning-summary.json](planning-summary.json)、[最终审阅](planning/final-review.json) 和 [冻结凭证](planning/frozen-plan.json)。完整规划记录（含首次中断调用）的本地归档位于 `intervaltree/build/intervaltree-project-planning-records-20261029.tar.gz`，机器 B 同样保留原始目录。

## 生成启动与恢复

原生预算于北京时间 **2026-10-29 02:11:39** 注册，截止时间 **2026-10-29 07:25:33**。首个服务因主 Python 缺少 `jsonschema` 在模型调用前暂停；改用机器 B 已有 Python 3.13 venv，于 **02:13:37** 开始实际模型会话 `Interval_Normalization`。

恢复使用原 run、原规划、原账本；实际截止时间仅注册一次，未重置或延期，故约两分钟依赖恢复时间也消耗此次实际时间预算。详见 [恢复凭证](generation-resume-001.json)。当前权威服务为 `intervaltree-project-20261028-001-generation-resume-001.service`，不要用首次服务的退出状态判断当前进展。

本次验证的是移植模型中的固定性质，不等于证明 Python 源码与模型完全等价。

## 2026-10-29 03:24:05 进展核查

- 固定目标：13/91 GREEN，均来自原 VERO 基线的 `Interval_Normalization`；扩展 0/9，具体实现层 0/26，其他 78 个目标尚未开始。
- 13 个目标于北京时间 02:29:45 左右全部 GREEN，包含规范形式、覆盖保持、排序、严格间隔、非空输出、长度界、端点去重和覆盖次数性质。
- 从 02:30:33 起封存反复返回 `rejected_request`，诊断为 `'campaign_id'`；已确认 63 次同类拒绝，持续约 52 分钟。19 次完成的 proof_batch 是批量提交数，不是逐目标尝试次数。
- 根因：新增 intervaltree 准备适配未在 `premise-audit-context.json` 写入 `campaign_id`，而 `retained_semantic_impact.audit_theory` 强制读取该字段。这是适配/调度问题，不是目标被证伪或证明尝试耗尽。
- 0 个理论封存意味着下游不能采用这些结果；单工作进程尚未移到其他理论。服务活跃不等于有效推进。原实际截止时间仍为 07:25:33，未重置。
- VERO 已完成结果为 80/91，本项目当前只是被基础设施故障干扰的中间快照，不能据此得出证明能力比较结论。本次只核查并记录状态，未改变远端运行或预算。

## 2026-10-29 03:57:53 进展核查

目标仍为 13/91 GREEN，封存 0/13，其余 78 个目标尚未开始。`campaign_id` 缺失仍未修复，已确认至少 112 次同类封存拒绝；完成的证明批次仍为 19。进程活跃但结果未增加。预算已用约 1 小时 46 分，原截止时间 07:25:33 不变。本次仅记录状态，未修改远端配置或预算。

## 2026-10-29 暂停与封存适配修复

北京时间 03:59:29 按用户要求停止生成服务。账本记录 `operator_pause`，进程 PID 为 0；systemd 显示 failed 是 runner 暂停退出的服务状态，不表示仍在运行。保留原规划、全部目标陈述、已接受证明及预算账本，未重置尝试或耗时。暂停凭证见 [operator-pause-20261029.json](operator-pause-20261029.json)。

修复包含：

- 准备阶段为 intervaltree 审计上下文写入稳定的 `campaign_id`，并把审计文件哈希绑定到 checker context。
- 加载环境时检查必需字段和哈希绑定；完整封存开始前再次检查。
- 缺失或损坏的审计元数据抛出 `NativePreparationError`，由现有基础设施错误通道退出，避免被当成模型可重试的证明请求拒绝。
- 启动前检查改为调用完整 `seal_theory` 流程，覆盖语义影响审计和封存证书重放。

在原冻结 application 发布包基础上应用本次最小修复，部署到机器 B 的 `seal-repair-20261029-001/`。原 application、原 dataset 和原 proof 目录未覆盖。修复发布包 SHA-256 为 `b4730a0f90fb0af4d695b0fa470000b18a777f615380923f1ba73b584000ac14`；[补丁](seal-repair-20261029-001/repair.patch)及[发布清单](seal-repair-20261029-001/application-manifest.json)已归档。

本地与机器 B 修复发布包的相关回归测试均为 **40 项通过**。机器 B 于北京时间 04:06:52 完成隔离原生验证：复用 `Interval_Normalization` 的现有 13 条已接受证明，完整证明检查、原生依赖审计、语义影响审计、封存构建和证书重放全部通过。模型调用 0，新增目标证明 0；校验原实验 1,028 个源文件哈希未变。详见 [验证结果](seal-repair-20261029-001/validation.json)、[测试日志](seal-repair-20261029-001/unit-tests.log)和[上下文修复来源](seal-repair-20261029-001/repair-provenance.json)。

隔离证书未发布到原实验，因此原实验计数仍是 13 GREEN、0 个封存理论，仓库集成未完成。实验保持暂停，不自动恢复。原实际时间策略包含停机时间，绝对截止时间仍为北京时间 07:25:33；暂停并未冻结或延长该截止时间。暂停时的剩余时间只作历史快照，不能当作未来恢复时仍可用的额度。
