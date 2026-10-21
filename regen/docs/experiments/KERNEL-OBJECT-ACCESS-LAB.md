# Kernel_Object_Access 单 theory 生成实验

2026-09-09。**交互式诊断完成；不满足无干预生成 setting**：七个固定 contract、八个 helper 及完整文件通过 Isabelle 检查，但证明与 helper 的生成包含外层 Codex 的直接干预。不能作为非干预成功案例。

## 实际执行流程及介入位置

1. 实验脚本继承此前诊断的五个 GREEN 节点，没有从全空证明状态重新运行 File Planner。
2. 外层 Codex 阅读 checker 的错误和残余目标，查阅合法定义，决定拆分目标及提出 helper。
3. 部分候选由单独 GPT-6 调用提出；外层 Codex 也直接编写、修改 proof body，包括具体规则选择、实例化、Isar 结构和 tactic。
4. 外层 Codex 将具体候选写入 `requests/*.json`。实验脚本只负责调用 checker、保存失败、对 GREEN 冷复核和提交状态。
5. 失败后返回第 2 步；helper 选择、候选修复、何时换目标均包含外层 Codex 决策，并非生产 GRA/Runner 自动完成。
6. 全部节点通过后，独立 finalizer 检查完整 theory、审计引用并导出文件。

因此，Isabelle 验证了这些证明的正确性，但没有验证生成过程符合无干预 setting。此前把此结果称为“任务完成”不准确。此诊断保留作流程研发证据，不能将其 helper、证明体、定向提示或 GREEN 状态喂给无干预新运行。

## 无干预运行边界（用户澄清后）

允许外层 Codex 修复结构 bug、改进对目标通用的检索/反馈/调度/预算/检查流程；不允许替目标写证明、设计具体 helper statement、挑选具体引理作为人工提示，或代替 GRA 决定图修改。模型角色可以在程序规定的输入与反馈闭环内自动完成这些工作。

每次修改执行代码后创建新 run，冻结代码、模型和预算，从允许的仓库投影重新自动规划；不导入此次诊断的 graph、proofs、acceptance 或产物。失败运行保留，不在原运行内替换候选。无干预成功需生产 Runner 的 complete check、dependency audit、生成 session build 和 seal 全部通过。该目标已经用于调试，因此即使无干预重跑成功，也应标记为开发目标上的回归案例，不宣称未见目标的泛化成绩。

## 设置与可复查产物

输入为 `reports/regen/projection-run-v3-002/run.json` 指向的冻结 Semantic_Base、合法 premise catalog 和已有 heap。继承 `reports/regen/input-repair-gpt6-001` 的五个已证明节点（两个 contract、三个 helper），继续生成剩余五个 contract 和五个语义 helper。没有读取已删除的人类 Refine/CRefine 证明或辅助命题。

这是 **Codex 交互式修复与 GPT-6 Astra 候选协作实验**，不能计为原 Runner 无人干预成功率。请求中的历史来源标签 `developer` 指 Codex 当前构造的候选，不指恢复的人类证明。没有改写原 campaign 的 GREEN、冻结预算或发布指针。

- 实验入口：`regen/tools/kernel_object_access_lab.py`。
- 请求、失败诊断、候选源码和检查日志：`reports/regen/kernel-object-access-lab-001/{requests,results,checks}`。
- 初始合同、不可变验收事务和可重建指针：同目录的 `initial.json`、`acceptance/`、`state.json`；重放实现为 `regen/tools/proof_lab_journal.py`。
- 最终完整检查、审计与导出入口：`regen/tools/finalize_kernel_object_access_lab.py`。
- 逐候选耗时：实验目录的 `attempt-costs.csv`、`cost-summary.json`；可用 `python3 reports/regen/kernel-object-access-lab-001/scripts/summarize.py` 重算。

## 证明策略与失败原因

原 setter 两个 helper 主要把已有假设打包，没有证明具体对象更新如何保持抽象/具体状态关系。缺失的不只是更多 premise 名称，而是可检查的中间语义命题。

最终采用以下拆分，helper 只有独立冷检查通过后才进入后续上下文：

| Helper | 作用 |
| --- | --- |
| `regen_lookupAround2_exact_hit` | 从精确映射命中得到 lookupAround2 的左端点 |
| `regen_local_cut_exclusion` | 利用 ps_clear 排除对象更新影响范围外的 cut |
| `regen_setObject_default_execution` | 展开 typed load / 对齐 / 区间检查，证明默认对象更新的执行结果和非失败性 |
| `regen_pspace_update_other` | 从局部 cut 排除推出 pspace 关系保持 |
| `regen_state_update_other` | 同步抽象和具体对象更新后保持完整 state_relation |
| `regen_map_to_ctes_other_update` | 非 CTE、非 TCB 对象替换保持 capability 投影 |

另外保留初始两个假设打包 helper；最终活动图共 15 节点。保留不等于已经证据确认采用。计划 dependencies 仍表示调度前置关系，已验证同 theory 前缀中的其他事实可以被引用，实际采用另由 Isabelle 依赖审计记录。

读操作先完成 endpoint，再迁移到 notification。写操作先证明通用 setObject，再将共享语义 helper 实例化到 endpoint/notification。最终七个 contract 的 statement、attributes 和 context 保持初始输入原样。

具体失败集中在：

