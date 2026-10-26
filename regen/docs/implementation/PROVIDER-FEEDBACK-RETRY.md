# 模型无完整反馈的一次重发与有效时间核算

2026-10-25 的实现适用于新发布的 `parallel-workspace` 证明生成。默认执行策略
`provider_feedback_retries=1`：可信响应超时、连接 EOF、明确的短暂 provider/网络故障
允许一次新会话重发；再次失败则持久化暂停。认证、未知协议错误、数学检查失败和
重复读取造成的停滞不借此获得额外重试。原 HTTP 恢复与旧停滞策略仍保留为兼容路径。

这里的请求是等待一次完整工具请求或正常回合结束的区间，不是整个持续数小时的
File Planner 会话。边界由 harness 记录：发送 `turn/start` 时开始；收到完整有效
工具请求时结束；执行工具并发送结果后，开始下一个等待区间。工具回调中的网络失败
只建立检测时刻的归属记录，不把已经执行的工具操作回溯为失败等待。

响应超时以最后一次与当前 thread/turn 匹配的真实模型反馈为准；持续到达的 reasoning
或 agentMessage 片段会刷新静默期限，所以 xhigh 长思考不因超过单次静默阈值而中断。
用量、额度等元数据不能延长静默期限。片段可以表明连接活跃，但仍须完整工具请求或
正常回合结束才能确认重发成功。关闭的 stdin 管道和 stdout EOF 都属于可重发传输失败；
磁盘持久化等其他 I/O 错误不冒充网络故障。

## 重发与成功的含义

旧会话及其检查任务必须完成取消、清理和 join，才建立新的 provider thread。
新 thread 从当前持久工作区、已接受 commits、草稿和检查反馈继续；不会自动重放
此前已经执行的工具调用。清理失败会暂停，不能同时保留两个可写 owner。

重发成功指收到与该会话归属一致、通过协议身份及工具 allowlist 检查的完整工具请求，
或正常的 `turn/completed`。普通文本片段、心跳、token 通知和新建 thread 本身均不算成功。
这个成功边界证明传输已恢复，不要求 Isabelle GREEN；后续工具参数或候选证明仍可能
被拒绝，仍按原规则反馈和检查。未知工具、错误归属或不完整消息不确认成功。

每个连续故障只预约一次替代请求。`provider_feedback_retry_scheduled` 与
`provider_feedback_retry_started` 记录预约和启动；匹配原失败 request ID 的成功收据
重置该故障的重发次数。恢复 coordinator 不能清零次数：已启动但缺失完成证据的
替代请求保持不确定并暂停。只有预约尚未启动时，恢复后才可执行这次已预约请求。

## 成功后扣除哪段时间

失败请求先照常计费，只有其重发获得完整有效响应后，才从有效实验预算扣除
**该失败请求已计入的等待区间**。旧成功响应、已执行工具、已接受证明、规划时间和
重发本身的生成时间均保留。重发也失败时不扣除；尚未确认的未来扣除不能越过当前
48 小时总预算。已因原生准备等原因排除的时间不会重复扣除。

并行预算计算计费区间的并集。A 的失败等待与正常工作的 B 重叠时，只扣 A 的
worker/theory 时间；总有效时间仍包含 B。两个失败请求均在各自重发成功后，才可
扣除它们共同占用、没有其他正常工作贡献的那段总有效时间。

`ProviderRequestAccounting` 保存精确计费段及参与者。退款收据和调整后的计数在同一个
`time_budget_checkpoint` 中原子写入，边界为 `provider_request_retry_credit`；重复同一
失败/成功配对不会重复扣除。调度器直接识别这个检查点，因此即使崩溃发生在写入
后续 `provider_feedback_retry_succeeded` 事件之前，也不会错误地再花一次重发额度。
原始 transcript、失败消息、token 用量和原始计费计数全部保留，不能把有效时间扣除
解释为 provider token 或费用退款。

## 范围与旧入口差异

| 入口 | 当前规则 |
| --- | --- |
| `parallel-workspace` 新发布 | 默认一次失败反馈重发；完整响应成功后按 request 区间扣除失败等待；保留原始证据 |
| 批处理 Repository Planner | `repository_planning_retry.call_with_retry` 对同一请求超时重发一次；成功后保存 timeout-summary 并清除失败 partial 响应正文；未接入上述并行 request 核算 |
| 仓库规划 workspace、消费评审、规划修订 | 仍遵循各自已有调用、输出修复和 fresh-only 边界；本次没有为它们统一接入新重发/退款策略 |
| 通用 Codex / VERO 应用 | 保留各自 provider 恢复策略与计费口径；不能仅因共享部分底层组件就宣称已与本规则完全一致 |

新策略写入运行绑定；旧冻结代码、配置和账本不会自动改变。旧暂停实验若需使用新实现，
必须由显式 continuation 保留来源、规划、全部历史成本及证据；没有可信 request 边界的
历史时间不能凭最后一次 token 或肉眼估计自动扣除。

实现入口：[会话](../../infrastructure/codex_workspace_session.py)、
[并行调度](../../planning/parallel_file_planner.py)、
[预算事件桥](../../infrastructure/parallel_generation_budget.py)、
[请求计时](../../infrastructure/provider_request_accounting.py)、
[有效时间预算](../../infrastructure/effective_time_budget.py)。
离线回归覆盖见 `test_provider_feedback_session.py`、`test_parallel_feedback_retry.py`
和 `test_provider_request_accounting.py`；这些测试不调用真实模型或 Isabelle，不代表
新的长期生成实验已完成。
