# Codex 会话停滞监测

`codex_session_watch` 是独立的只读监测程序，用于在主聊天无法继续输出时观察会话状态。它不能修复前端或网络，也不能证明模型没有在服务端计算。

在本仓库工作目录、另一个终端中运行：

```bash
python3 -m regen.infrastructure.codex_session_watch \
  --thread-id 01a0b123-a3c4-7270-ab12-7b44be82c9a7 \
  --watch --interval 30 --threshold-seconds 300 \
  --status-file /tmp/refine-codex-watch.json
```

这是前台监测进程；Ctrl+C只停止监测，不终止agent或实验。若要让输出不依赖前端管道，可追加 `> /tmp/refine-codex-watch.log 2>&1`，另行查看状态文件。未替用户部署常驻后台进程。

省略 `--watch` 只检查一次。也可用 `--session-file /absolute/path/to/rollout.jsonl` 指定日志。默认从 `~/.codex/sessions` 按完整线程ID定位唯一文件；自定义位置使用 `--sessions-root`。历史复验可添加 `--as-of 2026-09-22T12:00:00Z`，不可与watch同时使用。

- `active`：最近有该主agent的可观察事件。
- `no_observable_main_progress`：主agent超过阈值没有新事件；不是网络错误的确定诊断。
- `task_complete` / `turn_aborted`：本轮已完成或中断，不继续报作卡住。
- `pending_tools`：尚未返回的工具调用，工具本身可能仍正常工作。
- `subagents`：子任务活动单独呈现，不刷新主agent的进展计时。

程序首次扫描日志，之后仅读取新增完整行；文件mtime、token统计和子任务通知不会伪装成主agent进展。只输出事件类型、时间、ID和状态，不输出思考正文、消息、工具参数或结果。超过阈值只记录状态，不重启、不kill、不重发模型请求，避免重复计费或重叠controller。

发现长时间无进展时，先确认是否存在pending工具以及真实runner/checker进程。没有工作进展时可中断外层会话，再从已保存文件和验证journal继续；不要同时启动两个证明controller。仓库实验的 `runner-status.json` / `runner-events.ndjson` 负责另一层监测，心跳只表示monitor活着，不等于证明正在推进。

本次40分钟事件的本地回放在12:00识别为主agent无可观察进展，在12:13识别为turn_aborted。更精确的网络/服务端归因仍需客户端或服务端日志；不能由停滞标记直接推断。详见 `reports/analysis/codex-stall-20260922/REPORT.md`。


## 2026-09-23: 阶段日志与外层诊断分离

实验不再输出周期性 `*_heartbeat`，`runner-status.json` 只表示最后记录的阶段，
不代表进程实时存活。`file_planner_phase` 包含 request_number、phase、model_attempts、
action 和状态；Isabelle 保留开始/结束事件，增量 worker 增加等待对象的开始/结束。
旧调用者的 heartbeat_seconds 参数保留兼容，但不启动周期日志线程。

外层监控支持 `--client-log /path/to/Codex.log --client-utc-offset=-04:00`
以及 `--events-file /path/to/diagnostics.ndjson`。内部轮询仍为30秒，但只在观测事件
或诊断状态变化时写记录，不输出周期心跳。状态文件 observed_at 是最后发布时刻，
不是监控进程存活证明。客户端日志只读取尾部2MiB，按精确 turn ID 提取白名单错误
类别，不复制原始日志正文。时区偏移必须按日志所在地设置。

`last_tool_result` 记录最后返回的工具ID、名称与时间；
`tool_returned_no_later_event` 表示工具结果已落盘但无后续主会话事件。
这不是“请求已发送但服务端未回复”的确定结论。request_sent、
remote_response_received、frontend_rendered 在缺少对应证据时保持 unknown。
该监控不能修改已安装 Codex 的网络层；完整发送/接收/渲染追踪仍需客户端提供。
不自动重发模型请求或重启 runner。
