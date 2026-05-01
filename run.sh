#!/usr/bin/env bash
set -euo pipefail

# Usage: bash run.sh <thy_file_or_dir> [budget] [types] [session]
# types: all (default), spec, proof, haskell, c
# session: Isabelle session name (default: auto-detect from path)
#
# Runs on the host. All Isabelle work and skill-script invocations go through
# `docker compose exec` into the sel4-dev container (see
# .claude/skills/isabelle_prover/scripts/_dx.sh for the routing shared helper).
# The Claude CLI invocation stays on the host — budget/subscription/auth all
# transparent. The agent's Bash tool calls the same wrappers.
#
# Examples:
#   bash run.sh l4v/proof/invariant-abstract/ARM/ArchAcc_AI.thy
#   bash run.sh l4v/proof/invariant-abstract/ 50.00 proof
#   bash run.sh l4v/proof/access-control/ 100.00 proof Access

TARGET="${1:?Usage: bash run.sh <thy_file_or_dir> [budget] [types] [session]}"
BUDGET="${2:-100.00}"
TYPES="${3:-all}"
SESSION="${4:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

# Make sure claude's shebang resolves to node >=18. Default shell PATH may put
# a system /usr/bin/node (v10/v12) ahead of nvm-installed v20+, which breaks
# claude's CLI loader even though `claude` itself is on PATH.
NVM_NODE_DIR="${HOME}/.nvm/versions/node"
if [ -d "$NVM_NODE_DIR" ]; then
  LATEST=$(ls -v "$NVM_NODE_DIR" 2>/dev/null | tail -1)
  [ -n "$LATEST" ] && export PATH="$NVM_NODE_DIR/$LATEST/bin:$PATH"
fi
command -v claude >/dev/null || { echo "claude CLI not found on PATH" >&2; exit 1; }
node_major=$(node -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo 0)
if [ "$node_major" -lt 18 ]; then
  echo "node $node_major on PATH is too old for claude CLI; install node >=18 (e.g. via nvm)" >&2
  exit 1
fi

# Host-visible source (for git sha). Container sees the same tree at
# /workspace/verification/l4v (compose mount maps the repo root to /workspace).
L4V_PATH="${SCRIPT_DIR}/verification/l4v"

# Wrappers agent will call; cwd-relative because we chdir below.
# This repo nests the skill under claude/.claude/ rather than at the repo root,
# so the default points there. Override with $ISA_SCRIPTS if you've symlinked
# .claude/ → claude/.claude at the project root.
if [ -z "${ISA_SCRIPTS:-}" ]; then
  if [ -d "${SCRIPT_DIR}/claude/.claude/skills/isabelle_prover/scripts" ]; then
    export ISA_SCRIPTS="claude/.claude/skills/isabelle_prover/scripts"
  else
    export ISA_SCRIPTS=".claude/skills/isabelle_prover/scripts"
  fi
fi

# In-container paths.
CT_L4V_DIR="/workspace/verification/l4v"

# Wrapper for invoking isabelle inside the container.
# Pipe-safe: returns whatever the container command returns.
dc_isabelle() {
  docker compose -f "$COMPOSE_FILE" exec -T l4v \
    bash -lc "L4V_ARCH=${L4V_ARCH:-ARM} isabelle $*"
}

# Generic docker exec inside the service.
dc_exec() {
  docker compose -f "$COMPOSE_FILE" exec -T l4v bash -lc "$*"
}

cd "${SCRIPT_DIR}"

TARGET_NAME="$(basename "${TARGET}" .thy)"
mkdir -p logs reports
OUT_JSON="logs/run_${TARGET_NAME}_strengthen_$(date +%Y%m%d_%H%M%S).json"

START_TIME=$(date +%s)
START_STR=$(date -d "@${START_TIME}" '+%Y-%m-%d %H:%M:%S')
RUN_ID="${TARGET_NAME}_$(date +%Y%m%d_%H%M%S)"

# Publish current run id for the skill scripts to auto-append to attempts JSONL.
# Cleared on exit so out-of-band check-theory invocations don't pollute a stale log.
echo "${RUN_ID}" > logs/.current-run-id

# Background event-driven monitor watcher (host-side polling, zero docker exec).
# Watches logs/attempts-<RID>.jsonl for size growth and writes batched tick
# reports to logs/monitor-stream-<RID>.log within ~3s of each new attempt.
# Triggers fall in the agent's "thinking pause" between check-theory calls, so
# the watcher never competes with isabelle for cpuset 0-11 during a measurement.
WATCHER_PID=""
if [ -x "${SCRIPT_DIR}/bin/monitor-watcher.sh" ]; then
  nohup bash "${SCRIPT_DIR}/bin/monitor-watcher.sh" "${RUN_ID}" "${SCRIPT_DIR}" \
    >/dev/null 2>&1 &
  WATCHER_PID=$!
  echo "[monitor-watcher] PID ${WATCHER_PID}, stream → logs/monitor-stream-${RUN_ID}.log"
fi

# Trap: remove sentinel (watcher self-exits when sentinel disappears) + belt-and-braces kill.
trap 'rm -f logs/.current-run-id; [ -n "${WATCHER_PID}" ] && kill ${WATCHER_PID} 2>/dev/null || true' EXIT

# Inject session override if provided
SESSION_INSTRUCTION=""
if [ -n "${SESSION}" ]; then
  SESSION_INSTRUCTION="
SESSION OVERRIDE: Use session '${SESSION}' for ALL check-theory.sh, goal-at.sh, and proof-timing.sh calls for this target. Do not auto-detect from path."
fi

# Inject slow-proofs-report if present in cwd
REPORT_CONTEXT=""
if [ -f "slow-proofs-report.md" ]; then
  REPORT_CONTEXT="
SLOW PROOFS REPORT (pre-computed — do NOT re-run proof-timing.sh or scan-slow-proofs.sh; use this data directly to identify candidates):
$(cat slow-proofs-report.md)"
fi

# Inject lessons-learned if a session-specific file exists
LESSONS_CONTEXT=""
LESSONS_FILE="reports/${TARGET_NAME}-lessons-learned.md"
if [ -f "${LESSONS_FILE}" ]; then
  LESSONS_CONTEXT="
LESSONS LEARNED (from prior optimization runs — READ CAREFULLY before attempting any optimization):
$(cat "${LESSONS_FILE}")"
fi

# Detect required heap from SESSION arg or target path
if [ -n "${SESSION}" ]; then
  PREFLIGHT_SESSION="${SESSION}"
elif echo "${TARGET}" | grep -q "proof/access-control"; then
  PREFLIGHT_SESSION="Access"
elif echo "${TARGET}" | grep -q "proof/infoflow"; then
  PREFLIGHT_SESSION="InfoFlow"