- 输入没有覆盖残余目标所需的精确定义/缩写展开，例如 `opt_map_def`、`is_ep_def`、wordSizeCase 和 wordBits；仅补一个宽泛 tactic 不足以解决。
- 高阶映射和 `OF` 的隐式实例化出现多个 unifier；显式指定 map、地址和对象后解决。
- `Min` 成员关系需要有限、非空条件；字长边界需要明确的对齐和有序减法引理。宽泛 `word_bitwise` 产生庞大残余，`unat_arith` 超时。
- `auto` 没有自动完成完整状态的各个投影；显式分离 heap、TCB/capability 和 queue 关系，并控制 simp 展开顺序后通过。
- 部分尝试是 Isar 语法/命名修复，例如保留字、局部条件事实和构造器分情况，不代表命题本身困难。

因此，本次支持“补足语义拆分、精确输入、按残余目标修复”的价值；没有公平对照证明只提高五轮上限即可解决，也不能据此比较两个模型的整体能力。逐步修复发生在完整候选与 checker 反馈之间，没有冒称完成生产 stepwise backend 改造。

## 检查器与恢复改进

候选中的单条 Isabelle command 默认限制为 60 秒（checker 的 `persistent_command_timeout` 可调整）。命令超时返回普通 RED，记录耗时并保留不可变前缀供下一候选使用；进程/协议故障仍保留原冷 fallback。前缀准备和冷检查的实验进程预算独立设为 900 秒。任何 speculative GREEN 仍必须独立冷检查。

请求 005 曾耗尽旧 warm 进程预算并进入重复冷尝试，该冷尝试被停止，保留 RED/中止证据。改进后请求 020 在 60.206 秒返回超时 RED，没有重复冷 replay。`timeout-recovery-test/result.json` 另验证同一 worker、同一 prefix 中超时 RED 后可成功 GREEN；该测试命题没有进入活动证明图。

验收日志先保存包含旧状态哈希、候选、冷检查证据和新状态的不可变记录，再更新指针。启动时重放全部记录；指针丢失/滞后、result 未写时可以恢复。重放拒绝历史分叉、speculative GREEN、已有证明或合同接口变化。这是受限的追加 helper/证明实验协议，并未替代生产 GRA 的通用 revision/撤销事务。

## 实测成本

本轮记录 38 个候选：28 RED、10 GREEN；继承的五个 GREEN 不计入这 38 次。逐检查记录的 wall-time 累计如下，完整文件验收另计：

| 阶段 | 秒 | 分钟 |
| --- | ---: | ---: |
| 前缀准备 | 3069.205 | 51.15 |
| warm 候选执行 | 643.161 | 10.72 |
| 独立冷检查 | 2079.157 | 34.65 |
| 未归入以上字段的旧超时/fallback 开销 | 302.633 | 5.04 |
| 合计 | 6094.156 | 101.57 |

这是子进程记录的耗时累计，不是用户等待时间；不含继承节点、Codex 交互分析、审批等待、专用超时测试及最终完整检查。模型调用与 checker 有重叠，不能直接相加冒充总 elapsed time。

四个已执行 GPT-6 Astra 调用记录：high 规划 121.637 秒；另一次 high 在 300.112 秒超时；medium 两次分别 85.410、87.932 秒。high 超时不能视为证明 RED。后续状态更新外部调用被自动审批明确拒绝，未执行；改用本地 Codex 构造并交由 Isabelle 验证，没有绕过该拒绝。

当前仍按目标重建前缀，候选也重复执行局部 `interpretation Arch .`。命令计时中 `.` 累计约 331.6 秒，主要对应该 interpretation 的完成。这两项尚未优化完成。下一步可测量跨目标递增前缀及复用完整合法局部 context；必须继续保证事实清单、声明顺序和 context identity 一致，不能只省略初始化命令。

## 验收记录

七个 contract：`getCurThread_corres`、`getIdleThread_corres`、`getEndpoint_corres`、`getNotification_corres`、`setObject_other_corres`、`setEndpoint_corres`、`setNotification_corres`，均冷 GREEN；八个 helper 均冷 GREEN。

完整文件使用实际名称 `Kernel_Object_Access` 单独冷检查，并在同名上下文重新生成 proof dependency evidence，成功后才输出 manifest。最终目录：`reports/regen/kernel-object-access-final-001`。本实验复用上游 heap，不重建上游 session；独立 session heap build 不在此次完成声明中。

最终整文件冷检查 **GREEN，185.941 秒**；同名完整审计 **GREEN，208.203 秒**。审计覆盖全部 15 个节点，`has_skip_proof` 均为 false。导出文件 1008 行，SHA-256：

`9bb40994a0d3feddd064637f4d0f37285fe1a5c60ba291f9b5711f656a485b5d`

产物入口：[Kernel_Object_Access.thy](../../../reports/regen/kernel-object-access-final-001/Kernel_Object_Access.thy)、[manifest.json](../../../reports/regen/kernel-object-access-final-001/manifest.json)、[verification-summary.json](../../../reports/regen/kernel-object-access-final-001/verification-summary.json)。

六个语义 helper 均有证据确认采用；两个初始假设打包 helper 为“未确认采用”，不写成“未使用”。确认关系来自 Isabelle 导出的完整限定名，不来自 proof 文本匹配；不承诺恢复 simp/wp 的全部引用。

最终 291 项当前测试通过；额外真实 ML 超时恢复检查通过；10 条新增验收事务重放得到全部 15 个证明，源码/证明哈希与导出 manifest 一致。代码检查通过。
