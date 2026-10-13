#!/bin/bash
# PoC: drive lemma rewriting with claude -p and RECORD the full process.
# Prereq: poc_repl_server.py is already running + READY for the target lemma
#   (it holds the reached REPL; check with: bash isa_tool_host.sh state).
# Run this on the HOST (where `claude` is on PATH via run.sh's nvm setup).
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
LEMMA="${1:-equiv_forD}"
REC="runs/rewrite-${LEMMA}-$(date +%Y%m%d-%H%M%S).jsonl"
mkdir -p runs

# resolve the claude CLI: PATH, else the VS Code extension's bundled native binary.
CLAUDE="$(command -v claude 2>/dev/null)"
[ -z "$CLAUDE" ] && CLAUDE="$(ls -d "$HOME"/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)"
[ -z "$CLAUDE" ] && CLAUDE="$(ls -d "$HOME"/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)"
[ -z "$CLAUDE" ] && { echo "claude CLI not found (PATH or VS Code extension)" >&2; exit 1; }
echo "[run] claude bin: $CLAUDE"

# sanity: server up?
if ! bash isa_tool_host.sh state >/dev/null 2>&1; then
  echo "PoC server not responding. Start it first (see poc_repl_server.py)." >&2; exit 1
fi

PROMPT="你是 Isabelle 证明改写 agent。目标:把 lemma ${LEMMA} 的自动化行改写成到达【完全相同证明状态 B】的更优 tactic,优先 STATIC(不含 auto/blast/fastforce/force/clarsimp/metis 等搜索型方法)。

工具(通过 Bash 调用,工作目录已是 ${DIR};直接用相对名):
  bash isa_tool_host.sh state              # 返回 statement(引理陈述)/A_goal(待证目标)/B_desc(目标态)/named_facts_in_original/definitions(相关定义体)
  bash isa_tool_host.sh try '<step>'       # 在 A 的克隆上试一步(非消耗);返回 reached_B / audit_static / 结果目标
  bash isa_tool_host.sh commit '<step>'    # 同 try,但把结果推进为新的 A(用于逐步构建多步路径)
  bash isa_tool_host.sh record '<step>'    # 记录你最终选定的改写(机器可读,写 poc-result.json)
  bash isa_tool_host.sh stop               # 收尾,关闭服务

【工具契约·重要】<step> 必须是完整证明步骤:以 'apply (<method> ...)' 或 'by (<method> ...)' 给出(裸方法会被自动包成 apply,但请显式写)。多步可放进一个 try:'apply (..) apply (..)' 或 'by (.., ..)'。

流程:① 先 state——其中 definitions 已给出相关定义(通常无需再 grep 源码);② 迭代 try,直到某 step 使 reached_B=true 且尽量 audit_static=true;每步用一句话说明理由;③ 选定后 record '<最终step>';④ stop;⑤ 用一段话总结:原行→最终改写、为何更优。"

echo "[run] claude -p 驱动改写 ${LEMMA};记录 -> ${REC}"
"$CLAUDE" -p "$PROMPT" \
  --output-format stream-json --verbose --dangerously-skip-permissions \
  2>&1 | tee "$REC"
echo
echo "[run] 完整 claude -p 记录已存:${REC}"
echo "[run] 校验确实用了 claude -p(看 system/assistant/tool 事件):"
echo "      grep -c '\"type\"' ${REC}; head -1 ${REC}"