elif echo "${TARGET}" | grep -q "proof/refine"; then
  PREFLIGHT_SESSION="Refine"
elif echo "${TARGET}" | grep -q "proof/crefine"; then
  PREFLIGHT_SESSION="CRefine"
else
  PREFLIGHT_SESSION="AInvs"
fi

# Pre-flight: ensure the required session heap exists (via container).
# Heap dir is determined inside the container by Isabelle settings
# (/tmp/isabelle_settings overrides USER_HEAPS to /isabelle/$L4V_ARCH).
if dc_exec "test -f \"\$(isabelle getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux/${PREFLIGHT_SESSION}\""; then
  echo "[pre-flight] ${PREFLIGHT_SESSION} heap present"
else
  echo "[pre-flight] ${PREFLIGHT_SESSION} heap missing, rebuilding..."
  dc_isabelle build -b -d "${CT_L4V_DIR}" "${PREFLIGHT_SESSION}" 2>&1 | tail -5
  if ! dc_exec "test -f \"\$(isabelle getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux/${PREFLIGHT_SESSION}\""; then
    echo "[pre-flight] ERROR: ${PREFLIGHT_SESSION} heap build failed" >&2
    exit 1
  fi
  echo "[pre-flight] ${PREFLIGHT_SESSION} heap rebuilt"
fi

# Session-round initialization — minimal context for the agent.
# We give the agent: run_id (for log filenames), target session, git sha, and
# a session-wide baseline time. Everything about *how* to optimize is skill-owned.
#
# Two flavours of "session build" timing:
#   warm_check  — `isabelle build` with current heap. If nothing needs to
#                 recompile (the typical case mid-development), this just
#                 measures Isabelle startup + dependency-graph scan, ~7-15s.
#                 NOT a proxy for "how long does the session take to verify".
#   clean       — `isabelle build -c` clears the heap first, forcing every
#                 theory to recompile. Measures actual session verification
#                 wall, ~30 min – 2 h depending on session. Opt-in via
#                 RUN_SESSION_CLEAN_REBUILD=1 (off by default).
SESSION_WARM_CHECK_BEFORE_MS=""
SESSION_CLEAN_BEFORE_MS=""
GIT_COMMIT_SHA=""
SESSION_ROUND_CONTEXT=""
if echo "${TYPES}" | grep -qE '(^|,)proof(,|$)|^all$'; then
  GIT_COMMIT_SHA=$(git -C "${L4V_PATH}" rev-parse HEAD 2>/dev/null || echo "unknown")
  echo "[session-round] l4v commit: ${GIT_COMMIT_SHA}"

  echo "[session-round] Measuring session warm-check time for ${PREFLIGHT_SESSION}..."
  SESSION_BUILD_START=$(date +%s%3N)
  dc_isabelle build -d "${CT_L4V_DIR}" "${PREFLIGHT_SESSION}" > /dev/null 2>&1 || true
  SESSION_BUILD_END=$(date +%s%3N)
  SESSION_WARM_CHECK_BEFORE_MS=$(( SESSION_BUILD_END - SESSION_BUILD_START ))
  echo "[session-round] session_warm_check_before_ms = ${SESSION_WARM_CHECK_BEFORE_MS} (incremental no-op build, NOT a verification-cost proxy)"

  if [ "${RUN_SESSION_CLEAN_REBUILD:-0}" = "1" ]; then
    echo "[session-round] RUN_SESSION_CLEAN_REBUILD=1 — clean rebuild for honest baseline (slow!)..."
    CLEAN_START=$(date +%s%3N)
    dc_isabelle build -c -d "${CT_L4V_DIR}" "${PREFLIGHT_SESSION}" > /dev/null 2>&1 || true
    CLEAN_END=$(date +%s%3N)
    SESSION_CLEAN_BEFORE_MS=$(( CLEAN_END - CLEAN_START ))
    echo "[session-round] session_clean_before_ms = ${SESSION_CLEAN_BEFORE_MS} (full session rebuild)"
  fi

  # Per-theory baseline from the session's heap-log DB — zero extra build cost.
  # Agent can use this to rank files by elapsed time before spending on proof-timing.sh.
  BASELINES_MD="reports/session-baselines-${RUN_ID}.md"
  echo "[session-round] Extracting per-theory baselines from heap log..."
  if bash "${ISA_SCRIPTS}/emit-session-baselines.sh" "${PREFLIGHT_SESSION}" "${BASELINES_MD}" >/dev/null 2>&1; then
    echo "[session-round] wrote ${BASELINES_MD}"
    BASELINES_LINE="- per-theory baselines: ${BASELINES_MD}  (cold-start-safe; always present)"
  else
    BASELINES_LINE="- per-theory baselines: (unavailable — session has no heap-log yet)"
  fi

  # Surface prior runs (older attempts/impact) as optional context. before_ms values
  # in old impact records may be stale if those patches already landed.
  PRIOR_RUNS_LINE=""
  for imp in $(ls -t logs/impact-*.jsonl 2>/dev/null | head -5); do
    rid=$(basename "$imp" .jsonl | sed 's/^impact-//')
    n=$(wc -l < "$imp" 2>/dev/null || echo 0)
    [ "$n" = "0" ] && continue
    # skip the current run's own file
    [ "$rid" = "${RUN_ID}" ] && continue
    PRIOR_RUNS_LINE="${PRIOR_RUNS_LINE}
- ${rid}: ${n} applied changes (logs/attempts-${rid}.jsonl, logs/impact-${rid}.jsonl)"
  done
  if [ -n "$PRIOR_RUNS_LINE" ]; then
    PRIOR_RUNS_BLOCK="
PRIOR RUNS (optional — 'before_ms' values may be stale if those patches already landed):${PRIOR_RUNS_LINE}"
  else
    PRIOR_RUNS_BLOCK=""
  fi

  SESSION_ROUND_CONTEXT="
