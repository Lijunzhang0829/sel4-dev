# 生成检查阈值与稳定性修复（2026-10-27）

本轮对应用户指令“将运行阈值修改为300，继续修复现有缺口”。基线审计为 [generation-priors-failure-audit](../../../reports/regen/generation-priors-failure-audit-20261027-001/REPORT.md)。基线中的参数表和故障评估是修复前的快照，不覆盖本轮代码。

## 阈值范围

当前生成入口的 **candidate execute + audit 默认上限统一为300秒**：Runner的batch/workspace/parallel入口、File Planner、Workspace Agent、通用serial、and/or serial资源缺省值，以及batch/checker/失败缓存身份的fallback。

本机CRefine启动配方原已显式传300，现在绕过该配方使用普通CLI/API时也一致。显式参数仍优先；已冻结run的120秒policy、历史实验脚本、120/300对照fixture和预算记录不改写。启动新配置不能证明旧运行已迁移。

以下独立限时没有改成300：native command/check 900秒、provider build 1800秒、生成feedback 900秒、规划调用1800秒、消费评审600秒、helper probe/结构采样10秒。batch Python响应watchdog继续为候选上限+10秒，默认变为310秒。

## 已实现的修复

### helper probe使用声明边界审计

`sketch_workflow.render_probe`复用`FACT_SNAPSHOT_ML`与`target_resolution_ml`，在owned context内依次执行事实快照、声明/证明、新导出目标解析和oracle检查，再退出context。避免退出locale后用global短名查询；不接受旧同名父事实、空目标或歧义目标。

probe receipt增加`audit_policy=declaration-export-audit-v1`，旧policy probe不能继续用于新的分解review，需要重新probe。已有GREEN/封存证据不因此被手动撤销。probe仍不可发布GREEN，10秒预算不变。`native_fact_audit_smoke --only probe`新增普通、locale、旧同名fact、bundle、错误namespace、空目标等原生正反例入口。

### 损坏provider事件流拒绝验收

非法JSON与合法JSON但非object的行都计入解析错误。Codex/Claude调用不再把有解析错误的结果标成sealable，Codex外层metadata升级也保留该限制。验收再次解析实际raw文件，不能只靠旧meta/audit中错误的成功标志绕过。退出码0和非空final不能覆盖损坏的事件流。

这里修复的是事件解析完整性；不同provider完整生命周期协议的进一步检查应按各自协议增加，不能把JSON可解析等同于所有语义事件都齐全。

### revision decisions进入可信提交事务

新增共享`revision_decisions.decisions_valid`，检查每个issue恰好一条决策、未知/重复ID、字段集合、枚举和非空说明；所有非法类型返回可操作的ValueError。

规划工具对validate/preflight/submit自动携带工作区`revision-decisions.json`。revision阶段在原生预检/提交前验证；失败返回模型继续修复，没有轮数上限。可信提交保存决策的独立副本；下游读取`revision/revision-decisions.json`的可信输出，而不是提交后仍可改写的工作区文件。

### 规划请求超时重发一次与计时冲正

workspace planner和消费评审都接入`planning_retry.retry_planning_request`：单次timeout使用相同提示重发一次，10秒退避计作provider recovery；第二次连续timeout暂停。审计失败、普通schema错误、操作者中断不会被此机制误当作timeout重发。schema错误仍走自己的无限修复循环。

workspace重发前回滚本请求的工作区草稿、inspected状态和提交状态；保留失败查询记录及递增序号，不覆盖既有调用。成功后清除失败partial response正文，保留时间、usage、原摘要与失败metadata。失败草稿不能污染重发输入。

`ReviewedPlanningBudget`从可信请求开始/结束事件计算实际已计入的规划时间，不接受子进程自报退款秒数。只有明确配对的timeout→success才冲正；已排除的native准备不重复冲正；幂等credit和调整后clock在同一checkpoint中保留。成功请求、普通格式修复、未成功重发的耗时继续计入。预算耗尽不能靠预期退款绕过。未闭合请求禁止冻结。

这不是跨进程阶段恢复：进程被杀或连续两次timeout后的旧阶段仍需要专门的checkpoint/授权恢复协议，当前fresh-only保护保留。

### trusted audit阻塞不再无限Continue

完整审计中的新导出目标缺失、歧义、空目标、残留local hypotheses使用`trusted_audit_target`分类。完整证明检查已GREEN而后置目标审计失败时，seal返回`framework_blocked`；controller保持该阻塞状态，拒绝继续数学修改，session在工具回调处停止，调度器单独记录该worker失败。原GREEN保留，其他可运行theory不因此强制取消。

