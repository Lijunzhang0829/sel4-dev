# claude -p 传输掉线重试方案（claude_loop 流水线通道）

日期：2026-06-17 · 范围：`lemma-staticize/isa-repl/proposer_host.py`、`react_agent.py`

## 一句话

给 claude_loop 流水线里的 `claude -p` 调用加了**针对传输掉线的有韧性重试**，并修复了第一版 naive retry 引入的 spurious-NO-PATH 回归。**仅覆盖流水线通道**，不覆盖 IDE 交互会话通道（见末节边界）。

---

## 1. 两条独立通道（关键前提）

| 通道 | 谁在调 API | 报错形态 | 本方案能否管 |
|---|---|---|---|
| **流水线** | `proposer_host.py` 的 `claude -p` 子进程 | 子进程 stdout 空 / stderr 传输错误 | ✅ 本方案 |
| **IDE 交互会话** | Claude Code 客户端本体 | `API Error: The socket connection was closed unexpectedly` | ❌ 代码层碰不到 |

`socket connection closed` 这条消息若出现在 IDE，是后者；客户端有内置重试，报到用户眼前=硬断连或客户端重试耗尽。与本方案无关，与用量配额（那会报 `Rate limited`）也无关。

## 2. 问题

`proposer_host.call_claude*` 原本 `subprocess.run(timeout=150)` 一锤子、无重试：传输掉线（socket-closed / rate / 5xx）→ stdout 空 → react 收不到提议 → fallback 瞎选 menu → 该步白费。

## 3. 方案

新增 `_claude_stdout(prompt)` 统一封装两处调用，要点：

1. **只重试快速传输掉线**，正则 `_TRANSIENT` 命中 `socket connection closed / rate limit / 5xx / connection reset / overloaded / service unavailable …`。
2. **绝不重试纯 timeout**——claude -p 每次 fresh 进程、冷启常 spike 120-160s，timeout 是"慢"不是"掉线"，retry 它只会越窗。
3. **budget-aware**：整体 `BUDGET=290s`（含 retry），退避 2s/4s（掉线失败得快，短退避即可）。
4. 放宽冷启容忍：per-attempt `150→180s`。

## 4. 踩坑与修复（重要）

第一版 naive retry（retry 一切 + 固定 150s timeout）实测把 `sep_heap_domD` 从 CLOSED 打成 **NO-PATH**。根因是**多文件时序错配**：

```
proposer 总时长(150 timeout + 2 + 150 retry ≈ 302s)  >  react 等待窗 min(220, …)
→ react 放弃仍 pending 的请求 → fallback conjE/disjE → 全 FAIL → spurious NO-PATH
```

修复=上面的"只 retry 掉线 + budget-aware" + 把 `react_agent.llm_react` 的 resp 等待窗 `220→300s`。

**不变量（务必维持）**：`proposer BUDGET(290) < react 等待窗(300) < TIME_CAP`。单 lemma 的 `TIME_CAP` 要给"冷启首调 ~130s + 多步"留足，用 ≥600。

## 5. 验证

- **单测**（mock 前 2 次吐 `socket connection closed`，第 3 次正常）：重试两次后成功，`_TRANSIENT` 模式全命中。
- **端到端**（`sep_heap_domD`，真 claude -p）：R0001 冷启 134.0s 稳落新窗内、无需 retry、无越窗；6 步闭合，build✓，36→5ms，verdict `INCONCLUSIVE-below-noise-floor`，audit=True。
- 回归（naive retry → NO-PATH）已消除。

## 6. 可调旋钮（环境变量）

`CLAUDE_RETRIES=3` · `CLAUDE_TIMEOUT=180`（per-attempt）· `CLAUDE_BUDGET=290`（总预算）。改任一处必须复核 §4 不变量。

## 7. 边界（再次明确）

本方案**只让 claude_loop 流水线对传输掉线有韧性**。IDE 交互会话的 `socket connection closed` 是独立通道，仓库代码无法修复；缓解只能靠重发 / 排查本地网络 / 避免同账号高并发。本轮已复核：流水线**零孤儿进程**，不构成对 IDE 会话的并发挤占。

相关 memory：`proposer-retry-window-coupling`、`gate-timing-missing-json`、`infoflow-repl-init-wall`。
关联流程文档：`tactic-rewrite-and-claude-p-pipeline-20260616.md`。