RUN CONTEXT:
- run_id: ${RUN_ID}
- session: ${PREFLIGHT_SESSION}
- target: ${TARGET}
- start_time: ${START_STR}
- git_commit_sha: ${GIT_COMMIT_SHA}
- session_warm_check_before_ms: ${SESSION_WARM_CHECK_BEFORE_MS}ms
  (incremental \`isabelle build\` exit time when heap is current — startup
  overhead only, not a session verification-cost proxy.)
- session_clean_before_ms: ${SESSION_CLEAN_BEFORE_MS:-N/A}
  (full clean rebuild wall, only present when RUN_SESSION_CLEAN_REBUILD=1)
- For session-level optimization signal use \`sum_delta_ms\` from impact records,
  not session_*_ms differences.
${BASELINES_LINE}
- Logs go to: logs/attempts-${RUN_ID}.jsonl, logs/impact-${RUN_ID}.jsonl
  (see the SKILL.md rules section for the two-log schema; nothing else is required).${PRIOR_RUNS_BLOCK}"
fi

PROMPT="/isabelle_prover /isabelle:strengthen ${TARGET} --types ${TYPES}

Follow the isabelle-prover skill — rules are in SKILL.md (three hard rules:
no direct edits, no bypassing the prover, two JSONL logs per run).
Everything else — scan strategy, tactic choice, retry count, when to stop —
is your call.
${SESSION_INSTRUCTION}
${REPORT_CONTEXT}
${LESSONS_CONTEXT}
${SESSION_ROUND_CONTEXT}"

echo "================================================"
echo " strengthen runner (sel4-dev)"
echo "================================================"
echo " Target:  ${TARGET}"
echo " Types:   ${TYPES}"
echo " Session: ${SESSION:-auto}"
echo " Budget:  \$${BUDGET}"
echo " Run ID:  ${RUN_ID}"
echo " Output:  ${OUT_JSON}"
echo " Started: ${START_STR}"
if [ -n "${SESSION_WARM_CHECK_BEFORE_MS}" ]; then
echo " Session warm-check:  ${SESSION_WARM_CHECK_BEFORE_MS}ms (startup, NOT verify cost)"
[ -n "${SESSION_CLEAN_BEFORE_MS}" ] && echo " Session clean build: ${SESSION_CLEAN_BEFORE_MS}ms"
echo " l4v commit:          ${GIT_COMMIT_SHA:0:12}"
fi
echo "================================================"

set +e
claude -p "${PROMPT}" \
  --dangerously-skip-permissions \
  --max-budget-usd "${BUDGET}" \
  --output-format stream-json \
  --include-partial-messages \
  --verbose \
  > "${OUT_JSON}"
EXIT_CODE=$?
set -e

END_TIME=$(date +%s)
END_STR=$(date -d "@${END_TIME}" '+%Y-%m-%d %H:%M:%S')
DURATION=$(( END_TIME - START_TIME ))
MINUTES=$(( DURATION / 60 ))
SECS=$(( DURATION % 60 ))

FILE_SIZE=$(du -h "${OUT_JSON}" | cut -f1)
if [ "$EXIT_CODE" -eq 0 ]; then STATUS="SUCCESS"; else STATUS="FAILED (exit code: $EXIT_CODE)"; fi

RESULT_JSON=$(grep '"type":"result"' "${OUT_JSON}" 2>/dev/null | tail -1 || true)

if [ -n "${RESULT_JSON}" ]; then
  OUTPUT=$(printf '%s' "${RESULT_JSON}" | python3 -c '
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(f"cost: ${d.get(\"total_cost_usd\", 0):.3f}, turns: {d.get(\"num_turns\", 0)}, stop: {d.get(\"stop_reason\", \"N/A\")}")
except Exception:
    print("(result event unparseable)")
' 2>/dev/null || true)
else
  OUTPUT="(no result event)"
fi

echo ""
echo "================================================"
echo " Run Complete"
echo "================================================"
echo " Status:   ${STATUS}"
echo " Duration: ${MINUTES}m ${SECS}s"
echo " Output:   ${FILE_SIZE} → ${OUT_JSON}"
echo " Result:   ${OUTPUT}"
echo "================================================"

# Post-agent processing: roll up the two agent-produced logs into agent-metrics.jsonl,
# plus a downstream regression smoke when something was applied. Also produces:
#   - logs/impact-${RUN_ID}-enriched.jsonl   (impact joined with attempts: LOC, patch_path)
#   - logs/per-target-${RUN_ID}.jsonl        (per-file roll-up incl. token attribution)
# Opt-in (off by default):
#   - RUN_SESSION_CLEAN_REBUILD=1  → full clean rebuild before+after, emits
#     reports/session-baselines-${RUN_ID}-after.md and -diff.md (honest, single-build).
#   - RUN_PROOF_TIMING_AFTER=1     → re-runs proof-timing.sh on touched files
#     (per-proof before/after diagnosis when session signal is unclear).
if [ -n "${RUN_ID:-}" ] && echo "${TYPES}" | grep -qE '(^|,)proof(,|$)|^all$'; then
  AGENT_METRICS_LOG="logs/metrics-${RUN_ID}.jsonl"
  ATTEMPTS_LOG="logs/attempts-${RUN_ID}.jsonl"
  IMPACT_LOG="logs/impact-${RUN_ID}.jsonl"
  ENRICHED_IMPACT_LOG="logs/impact-${RUN_ID}-enriched.jsonl"
  PER_TARGET_LOG="logs/per-target-${RUN_ID}.jsonl"
  BASELINES_AFTER_MD="reports/session-baselines-${RUN_ID}-after.md"
  BASELINES_DIFF_MD="reports/session-baselines-${RUN_ID}-diff.md"
  STATS=$(python3 - <<PYEOF
import json, os

# From attempts:
#   attempt_total / attempt_pass count kind in {patch, apply} only
#   apply_attempts / applied count kind == apply only
#   sledgehammer_calls / sledgehammer_wall_ms break out the new tool separately
#   so it doesn't pollute hit_rate computations (sledgehammer is a query, not a
#   patch attempt, and its wall is much higher than check-theory's).
attempt_total = 0
attempt_pass  = 0
applied       = 0
apply_attempts = 0
sledgehammer_calls = 0
sledgehammer_pass  = 0
sledgehammer_wall_ms = 0

p_att = "${ATTEMPTS_LOG}"
if os.path.exists(p_att):
    with open(p_att) as f:
        for line in f:
            try:
                d = json.loads(line)
                kind = d.get("kind")
                verdict = d.get("verdict")
                if kind in ("patch", "apply"):
                    attempt_total += 1
                    if verdict == "pass":
                        attempt_pass += 1
                if kind == "apply":
                    apply_attempts += 1
                    if verdict == "pass":
                        applied += 1
                if kind == "sledgehammer":
                    sledgehammer_calls += 1
                    if verdict == "pass":
                        sledgehammer_pass += 1
                    sledgehammer_wall_ms += int(d.get("wall_ms") or 0)
            except Exception:
                pass

# From impact: sum of delta_ms and delta_pct over applied changes
sum_ms = 0
sum_pct = 0.0
impact_count = 0
p_imp = "${IMPACT_LOG}"
if os.path.exists(p_imp):
    with open(p_imp) as f:
        for line in f:
            try:
                d = json.loads(line)
                v = d.get("delta_ms")
                if v is not None: sum_ms += int(v)
                v = d.get("delta_pct")
                if v is not None: sum_pct += float(v)
                impact_count += 1
            except Exception:
                pass

print(f"{applied} {attempt_total} {attempt_pass} {apply_attempts} {impact_count} {sum_ms} {sum_pct} {sledgehammer_calls} {sledgehammer_pass} {sledgehammer_wall_ms}")
PYEOF
)
  APPLIED_COUNT=$(echo "${STATS}" | awk '{print $1}')
  ATTEMPT_TOTAL=$(echo "${STATS}" | awk '{print $2}')
  ATTEMPT_PASS=$(echo "${STATS}" | awk '{print $3}')
  APPLY_ATTEMPTS=$(echo "${STATS}" | awk '{print $4}')
  IMPACT_COUNT=$(echo "${STATS}" | awk '{print $5}')
  SUM_DELTA_MS=$(echo "${STATS}" | awk '{print $6}')
  SUM_DELTA_PCT=$(echo "${STATS}" | awk '{print $7}')
  SLEDGEHAMMER_CALLS=$(echo "${STATS}" | awk '{print $8}')
  SLEDGEHAMMER_PASS=$(echo "${STATS}" | awk '{print $9}')
  SLEDGEHAMMER_WALL_MS=$(echo "${STATS}" | awk '{print $10}')

  # ────────────────────────────────────────────────────────────────────
  # Enrich impact + per-target attribution
  #
  # Two outputs:
  #   1) impact-${RUN_ID}-enriched.jsonl — every agent-written impact record
  #      joined with attempts.jsonl (matched by patch_sha) so it carries
  #      lines_added / lines_removed / patch_path / attempts_to_apply.
  #   2) per-target-${RUN_ID}.jsonl — one record per target file: counts of
  #      attempts/applies/passes, file-level delta sum, LOC delta, plus an
  #      estimated token / cost share computed from the stream-json transcript.
  #
  # Token attribution rule: each assistant message is bound to the attempts
  # record with the closest preceding `ts` (any kind, any source). Tokens
  # before the first attempts record are dropped from the per-target rollup
  # but retained in the run-level metrics record below.
  # ────────────────────────────────────────────────────────────────────
  ATTEMPTS_LOG_ENV="${ATTEMPTS_LOG}" \
  IMPACT_LOG_ENV="${IMPACT_LOG}" \
  ENRICHED_OUT_ENV="${ENRICHED_IMPACT_LOG}" \
  PER_TARGET_OUT_ENV="${PER_TARGET_LOG}" \
  STREAM_JSON_ENV="${OUT_JSON}" \
  SESSION_ENV="${PREFLIGHT_SESSION}" \
  python3 - <<'PYEOF'
import os, json
from datetime import datetime

def _load_jsonl(p):
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out

def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(s.replace("Z",""), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None

attempts = _load_jsonl(os.environ["ATTEMPTS_LOG_ENV"])
impacts  = _load_jsonl(os.environ["IMPACT_LOG_ENV"])

# Index attempts by patch_sha → (most recent matching record). The auto kind:apply
# record is the canonical source of patch_path + LOC; fall back to kind:patch.
by_sha_apply = {}
by_sha_patch = {}
for a in attempts:
    sha = a.get("patch_sha")
    if not sha:
        continue
    if a.get("kind") == "apply":
        by_sha_apply[sha] = a
    elif a.get("kind") == "patch":
        by_sha_patch.setdefault(sha, a)

# Count patch+apply attempts per (target, patch_sha) pair to populate
# attempts_to_apply on each impact record.
attempts_per_target = {}
for a in attempts:
    if a.get("kind") in ("patch", "apply"):
        attempts_per_target.setdefault(a.get("target"), []).append(a)

# 1) Enriched impact
enriched_path = os.environ["ENRICHED_OUT_ENV"]
with open(enriched_path, "w") as f:
    for imp in impacts:
        rec = dict(imp)
        sha = imp.get("patch_sha")
        src = by_sha_apply.get(sha) or by_sha_patch.get(sha) or {}
        for k in ("lines_added", "lines_removed", "patch_bytes", "patch_path"):
            if k in src and k not in rec:
                rec[k] = src[k]
        # attempts_to_apply: how many patch+apply attempts on this target
        # happened up to and including the matching apply record's ts.
        scope = imp.get("scope")
        bound_ts = _parse_ts((by_sha_apply.get(sha) or {}).get("ts"))
        n_attempts = 0
        if scope:
            # Match attempts on either basename(scope) or full path that ends with scope
            for a in attempts_per_target.get(scope, []) + [
                a for tgt, lst in attempts_per_target.items()
                if tgt and tgt != scope and (tgt.endswith("/" + scope) or scope.endswith("/" + (tgt or "")))
                for a in lst
            ]:
                ats = _parse_ts(a.get("ts"))
                if bound_ts is None or (ats is not None and ats <= bound_ts):
                    n_attempts += 1
        if n_attempts:
            rec["attempts_to_apply"] = n_attempts
        f.write(json.dumps(rec) + "\n")

# 2) Per-target rollup with token attribution
# Walk the stream-json once, attributing each assistant-message usage to the
# most recent attempts record (by ts). Stream events that pre-date all attempts
# fall into the "_pre_run" bucket and are dropped from the per-target file.
import re as _re_norm
def _norm_target(t):
    if not t: return t
    base = t.split("/")[-1]
    # Strip ":L<line>" suffix used by sledgehammer/goal-at so per-file aggregation
    # collapses "IpcCancel_AI.thy:L368" and "IpcCancel_AI.thy" into one bucket.
    return _re_norm.sub(r":L\d+$", "", base)

# Build a chronologically sorted attempts table keyed by basename(target)
sorted_attempts = []
for a in attempts:
    ats = _parse_ts(a.get("ts"))
    if ats is None:
        continue
    sorted_attempts.append((ats, _norm_target(a.get("target")), a))
sorted_attempts.sort(key=lambda x: x[0])

# Cumulative usage delta per assistant turn: stream-json reports per-message
# usage snapshots. We treat each assistant `message` event's usage as the
# delta for that turn.
attribution = {}  # target_basename → {"input_tokens", "output_tokens", "cache_read", "cache_creation", "turns"}
def _bump(tgt, usage):
    d = attribution.setdefault(tgt, {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        "assistant_turns": 0,
    })
    d["input_tokens"]                += int(usage.get("input_tokens") or 0)
    d["output_tokens"]               += int(usage.get("output_tokens") or 0)
    d["cache_read_input_tokens"]     += int(usage.get("cache_read_input_tokens") or 0)
    d["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
    d["assistant_turns"]             += 1

stream_path = os.environ.get("STREAM_JSON_ENV", "")
# Attribution rule: walk stream-json sequentially; whenever an assistant turn
# contains a Bash tool_use that calls check-theory.sh / proof-timing.sh on a
# .thy file, set that file as the active target. THIS turn's usage and every
# subsequent turn's usage attribute to that target until another such tool_use
# changes it. Tokens spent before the first targeted tool call land in "_recon".
#
# This replaces an earlier ts-based binding which always failed: the claude
# CLI stream-json events do not carry per-event timestamps, so ts comparison
# gave None and every turn collapsed into "_pre_run", which was then dropped.
import re as _re
# Match the script name (optionally followed by a closing shell quote from a
# wrapper like `bash "$ISA_SCRIPTS/check-theory.sh"`) then the .thy argument.
# Non-greedy + word boundary so a leading `"` quote isn't captured.
# Includes sledgehammer.sh so its tokens attribute to the file it queries.
_THY_TARGET_RE = _re.compile(r'(?:check-theory|proof-timing|sledgehammer)\.sh"?\s+"?(\S+?\.thy)\b')

def _scan_target_from_assistant(ev):
    """Return basename(.thy) if THIS assistant turn contains a Bash tool_use
    that names a .thy file; else None. Multiple matches → take the LAST."""
    msg = ev.get("message") or {}
    chosen = None
    for c in (msg.get("content") or []):
        if not isinstance(c, dict):
            continue
        if c.get("type") == "tool_use" and c.get("name") == "Bash":
            cmd = (c.get("input") or {}).get("command") or ""
            m = _THY_TARGET_RE.search(cmd)
            if m:
                chosen = m.group(1).split("/")[-1]
    return chosen

if stream_path and os.path.exists(stream_path):
    last_target = "_recon"
    with open(stream_path) as sf:
        for line in sf:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") != "assistant":
                continue
            new_tgt = _scan_target_from_assistant(ev)
            if new_tgt:
                last_target = new_tgt
            usage = (ev.get("message") or {}).get("usage") or ev.get("usage") or {}
            if not usage:
                continue
            _bump(last_target, usage)

# Approximate per-target USD using sonnet-4-6 prices (matches fallback in metrics)
PRICE = {"in": 3.0, "out": 15.0, "cache_read": 0.30, "cache_create": 3.75}
def _est_usd(d):
    return (d["input_tokens"]*PRICE["in"]
          + d["output_tokens"]*PRICE["out"]
          + d["cache_read_input_tokens"]*PRICE["cache_read"]
          + d["cache_creation_input_tokens"]*PRICE["cache_create"]) / 1_000_000

# Aggregate per-target counts from attempts + impacts
per_target = {}
for a in attempts:
    tgt = _norm_target(a.get("target"))
    if not tgt:
        continue
    rec = per_target.setdefault(tgt, {
        "target": tgt, "session": os.environ["SESSION_ENV"],
        "baseline_runs": 0, "patch_attempts": 0, "patch_passes": 0,
        "apply_attempts": 0, "applied": 0,
        "lines_added": 0, "lines_removed": 0,
        "sledgehammer_calls": 0, "sledgehammer_pass": 0, "sledgehammer_wall_ms": 0,
    })
    kind = a.get("kind"); verdict = a.get("verdict")
    if kind == "baseline":
        rec["baseline_runs"] += 1
    elif kind == "patch":
        rec["patch_attempts"] += 1
        if verdict == "pass":
            rec["patch_passes"] += 1
    elif kind == "apply":
        rec["apply_attempts"] += 1
        if verdict == "pass":
            rec["applied"] += 1
            rec["lines_added"]   += int(a.get("lines_added") or 0)
            rec["lines_removed"] += int(a.get("lines_removed") or 0)
    elif kind == "sledgehammer":
        rec["sledgehammer_calls"] += 1
        if verdict == "pass":
            rec["sledgehammer_pass"] += 1
        rec["sledgehammer_wall_ms"] += int(a.get("wall_ms") or 0)

for imp in impacts:
    tgt = _norm_target(imp.get("scope"))
    if not tgt:
        continue
    rec = per_target.setdefault(tgt, {
        "target": tgt, "session": os.environ["SESSION_ENV"],
        "baseline_runs": 0, "patch_attempts": 0, "patch_passes": 0,
        "apply_attempts": 0, "applied": 0,
        "lines_added": 0, "lines_removed": 0,
        "sledgehammer_calls": 0, "sledgehammer_pass": 0, "sledgehammer_wall_ms": 0,
    })
    rec["file_before_ms_min"] = min(rec.get("file_before_ms_min", 10**12), int(imp.get("before_ms") or 10**12))
    rec["file_after_ms_max"]  = max(rec.get("file_after_ms_max", 0), int(imp.get("after_ms") or 0))
    rec["sum_delta_ms"]   = rec.get("sum_delta_ms", 0)   + int(imp.get("delta_ms") or 0)
    rec["sum_delta_pct"]  = rec.get("sum_delta_pct", 0.0) + float(imp.get("delta_pct") or 0.0)

# Merge attribution + write. Include attribution-only buckets ("_recon" plus
# any .thy ever named in tool_use but never seen in attempts) so no tokens
# silently disappear from the rollup.
all_keys = sorted(set(per_target.keys()) | set(attribution.keys()))
with open(os.environ["PER_TARGET_OUT_ENV"], "w") as f:
    for tgt in all_keys:
        rec = per_target.get(tgt) or {
            "target": tgt, "session": os.environ["SESSION_ENV"],
            "baseline_runs": 0, "patch_attempts": 0, "patch_passes": 0,
            "apply_attempts": 0, "applied": 0,
            "lines_added": 0, "lines_removed": 0,
            "sledgehammer_calls": 0, "sledgehammer_pass": 0, "sledgehammer_wall_ms": 0,
        }
        attr = attribution.get(tgt) or {}
        rec["input_tokens"]                = attr.get("input_tokens", 0)
        rec["output_tokens"]               = attr.get("output_tokens", 0)
        rec["cache_read_input_tokens"]     = attr.get("cache_read_input_tokens", 0)
        rec["cache_creation_input_tokens"] = attr.get("cache_creation_input_tokens", 0)
        rec["assistant_turns"]             = attr.get("assistant_turns", 0)
        rec["est_usd_attributed"]          = round(_est_usd(rec) if attr else 0.0, 6)
        rec["hit_rate"] = (rec["applied"] / rec["apply_attempts"]) if rec["apply_attempts"] > 0 else None
        f.write(json.dumps(rec) + "\n")

# Surface counts so the shell can summarise
print(len(per_target), len(impacts))
PYEOF

  DOWNSTREAM_JSON="{}"
  DOWNSTREAM_TIMEOUT="${DOWNSTREAM_TIMEOUT:-1800}"
  if [ "${APPLIED_COUNT}" -gt 0 ]; then
    case "${PREFLIGHT_SESSION}" in
      AInvs)       DOWNSTREAM="Access BaseRefine" ;;
      Access)      DOWNSTREAM="InfoFlow" ;;
      BaseRefine)  DOWNSTREAM="Refine" ;;
      CBaseRefine) DOWNSTREAM="CRefine" ;;
      *)           DOWNSTREAM="" ;;
    esac
    DS_RESULTS=""
    for ds in $DOWNSTREAM; do
      if dc_exec "test -f \"\$(isabelle getenv -b ISABELLE_HEAPS)/polyml-5.9.1_x86_64_32-linux/${ds}\""; then
        echo "[post-run] Downstream smoke: building ${ds} (timeout ${DOWNSTREAM_TIMEOUT}s)..."
        set +e
        timeout "${DOWNSTREAM_TIMEOUT}" \
          docker compose -f "$COMPOSE_FILE" exec -T l4v \
          bash -lc "L4V_ARCH=ARM isabelle build -d ${CT_L4V_DIR} ${ds}" > /dev/null 2>&1
        DS_RC=$?
        set -e
        case $DS_RC in
          0)   DS_STATE="pass" ;;
          124) DS_STATE="timeout" ;;
          *)   DS_STATE="fail" ;;
        esac
      else
        DS_STATE="skipped"
      fi
      DS_RESULTS="${DS_RESULTS}  \"${ds}\": \"${DS_STATE}\","
      echo "[post-run] Downstream ${ds}: ${DS_STATE}"
    done
    DS_RESULTS="${DS_RESULTS%,}"
    DOWNSTREAM_JSON="{${DS_RESULTS}}"
  fi

  # ────────────────────────────────────────────────────────────────────
  # Session-after measurement
  #
  # The default `isabelle build` is incremental — when the heap is current
  # it returns in 7-15s having done nothing. That's startup overhead, not a
  # measurement of session verification cost. Comparing such a number
  # before/after the agent's applies produces noise (we observed +21.74%
  # session_delta_pct on a 2.4s difference between two ~12s warm-check exits,
  # which was meaningless). Worse, the `theory_timings` heap-log DB that
  # emit-session-baselines.sh reads from then mixes timings written by
  # different builds, making session-baselines-after.md and -diff.md show
  # spurious +400% regressions on theories that weren't actually rebuilt.
  #
  # Honest signal sources:
  #   1. sum_delta_ms from impact records — file-level wall changes the
  #      agent already verified. This is THE session-level signal.
  #   2. session_clean_after_ms (opt-in RUN_SESSION_CLEAN_REBUILD=1) —
  #      forced full rebuild, real verification wall. Comparable to
  #      session_clean_before_ms also captured under the same flag.
  #
  # We unconditionally re-do the cheap warm-check (for completeness in the
  # metrics record) but no longer compute a misleading delta from it. The
  # baselines-after.md / -diff.md are emitted ONLY when the clean rebuild
  # ran, so the per-theory table is coherent (all timings from one build).
  # ────────────────────────────────────────────────────────────────────
  SESSION_WARM_CHECK_AFTER_MS=""
  SESSION_CLEAN_AFTER_MS=""
  SESSION_CLEAN_DELTA_MS=""
  SESSION_CLEAN_DELTA_PCT=""
  BASELINES_BEFORE_MD="reports/session-baselines-${RUN_ID}.md"
  EMIT_AFTER_BASELINES=0
  if [ "${APPLIED_COUNT}" -gt 0 ]; then
    echo "[post-run] Re-measuring session warm-check time..."
    T1=$(date +%s%3N)
    dc_isabelle build -d "${CT_L4V_DIR}" "${PREFLIGHT_SESSION}" > /dev/null 2>&1 || true
    T2=$(date +%s%3N)
    SESSION_WARM_CHECK_AFTER_MS=$(( T2 - T1 ))
    echo "[post-run] session_warm_check_after_ms = ${SESSION_WARM_CHECK_AFTER_MS} (informational; do not interpret as session verify cost)"

    if [ "${RUN_SESSION_CLEAN_REBUILD:-0}" = "1" ] && [ -n "${SESSION_CLEAN_BEFORE_MS:-}" ]; then
      echo "[post-run] RUN_SESSION_CLEAN_REBUILD=1 — clean rebuild for honest session_after measurement..."
      T1=$(date +%s%3N)
      dc_isabelle build -c -d "${CT_L4V_DIR}" "${PREFLIGHT_SESSION}" > /dev/null 2>&1 || true
      T2=$(date +%s%3N)
      SESSION_CLEAN_AFTER_MS=$(( T2 - T1 ))
      SESSION_CLEAN_DELTA_MS=$(( SESSION_CLEAN_AFTER_MS - SESSION_CLEAN_BEFORE_MS ))
      SESSION_CLEAN_DELTA_PCT=$(python3 -c "print(round(${SESSION_CLEAN_DELTA_MS}*100/${SESSION_CLEAN_BEFORE_MS}, 2))")
      echo "[post-run] session_clean_after_ms = ${SESSION_CLEAN_AFTER_MS} (Δ ${SESSION_CLEAN_DELTA_MS}ms / ${SESSION_CLEAN_DELTA_PCT}%)"
      EMIT_AFTER_BASELINES=1
    else
      echo "[post-run] (skipping per-theory after/diff: requires RUN_SESSION_CLEAN_REBUILD=1 for coherent timings)"
    fi
  fi

  if [ "${EMIT_AFTER_BASELINES}" = "1" ]; then
    echo "[post-run] Emitting per-theory baseline (post-clean-rebuild) + computing diff..."
    if bash "${ISA_SCRIPTS}/emit-session-baselines.sh" "${PREFLIGHT_SESSION}" "${BASELINES_AFTER_MD}" >/dev/null 2>&1; then
      BEFORE_MD_ENV="${BASELINES_BEFORE_MD}" \
      AFTER_MD_ENV="${BASELINES_AFTER_MD}" \
      DIFF_MD_ENV="${BASELINES_DIFF_MD}" \
      SESSION_NAME_ENV="${PREFLIGHT_SESSION}" \
      python3 - <<'PYEOF'