普通proof RED、合法semantic-impact拒绝、仅模型文字声称blocked不触发这一分类。不能把所有seal失败都当作同一种框架bug；新错误类型需要明确证据后再扩展。

### 最终修订计划再次消费评审

流程变为draft → review → revision → final-review → freeze。最后一次评审读取实际提交的revision计划，使用同一模型/努力级别和600秒调用预算，计入同一有效规划时钟。冻结receipt绑定最终计划与final review摘要，并保留最终issues。

最终评审不会自动再改计划；issues是保留的风险，`quality_improvement_certified=False`。结构合法、消费评审通过和native声明预检均不证明任务必定可证或规划更优。

## 验证与未完成项

离线回归覆盖：locale probe的实际源码渲染边界和禁令；成功退出却包含损坏NDJSON的真实本地子进程；决策校验失败→修复→可信提交；timeout的两次调用、工作区回滚、查询序号不重用；失败规划时间的精确/幂等冲正与clock恢复；trusted block停止Continue并保留GREEN；最终review进入同一预算。

本轮最终测试结果见 [修复验证记录](../../../reports/regen/generation-stability-fixes-20261027-001/REPORT.md)。合成native smoke入口已扩展，但本机CRefine checker heap此前被清理，当前没有实际执行新probe的Isabelle验证，也没有启动新的模型实验。

仍需后续完成：

1. 跨进程规划阶段恢复：精确绑定源输入、实现与模型；保存阶段checkpoint；保留已收费工作和未知尾部，避免重复提交与预算清零。
2. effective模式的非终止性重复错误/无信息进展告警仍未恢复；不能用新的无GREEN硬截止替代用户要求的无限修复。
3. 相同候选/前缀/资源的120与300秒native对照，以及新接口实际使用率/吞吐测量。
4. heap和归档证据的可恢复性验证；新增代码不补回已删除资产。

本轮未更改模型选择、owner-blind输入、contract/specification、原实验预算或历史GREEN；未启动实验，未进行Git提交。


## 2026-10-29 提交前补充：Refine 稳定性探索

`refine-astra-effective6h-20261027-001` 使用冻结实现、gpt-6-astra / low、300秒候选阈值，运行6小时有效预算（含规划）。该运行用于检查运行稳定性，不作为 Human/Ours/No-plan 正式规划效果对照。不可变提交重放确认59/287 contracts、4个辅助引理GREEN，4个theory封存；预算到期暂停，未完成全仓库集成。

规划1次timeout和证明3次无完整反馈均恢复，成功重试后的有效时间冲正已核对。原生probe预检13/13通过；本轮4次封存均成功，未复现此前的locale目标审计故障。这些证据不等于系统不存在bug。监控遗漏嵌套工具RED和请求拒绝仍需修复；长时间无contract GREEN也不能直接判为反馈利用停滞。

本次提交按该稳定性运行冻结的源码版本整理。后续Intervaltree适配、可选墙钟截止及其他独立工作不包含在此版本中。审计来源见 `reports/regen/refine-astra-log-audit-20261029-001/REPORT.md`；正式规划比较必须另建独立运行。


## 2026-11-03 补充：消费审查处置闭环

上文的单次 revision 和“终审只记录风险”描述属于历史流程。当前 `review-obligations-v1` 移除 issues 的八条上限，并检查每个 theory 声明消费、但不在 transitive imports 中的外部 contract 都有正式 issue；逐 theory 评估中的具体缺口不能只留在摘要文字里。问题身份按 kind 与 contract IDs 稳定绑定，跨轮重新编号或分组不会使同一问题消失。

修订除了 revise/local/retain，还必须给出按 theory/contract 绑定的 obligations，包括候选 provider、import/local/retain 路线、具体任务及完成条件。选择 import 必须落实到实际 import 闭包；声称 revise 必须有实际分组、imports、helpers、上下文或相关证明方向变化。旧格式仍可读取，新的 reviewed workflow 在提交前强制此规则。

最终评审显式接受或拒绝上一轮处置。新问题或被拒绝的处置进入下一轮真实 revision；默认最多三轮（`--max-revision-rounds`），达到上限仍有未处置问题时不产生 frozen-plan。schema 输出修复仍与实际计划修订轮次分开计数。所有轮次计入同一有效预算，最终receipt指向实际最后一轮目录。

`review-handoff.json` 在冻结前写入最终计划目录，绑定完整 published plan 摘要；按 theory 保存评估、问题、处置和仍待证明的局部任务。已接受 local/retain 表示责任分配被认可，不表示证明完成；即使问题不再出现在终审 issues 中，已分配局部任务仍交给 File Planner。旧实验升级使用 `build_review_handoff(..., legacy=True)`，保留未决状态，不伪造已解决，也不改已接受计划或证明。