import os, re
def _parse(md):
    out = {}
    if not os.path.exists(md):
        return out
    for line in open(md):
        m = re.match(r"\|\s*([^\|]+?)\s*\|\s*(\d+)\s*\|\s*\d+\s*\|\s*\d+\s*\|", line)
        if m:
            out[m.group(1).strip()] = int(m.group(2))
    return out
before = _parse(os.environ["BEFORE_MD_ENV"])
after  = _parse(os.environ["AFTER_MD_ENV"])
keys = sorted(set(before) | set(after))
rows = []
for k in keys:
    b = before.get(k); a = after.get(k)
    if b is None or a is None:
        continue
    d = a - b
    pct = (d * 100.0 / b) if b > 0 else 0.0
    rows.append((k, b, a, d, pct))
rows.sort(key=lambda r: abs(r[3]), reverse=True)
with open(os.environ["DIFF_MD_ENV"], "w") as f:
    f.write(f"# Per-theory before/after — {os.environ['SESSION_NAME_ENV']}\n\n")
    f.write("Sorted by |delta_ms| desc. Negative delta = faster after the run.\n\n")
    f.write("| Theory | before_ms | after_ms | delta_ms | delta_pct |\n|---|---:|---:|---:|---:|\n")
    for k, b, a, d, p in rows:
        f.write(f"| {k} | {b} | {a} | {d} | {p:+.2f}% |\n")
print(f"  wrote {os.environ['DIFF_MD_ENV']} ({len(rows)} theories)")
PYEOF
    else
      echo "[post-run] (skipped diff: emit-session-baselines after failed)"
    fi
  fi

  # ────────────────────────────────────────────────────────────────────
  # Optional: proof-level after timing on touched files
  #
  # Off by default. Turn on with RUN_PROOF_TIMING_AFTER=1 when single-proof
  # wins don't materialise at the session level — this localises the issue
  # to specific proofs whose cost moved (or didn't) post-apply.
  # ────────────────────────────────────────────────────────────────────
  if [ "${RUN_PROOF_TIMING_AFTER:-0}" = "1" ] && [ "${APPLIED_COUNT}" -gt 0 ]; then
    TOUCHED_FILES=$(python3 -c "
import json
seen = set()
for line in open('${IMPACT_LOG}'):
    try:
        d = json.loads(line)
        s = d.get('scope') or ''
        if s.endswith('.thy'):
            seen.add(s)
    except Exception:
        pass
print(' '.join(sorted(seen)))
" 2>/dev/null || true)
    for tf in $TOUCHED_FILES; do
      base=$(basename "$tf" .thy)
      out_md="reports/proof-timing-${RUN_ID}-${base}-after.md"
      # Resolve scope (basename) to a real file under l4v
      full=$(find verification/l4v -type f -name "$(basename "$tf")" 2>/dev/null | head -1)
      [ -z "$full" ] && full="$tf"
      echo "[post-run] proof-timing AFTER on ${full} → ${out_md}"
      bash "${ISA_SCRIPTS}/proof-timing.sh" "$full" "${PREFLIGHT_SESSION}" > "$out_md" 2>&1 || true
    done
  fi

  DURATION_MS=$(( DURATION * 1000 ))
  RUN_ID_ENV="${RUN_ID}" \
  SESSION_ENV="${PREFLIGHT_SESSION}" \
  DURATION_MS_ENV="${DURATION_MS}" \
  EXIT_CODE_ENV="${EXIT_CODE}" \
  APPLIED_COUNT_ENV="${APPLIED_COUNT}" \
  ATTEMPT_TOTAL_ENV="${ATTEMPT_TOTAL}" \
  ATTEMPT_PASS_ENV="${ATTEMPT_PASS}" \
  APPLY_ATTEMPTS_ENV="${APPLY_ATTEMPTS}" \
  IMPACT_COUNT_ENV="${IMPACT_COUNT}" \
  SUM_DELTA_MS_ENV="${SUM_DELTA_MS}" \
  SUM_DELTA_PCT_ENV="${SUM_DELTA_PCT}" \
  SESSION_WARM_CHECK_BEFORE_MS_ENV="${SESSION_WARM_CHECK_BEFORE_MS:-}" \
  SESSION_WARM_CHECK_AFTER_MS_ENV="${SESSION_WARM_CHECK_AFTER_MS:-}" \
  SESSION_CLEAN_BEFORE_MS_ENV="${SESSION_CLEAN_BEFORE_MS:-}" \
  SESSION_CLEAN_AFTER_MS_ENV="${SESSION_CLEAN_AFTER_MS:-}" \
  SESSION_CLEAN_DELTA_MS_ENV="${SESSION_CLEAN_DELTA_MS:-}" \
  SESSION_CLEAN_DELTA_PCT_ENV="${SESSION_CLEAN_DELTA_PCT:-}" \
  EMIT_AFTER_BASELINES_ENV="${EMIT_AFTER_BASELINES:-0}" \
  SLEDGEHAMMER_CALLS_ENV="${SLEDGEHAMMER_CALLS:-0}" \
  SLEDGEHAMMER_PASS_ENV="${SLEDGEHAMMER_PASS:-0}" \
  SLEDGEHAMMER_WALL_MS_ENV="${SLEDGEHAMMER_WALL_MS:-0}" \
  ENRICHED_IMPACT_LOG_ENV="${ENRICHED_IMPACT_LOG}" \
  PER_TARGET_LOG_ENV="${PER_TARGET_LOG}" \
  BASELINES_DIFF_MD_ENV="${BASELINES_DIFF_MD}" \
  BASELINES_AFTER_MD_ENV="${BASELINES_AFTER_MD}" \
  GIT_COMMIT_SHA_ENV="${GIT_COMMIT_SHA:-}" \
  PROOF_TIMING_AFTER_ENV="${RUN_PROOF_TIMING_AFTER:-0}" \
  DOWNSTREAM_JSON_ENV="${DOWNSTREAM_JSON}" \
  RESULT_JSON_ENV="${RESULT_JSON}" \
  OUT_JSON_ENV="${OUT_JSON}" \
  AGENT_METRICS_LOG_ENV="${AGENT_METRICS_LOG}" \
  python3 - <<'PYEOF'
import json, os

run_id          = os.environ["RUN_ID_ENV"]
session         = os.environ["SESSION_ENV"]
duration_ms     = int(os.environ["DURATION_MS_ENV"])
exit_code       = int(os.environ["EXIT_CODE_ENV"])
applied         = int(os.environ["APPLIED_COUNT_ENV"])
attempt_total   = int(os.environ["ATTEMPT_TOTAL_ENV"])
attempt_pass    = int(os.environ["ATTEMPT_PASS_ENV"])
apply_attempts  = int(os.environ["APPLY_ATTEMPTS_ENV"])
impact_count    = int(os.environ["IMPACT_COUNT_ENV"])
sum_ms          = int(os.environ["SUM_DELTA_MS_ENV"])
sum_pct         = float(os.environ["SUM_DELTA_PCT_ENV"])
downstream      = json.loads(os.environ["DOWNSTREAM_JSON_ENV"])
result_raw      = os.environ["RESULT_JSON_ENV"].strip()
out_path        = os.environ["AGENT_METRICS_LOG_ENV"]

def _opt_int(name):
    v = os.environ.get(name, "").strip()
    return int(v) if v else None

def _opt_float(name):
    v = os.environ.get(name, "").strip()
    return float(v) if v else None

rec = {
    "run_id": run_id, "session": session, "duration_ms": duration_ms,
    "exit_code": exit_code,
    "git_commit_sha": os.environ.get("GIT_COMMIT_SHA_ENV") or None,
    "attempts_total": attempt_total,        # patch + apply invocations
    "attempts_passed": attempt_pass,
    "apply_attempts": apply_attempts,
    "applied_count": applied,
    "impact_records": impact_count,
    "sum_delta_ms": sum_ms,
    "sum_delta_pct": sum_pct,
    "hit_rate": (applied / attempt_total) if attempt_total > 0 else None,
    "apply_success_rate": (applied / apply_attempts) if apply_attempts > 0 else None,
    "downstream_regression_check": downstream,
    # Two flavours of session-build timing — see run.sh for full rationale.
    # warm_check  = `isabelle build` exit time when nothing to do (~7-15s);
    #               useful as noise floor / smoke; NOT a verify-cost proxy.
    # clean_*     = full rebuild wall, only present under RUN_SESSION_CLEAN_REBUILD=1.
    # For session-level optimization signal, prefer sum_delta_ms (above) or
    # session_clean_delta_ms (below, opt-in).
    "session_warm_check_before_ms": _opt_int("SESSION_WARM_CHECK_BEFORE_MS_ENV"),
    "session_warm_check_after_ms":  _opt_int("SESSION_WARM_CHECK_AFTER_MS_ENV"),
    "session_clean_before_ms":      _opt_int("SESSION_CLEAN_BEFORE_MS_ENV"),
    "session_clean_after_ms":       _opt_int("SESSION_CLEAN_AFTER_MS_ENV"),
    "session_clean_delta_ms":       _opt_int("SESSION_CLEAN_DELTA_MS_ENV"),
    "session_clean_delta_pct":      _opt_float("SESSION_CLEAN_DELTA_PCT_ENV"),
    "session_clean_rebuild_enabled": os.environ.get("EMIT_AFTER_BASELINES_ENV") == "1",
    "sledgehammer_calls":   int(os.environ.get("SLEDGEHAMMER_CALLS_ENV") or 0),
    "sledgehammer_pass":    int(os.environ.get("SLEDGEHAMMER_PASS_ENV") or 0),
    "sledgehammer_wall_ms": int(os.environ.get("SLEDGEHAMMER_WALL_MS_ENV") or 0),
    "enriched_impact_path": os.environ.get("ENRICHED_IMPACT_LOG_ENV") or None,
    "per_target_path":      os.environ.get("PER_TARGET_LOG_ENV") or None,
    # baselines after/diff are honest only when clean rebuild ran; surfaced
    # as paths only in that case.
    "baselines_after_path": (os.environ.get("BASELINES_AFTER_MD_ENV") or None)
                            if os.environ.get("EMIT_AFTER_BASELINES_ENV") == "1" else None,
    "baselines_diff_path":  (os.environ.get("BASELINES_DIFF_MD_ENV") or None)
                            if os.environ.get("EMIT_AFTER_BASELINES_ENV") == "1" else None,
    "proof_timing_after_enabled": os.environ.get("PROOF_TIMING_AFTER_ENV") == "1",
}

def _from_stream_aggregation(stream_path):
    """Sum usage across ALL assistant message events. The result event's `usage`
    field reports only the LAST message's usage, not cumulative — so for a
    multi-turn run it under-reports tokens by orders of magnitude. Stream
    aggregation is the only source of truth for turn count + tokens."""
    turns = 0
    in_tok = out_tok = cache_cr = cache_rd = 0
    try:
        with open(stream_path) as sf:
            for line in sf:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "assistant":
                    turns += 1
                usage = (ev.get("message") or {}).get("usage") or ev.get("usage") or {}
                if not usage:
                    continue
                in_tok   += int(usage.get("input_tokens") or 0)
                out_tok  += int(usage.get("output_tokens") or 0)
                cache_cr += int(usage.get("cache_creation_input_tokens") or 0)
                cache_rd += int(usage.get("cache_read_input_tokens") or 0)
    except FileNotFoundError:
        pass
    # Price estimate (claude-sonnet-4-6 as of 2026-04):
    # $3 / MTok input, $15 / MTok output, $0.30 / MTok cache-read, $3.75 / MTok cache-create
    est_cost = (in_tok * 3 + out_tok * 15 + cache_rd * 0.30 + cache_cr * 3.75) / 1_000_000
    return {
        "num_turns": turns or None,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_creation_input_tokens": cache_cr,
        "cache_read_input_tokens": cache_rd,
        "estimated_cost_usd_from_tokens": est_cost if est_cost > 0 else None,
    }

# Tokens + turn count: ALWAYS from stream aggregation (result event under-reports).
parsed = _from_stream_aggregation(os.environ.get("OUT_JSON_ENV", ""))
parsed["metrics_source"] = "stream_aggregation"

# Cost + stop_reason: from result event when present (authoritative for $).
# Fall back to the token-derived estimate if no result event landed (budget kill).
if result_raw:
    try:
        r = json.loads(result_raw)
        cost = r.get("total_cost_usd")
        if cost is not None:
            parsed["total_cost_usd"] = cost
            parsed["total_cost_usd_source"] = "result_event"
        parsed["stop_reason"] = r.get("stop_reason")
    except Exception as e:
        rec["result_event_parse_error"] = str(e)

if parsed.get("total_cost_usd") is None and parsed.get("estimated_cost_usd_from_tokens") is not None:
    parsed["total_cost_usd"] = parsed["estimated_cost_usd_from_tokens"]
    parsed["total_cost_usd_source"] = "estimate_from_token_prices"

rec.update(parsed)

cost     = rec.get("total_cost_usd")
in_tok   = rec.get("input_tokens") or 0
out_tok  = rec.get("output_tokens") or 0
cache_rd = rec.get("cache_read_input_tokens") or 0
cache_cr = rec.get("cache_creation_input_tokens") or 0

rec["usd_per_applied"] = (cost / applied) if (applied > 0 and cost) else None
rec["usd_per_pct_speedup"] = (cost / sum_pct) if (sum_pct > 0 and cost) else None
rec["net_speedup_ms_per_dollar"] = (sum_ms / cost) if (cost and cost > 0) else None
total_tok = in_tok + out_tok
rec["tokens_per_applied"] = (total_tok / applied) if applied > 0 else None
cache_denom = in_tok + cache_cr + cache_rd
rec["cache_hit_rate"] = (cache_rd / cache_denom) if cache_denom > 0 else None

with open(out_path, "a") as f:
    f.write(json.dumps(rec) + "\n")

cost_str = f"${rec.get('total_cost_usd', 'n/a')}"
hit_str  = f"{rec['hit_rate']:.2f}" if rec.get('hit_rate') is not None else "n/a"
print(f"  cost: {cost_str}, turns: {rec.get('num_turns', 'n/a')}, applied: {applied}, hit_rate: {hit_str}")
PYEOF

  echo ""
  echo "[post-run] Agent metrics: ${AGENT_METRICS_LOG}"
fi
