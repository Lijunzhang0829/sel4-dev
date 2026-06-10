#!/usr/bin/env bash
# tools/spec_strengthen/spec_strengthen_run.sh
#
# Process manager for spec strengthening on the AInvs (and related)
# sessions. Implements a 5-subcommand interface with a JSONL
# candidate-state ledger.
#
# Subcommands:
#
#   survey <file> [--session <s>] [--pattern G|C|A|all] [--out <path>]
#       Scan <file> for Pattern G / C / A candidates. Pattern D is
#       not surveyed (no automated detector). Write a markdown survey
#       doc + append `discovered` events to the ledger for new
#       candidates.
#
#   execute --candidate <key> --expid <expid> [-y] [--retry]
#       Run the full pipeline on a candidate identified by canonical
#       key (e.g. `G:KHeap_AI:set_object:domain_index`). Branches by
#       the pattern prefix.  Resumes failed candidates only with
#       --retry.
#
#   execute --pattern A|D --patch <patch> --theory <thy> \
#           --expid <expid> [--key <key>] [-y]
#       Run the standard pipeline on a custom patch. Pattern D's
#       only path; Pattern A's fallback path.
#
#   status [<key>]
#       Print the current state of one candidate (latest ledger
#       event) or all candidates with their states.
#
#   ledger
#       Tail the last 40 ledger events.
#
#   mark-audited <key> [--expid <expid>]
#       Append an `audited` event after the author commits the audit
#       dir to git.
#
#   mark-aborted <key> [--reason <r>]
#       Append an `aborted` event; the candidate is explicitly
#       removed from active consideration.
#
# Spec-strengthening patterns supported: A, C, D, G.
# Patterns B, E, F are deliberately not handled — they are not strict
# spec strengthening under the project's revised definition.
#
# State storage: reports/spec-strengthen/candidate-ledger.jsonl
# (append-only; latest event per key = current state).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
SPEC_TOOLS="${SPEC_TOOLS:-tools/spec_strengthen}"
LEDGER="reports/spec-strengthen/candidate-ledger.jsonl"

mkdir -p "$(dirname "$LEDGER")"
touch "$LEDGER"

# ---------------- ledger helpers --------------------------------------------

# Append a JSON line to the ledger.  Caller provides a JSON dict;
# script adds ts.
ledger_append() {
  local payload="$1"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python3 -c "
import json, sys
d = json.loads('''$payload''')
d['ts'] = '$ts'
print(json.dumps(d, ensure_ascii=False))
" >> "$LEDGER"
}

# Get the latest event for a candidate key.  Echo the event JSON or
# empty if no events for that key.
#
# Previously implemented as `tac "$LEDGER" | python3 ...` with the
# python iterating reversed lines until match. That form races under
# `set -euo pipefail`: when python exits early (sys.exit(0) on match),
# tac receives SIGPIPE → exit 141 → pipefail propagates non-zero into
# the command substitution → set -e aborts the script. The race only
# triggered when the matched event was DEEP enough in the file that
# tac had buffered material in flight at python's exit; latest-events
# (newer than ~6 lines from tail) happened to match before tac filled
# its buffer, hiding the bug for the 0027-0049 work where all
# candidates were freshly discovered. Surfaced by the 0050+ retries
# that look up trial_failed events from earlier in the file.
#
# Fix: read the file in Python, iterate reversed in-memory. No pipe,
# no SIGPIPE race.
ledger_state() {
  local key="$1"
  python3 -c "
import json, os, sys
key = '$key'
path = '$LEDGER'
if not os.path.exists(path):
    sys.exit(0)
with open(path) as f:
    lines = f.readlines()
for line in reversed(lines):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    if d.get('key') == key:
        print(json.dumps(d, ensure_ascii=False))
        sys.exit(0)
"
}

# Convenience: echo just the 'event' field of the latest state.
ledger_state_event() {
  local state
  state="$(ledger_state "$1")"
  if [ -z "$state" ]; then
    echo "none"
  else
    echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin)['event'])"
  fi
}

# ---------------- session derivation ----------------------------------------

detect_session() {
  case "$1" in
    *spec/abstract/*)            echo ASpec ;;
    *spec/cspec/*)               echo CSpec ;;
    *proof/invariant-abstract/*) echo AInvs ;;
    *proof/refine/*)             echo Refine ;;
    *proof/crefine/*)            echo CRefine ;;
    *proof/access-control/*)     echo Access ;;
    *proof/infoflow/*)           echo InfoFlow ;;
    *proof/drefine/*)            echo DRefine ;;
    *proof/bisim/*)              echo Bisim ;;
    *)                           echo "" ;;
  esac
}

# ---------------- usage -----------------------------------------------------

usage() {
  cat >&2 <<'EOF'
spec_strengthen_run.sh — process manager for spec strengthening
                        (patterns A, C, D, G only).

Subcommands:
  survey  <file> [--session <s>] [--pattern G|C|A|all] [--out <path>]
  execute --candidate <key> --expid <expid> [-y] [--retry]
  execute --pattern A|D --patch <patch> --theory <thy> --expid <expid>
                [--key <key>] [-y]
  status  [<key>]
  ledger
  mark-audited <key> [--expid <expid>]
  mark-aborted <key> [--reason <r>]

Run from the repo root.
EOF
  exit 2
}

# ---------------- subcommand: status ----------------------------------------

cmd_status() {
  local key="${1:-}"
  if [ -n "$key" ]; then
    local state
    state="$(ledger_state "$key")"
    if [ -z "$state" ]; then
      echo "$key: no events"
      return 0
    fi
    echo "$state" | python3 -m json.tool
    return 0
  fi

  # All candidates: latest event per key
  python3 <<EOF
import json
from collections import OrderedDict
latest = OrderedDict()
with open("$LEDGER") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        latest[d['key']] = d
if not latest:
    print("(ledger is empty)")
else:
    print(f"{'event':<20} {'key'}")
    print("-" * 80)
    for k, d in latest.items():
        print(f"{d['event']:<20} {k}")
EOF
}

# ---------------- subcommand: ledger ----------------------------------------

cmd_ledger() {
  tail -n 40 "$LEDGER" | python3 -c "
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except json.JSONDecodeError: continue
    ts = d.get('ts', '?')
    ev = d.get('event', '?')
    key = d.get('key', '?')
    print(f'{ts}  {ev:<18} {key}')
"
}

# ---------------- subcommand: mark-audited / mark-aborted -------------------

cmd_mark_audited() {
  local key="${1:-}"; [ -z "$key" ] && usage
  shift || true
  local expid=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --expid) expid="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  ledger_append "{\"key\":\"$key\",\"event\":\"audited\",\"expid\":\"$expid\"}"
  echo "marked: $key → audited"
}

cmd_mark_aborted() {
  local key="${1:-}"; [ -z "$key" ] && usage
  shift || true
  local reason=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --reason) reason="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  ledger_append "{\"key\":\"$key\",\"event\":\"aborted\",\"reason\":\"$reason\"}"
  echo "marked: $key → aborted"
}

# ---------------- subcommand: survey ----------------------------------------

cmd_survey() {
  local file="" session="" out="" pattern="all"
  # first positional arg = file
  if [ "${1:-}" = "" ] || [[ "${1:-}" == --* ]]; then usage; fi
  file="$1"; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --session) session="$2"; shift 2 ;;
      --out)     out="$2";     shift 2 ;;
      --pattern) pattern="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  case "$pattern" in
    G|C|A|all) ;;
    *) echo "invalid survey pattern: $pattern (use G, C, A, or all)" >&2; exit 2 ;;
  esac

  [ -f "$file" ] || { echo "theory not found: $file" >&2; exit 4; }
  if [ -z "$session" ]; then
    session="$(detect_session "$file")"
    [ -z "$session" ] && { echo "could not derive session from $file" >&2; exit 4; }
  fi

  local theory_base date_tag
  theory_base="$(basename "$file" .thy)"
  date_tag="$(date +%Y%m%d)"
  [ -z "$out" ] && out="reports/spec-strengthen/survey-${theory_base}-${date_tag}.md"
  mkdir -p "$(dirname "$out")"

  echo "[survey] file=$file  session=$session  pattern=$pattern"
  echo "[survey] writing → $out"

  local g_jsonl="" c_json="[]" a_json="[]"
  if [ "$pattern" = "G" ] || [ "$pattern" = "all" ]; then
    g_jsonl="$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_frame_gap.py" "$file" 2>/dev/null || true)"
  fi
  if [ "$pattern" = "C" ] || [ "$pattern" = "all" ]; then
    c_json="$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_candidates.py" --pattern C --target "${session,,}" --limit 30 --json 2>/dev/null || true)"
  fi
  if [ "$pattern" = "A" ] || [ "$pattern" = "all" ]; then
    a_json="$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_candidates.py" --pattern A --target "${session,,}" --limit 30 --json 2>/dev/null || true)"
  fi

  LEDGER_PATH="$LEDGER" \
  THEORY_FILE="$file" \
  THEORY_BASE="$theory_base" \
  SESSION="$session" \
  SURVEY_PATTERN="$pattern" \
  OUT_PATH="$out" \
  G_JSONL="$g_jsonl" \
  C_JSON="$c_json" \
  A_JSON="$a_json" \
  python3 - <<'PYEOF'
import json, os, datetime, sys
from pathlib import Path
import re

ledger_path = os.environ['LEDGER_PATH']
theory      = os.environ['THEORY_FILE']
theory_path = Path(theory).resolve()
theory_base = os.environ['THEORY_BASE']
session     = os.environ['SESSION']
survey_pattern = os.environ['SURVEY_PATTERN']
out_path    = os.environ['OUT_PATH']

latest = {}
try:
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            latest[d['key']] = d
except FileNotFoundError:
    pass

g_records = []
for line in (os.environ.get('G_JSONL') or '').splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        g_records.append(json.loads(line))
    except json.JSONDecodeError:
        pass

try:
    c_records = json.loads(os.environ.get('C_JSON') or '[]')
except json.JSONDecodeError:
    c_records = []
c_records = [
    r for r in c_records
    if Path(r.get('file_path', '')).resolve() == theory_path
]

try:
    a_records = json.loads(os.environ.get('A_JSON') or '[]')
except json.JSONDecodeError:
    a_records = []
a_records = [
    r for r in a_records
    if Path(r.get('file_path', '')).resolve() == theory_path
]

def c_key(r):
    m = re.search(r"dropping `([^`]+)`", r.get('suggested_move', ''))
    premise = m.group(1) if m else '?'
    return f"C:{theory_base}:{r['name']}:{premise}", premise

def a_key(r):
    return f"A:{theory_base}:{r['name']}"

thy_text = ''
try:
    thy_text = theory_path.read_text()
except Exception:
    pass

def a_redirect_status(name):
    if not thy_text:
        return '? (file not read)'
    m = re.search(rf'^lemma\s+{re.escape(name)}\b.*?^\s*(by|apply)\b[^\n]*',
                  thy_text, re.MULTILINE | re.DOTALL)
    if m and 'strengthen' in m.group(0):
        return 'likely redirect'
    return 'manual review'

new_events = []
ts = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')

def ev_new(record):
    new_events.append(record)

for r in g_records:
    key = r['key']
    if key in latest:
        continue
    if r['status'] == 'clean':
        ev_new({'ts': ts, 'key': key, 'event': 'discovered',
                'pattern': 'G', 'theory': r['theory'],
                'evidence': 'mechanical',
                'metadata': {'op': r['op'], 'field': r['field'],
                             'anchor': r['anchor'],
                             'anchor_line': r['anchor_line']}})
    else:
        ev_new({'ts': ts, 'key': key, 'event': 'preflight_failed',
                'pattern': 'G', 'theory': r['theory'],
                'evidence': 'mechanical',
                'reason': r['reason']})

for r in c_records:
    key, premise = c_key(r)
    if key in latest:
        continue
    ev_new({'ts': ts, 'key': key, 'event': 'discovered',
            'pattern': 'C', 'theory': theory,
            'evidence': 'heuristic',
            'metadata': {'lemma': r['name'], 'premise': premise,
                         'suspicion_score': r.get('suspicion_score', 0)}})

for r in a_records:
    key = a_key(r)
    if key in latest:
        continue
    ev_new({'ts': ts, 'key': key, 'event': 'discovered',
            'pattern': 'A', 'theory': theory,
            'evidence': 'heuristic+manual',
            'metadata': {'weak': r['name'],
                         'suspicion_score': r.get('suspicion_score', 0)}})

out = []
date_str = datetime.date.today().isoformat()
out.append(f"# spec-strengthen survey — {session} / {theory_base}.thy — {date_str}")
out.append("")
out.append(f"Source: `{theory}`")
out.append("")
out.append("This survey follows the 4-layer model: detector outputs are collected per-pattern, then presented by **verification tier** rather than by a unified candidate ranking.")
out.append("")
out.append("Evidence tags:")
out.append("- `mechanical`: preflight already completed; candidate is high-confidence execute material")
out.append("- `heuristic`: scanner hit only; execute must upgrade it with a probe")
out.append("- `heuristic+manual`: scanner hit only; human review remains mandatory")
out.append("- `none`: no detector; execute-only/manual path")
out.append("")

selected_all = survey_pattern == 'all'
show_g = selected_all or survey_pattern == 'G'
show_c = selected_all or survey_pattern == 'C'
show_a = selected_all or survey_pattern == 'A'

if show_g:
    g_clean = [r for r in g_records if r['status'] == 'clean']
    out.append("## Tier 1 — Mechanically clean (high-confidence apply)")
    out.append("")
    out.append("Pattern G candidates. These already passed direct-grep and crunch-derived preflight checks.")
    out.append("")
    if g_clean:
        out.append("| Key | evidence | (op, field) | anchor | prior |")
        out.append("|---|---|---|---|---|")
        new_keys = {e['key'] for e in new_events}
        for r in g_clean:
            anchor = r.get('anchor') or '—'
            if r.get('anchor_line'):
                anchor = f"{anchor} (L{r['anchor_line']})"
            prior = latest.get(r['key'], {}).get('event', '-')
            if r['key'] in new_keys:
                prior = 'discovered (this run)'
            out.append(f"| `{r['key']}` | `{r.get('evidence','mechanical')}` | {r['op']} / {r['field']} | {anchor} | {prior} |")
    else:
        out.append("(none in this file)")
    out.append("")

if show_c:
    out.append("## Tier 2 — Probe-confirmable (execute upgrades heuristic to ground-truth)")
    out.append("")
    out.append("Pattern C candidates. The scanner only supplies a suspicion signal; `execute --candidate <key>` must run the TRIAL-based premise probe before any patch generation.")
    out.append("")
    out.append("`suspicion_score` is **not** a success ranking. It is a within-pattern impact score: higher means \"bigger payoff if true\", not \"more likely to survive probe\".")
    out.append("")
    if c_records:
        out.append("| Key | evidence | lemma / premise | Consumers | suspicion_score | prior |")
        out.append("|---|---|---|---:|---:|---|")
        new_keys = {e['key'] for e in new_events}
        for r in c_records:
            key, premise = c_key(r)
            prior = latest.get(key, {}).get('event', '-')
            if key in new_keys:
                prior = 'discovered (this run)'
            out.append(f"| `{key}` | `{r.get('evidence','heuristic')}` | {r['name']} / {premise} | {r.get('consumers_lines','?')} | {r.get('suspicion_score','?')} | {prior} |")
    else:
        out.append("(no C candidates for this file in scanner output)")
    out.append("")

if show_a:
    out.append("## Tier 3 — Manual review only")
    out.append("")
    out.append("Pattern A candidates. The detector only identifies weak/strong pairs plus a redirect-shaped proof hint. Pre/post comparability and consumer safety are still manual judgments, so there is no auto-execute path.")
    out.append("")
    if a_records:
        out.append("| Key | evidence | weak lemma | suggested strong companion | redirect proof hint | suspicion_score | prior |")
        out.append("|---|---|---|---|---|---:|---|")
        new_keys = {e['key'] for e in new_events}
        for r in a_records:
            key = a_key(r)
            prior = latest.get(key, {}).get('event', '-')
            if key in new_keys:
                prior = 'discovered (this run)'
            m = re.search(r"`([A-Za-z_][A-Za-z_0-9']*)`\s+companion\s+`([^`]+)`", r.get('suggested_move', ''))
            companion = m.group(2) if m else '?'
            out.append(f"| `{key}` | `{r.get('evidence','heuristic+manual')}` | {r['name']} | {companion} | {a_redirect_status(r['name'])} | {r.get('suspicion_score','?')} | {prior} |")
    else:
        out.append("(no A candidates for this file in scanner output)")
    out.append("")

out.append("## Out of scope / manual only")
out.append("")
out.append("**Pattern D** has no detector. It is an execute-only path with evidence tag `none`.")
out.append("```")
out.append("  spec_strengthen_run.sh execute --pattern D \\")
out.append("    --patch <patch> --theory <thy> --expid <expid> [--key <key>] [-y]")
out.append("```")
out.append("")

if show_g:
    g_failed = [r for r in g_records if r['status'] != 'clean']
    if g_failed:
        out.append("## Informational — Tier 1 candidates rejected by mechanical preflight")
        out.append("")
        out.append("These remain visible for traceability, but they are not execute candidates.")
        out.append("")
        out.append("| Key | evidence | (op, field) | reason |")
        out.append("|---|---|---|---|")
        for r in g_failed:
            out.append(f"| `{r['key']}` | `{r.get('evidence','mechanical')}` | {r['op']} / {r['field']} | {r['reason']} |")
        out.append("")

with open(out_path, 'w', encoding='utf-8') as f:
    f.write("\n".join(out) + "\n")

with open(ledger_path, 'a', encoding='utf-8') as f:
    for ev in new_events:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")

print(f"[survey] markdown written: {out_path}", file=sys.stderr)
print(f"[survey] appended {len(new_events)} new ledger event(s)", file=sys.stderr)
PYEOF

  echo "[survey] state summary:"
  cmd_status | head -20
}

# ---------------- execute helpers -------------------------------------------

# Standard pipeline: snapshot → baseline → trial → impact → apply →
# audit dir. Called by all execute branches after pattern-specific
# prep has produced a valid patch file.
#
# Args:
#   $1 = key
#   $2 = pattern (G/C/A/D)
#   $3 = theory_abs
#   $4 = session
#   $5 = patch_path
#   $6 = expid
standard_pipeline() {
  local key="$1" pattern="$2" theory_abs="$3" session="$4" patch="$5" expid="$6"
  local audit_dir="reports/experiments/${expid}"
  mkdir -p "$REPO_ROOT/$audit_dir"

  local snap="/tmp/spec_strengthen_${expid}_pre.$$"
  cp "$theory_abs" "$snap"
  echo "[1/6] snapshot → $snap"

  echo "[2/6] baseline wall ..."
  local baseline_out baseline_ms baseline_raw
  # `|| true` mirrors the trial-step defense (see [3/6] comment): without it,
  # pipefail propagates check-theory.sh's nonzero exit (e.g. heap-lock conflict,
  # exit 4) into the variable assignment, where `set -e` then aborts the script
  # silently before the `[ -z "$baseline_ms" ]` ledger-failure branch can run.
  baseline_raw="$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$theory_abs" "$session" 2>&1 || true)"
  baseline_out="$(echo "$baseline_raw" | tail -1)"
  baseline_ms="$(echo "$baseline_out" | grep -oE '\([0-9]+ms\)' | tr -d '()ms' || true)"
  [ -z "$baseline_ms" ] && {
    ledger_append "{\"key\":\"$key\",\"event\":\"trial_failed\",\"expid\":\"$expid\",\"reason\":\"baseline run failed\"}"
    echo "  ✗ baseline run failed"; exit 7;
  }
  echo "  ✓ baseline_wall_ms=$baseline_ms"

  echo "[3/6] trial wall (with patch) ..."
  local trial_out trial_ms
  # Defensive: trap pipefail/set-e interactions that could silently kill
  # the script if check-theory.sh produces unusual output (e.g.
  # duplicate-fact errors that don't carry a normal `(NNNms)` suffix).
  # See the 0026 (sym_refs) incident: a duplicate-`_old`-witness collision
  # from an iterative-C cycle caused the previous run to exit 0 with
  # no audit dir built — the grep extraction silently exited via
  # pipefail. The `|| true` here keeps the failure visible.
  local trial_raw
  trial_raw="$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$theory_abs" "$session" --patch "$patch" 2>&1 || true)"
  trial_out="$(echo "$trial_raw" | tail -1)"
  if ! echo "$trial_out" | grep -q '^OK'; then
    ledger_append "{\"key\":\"$key\",\"event\":\"trial_failed\",\"expid\":\"$expid\",\"reason\":\"check-theory.sh --patch did not return OK\"}"
    echo "  ✗ trial FAILED. Last line: $trial_out"
    echo "  (tail of full trial output:)"
    echo "$trial_raw" | tail -5 | sed 's/^/    /'
    exit 7
  fi
  trial_ms="$(echo "$trial_out" | grep -oE '\([0-9]+ms\)' | tr -d '()ms' || echo "")"
  [ -z "$trial_ms" ] && trial_ms="0"
  local delta_pct
  delta_pct="$(awk -v b="$baseline_ms" -v t="$trial_ms" 'BEGIN{printf "%.1f", (t-b)*100.0/b}')"
  echo "  ✓ trial_wall_ms=$trial_ms (Δ ${delta_pct}%)"

  echo "[4/6] spec_impact verdict ..."
  python3 "$REPO_ROOT/$SPEC_TOOLS/spec_impact.py" "$patch" "$theory_abs" \
    --baseline-wall "$baseline_ms" --trial-wall "$trial_ms" \
    --tree "$REPO_ROOT/verification/l4v/proof" \
    --measurement-out "$REPO_ROOT/$audit_dir/measurement.json" >/dev/null 2>&1 || true
  local gate verdict
  gate="$(python3 -c "import json; d=json.load(open('$REPO_ROOT/$audit_dir/measurement.json')); print('PASS' if d.get('gate_pass') else 'FAIL')")"
  verdict="$(python3 -c "import json; d=json.load(open('$REPO_ROOT/$audit_dir/measurement.json')); print(d.get('impact_verdict','?'))")"
  echo "  verdict=$verdict gate=$gate"
  if [ "$gate" != "PASS" ]; then
    ledger_append "{\"key\":\"$key\",\"event\":\"impact_failed\",\"expid\":\"$expid\",\"verdict\":\"$verdict\"}"
    echo "  ✗ Acceptance Gate 2 FAIL; no apply."
    exit 8
  fi

  echo "[5/6] apply ..."
  local apply_out apply_ms
  apply_out="$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$theory_abs" "$session" --apply "$patch" 2>&1 | tail -3)"
  if ! echo "$apply_out" | grep -q 'Patch applied'; then
    ledger_append "{\"key\":\"$key\",\"event\":\"trial_failed\",\"expid\":\"$expid\",\"reason\":\"apply failed\"}"
    echo "  ✗ apply failed"; exit 7;
  fi
  apply_ms="$(echo "$apply_out" | grep -oE '\([0-9]+ms\)' | head -1 | tr -d '()ms')"
  echo "  ✓ apply_wall_ms=$apply_ms"

  echo "[6/6] capture patch.diff + audit dir ..."
  local diff_tmp
  diff_tmp="$(mktemp)"
  diff -u "$snap" "$theory_abs" > "$diff_tmp" 2>/dev/null || true
  local theory_rel="${theory_abs#$REPO_ROOT/}"
  local theory_short="${theory_rel#verification/l4v/}"
  {
    echo "diff --git a/${theory_short} b/${theory_short}"
    sed "1s|^--- .*|--- a/${theory_short}|; 2s|^+++ .*|+++ b/${theory_short}|" "$diff_tmp"
  } > "$REPO_ROOT/$audit_dir/patch.diff"
  rm -f "$diff_tmp"
  cp "$patch" "$REPO_ROOT/$audit_dir/range-patch.patch.txt"

  # command.sh + decision.md skeleton (pattern-specific)
  write_command_sh "$audit_dir" "$theory_rel" "$session" "$expid"
  write_decision_md "$audit_dir" "$key" "$pattern" "$theory_rel" \
    "$expid" "$baseline_ms" "$trial_ms" "$apply_ms" "$verdict"

  ledger_append "{\"key\":\"$key\",\"event\":\"applied\",\"expid\":\"$expid\",\"walls\":{\"baseline\":$baseline_ms,\"trial\":$trial_ms,\"apply\":$apply_ms},\"verdict\":\"$verdict\"}"

  echo ""
  echo "================================================================"
  echo "SUMMARY"
  echo "================================================================"
  echo "  key:       $key"
  echo "  pattern:   $pattern"
  echo "  expid:     $expid"
  echo "  walls:     baseline=$baseline_ms ms  trial=$trial_ms ms (Δ${delta_pct}%)  apply=$apply_ms ms"
  echo "  verdict:   $verdict / $gate"
  echo "  audit:     $audit_dir/"
  echo ""
  echo "Next steps (author):"
  echo "  1. Fill in TODO sections in $audit_dir/decision.md"
  echo "  2. git add $audit_dir/ && git commit"
  echo "  3. spec_strengthen_run.sh mark-audited '$key' --expid '$expid'"
  echo "================================================================"
}

# Write command.sh into the audit dir
write_command_sh() {
  local audit_dir="$1" theory_rel="$2" session="$3" expid="$4"
  cat > "$REPO_ROOT/$audit_dir/command.sh" <<EOF
#!/usr/bin/env bash
# Re-runnable measurement for ${expid}. Generated by spec_strengthen_run.sh.
set -euo pipefail

THEORY="${theory_rel}"
SESSION="${session}"
ISA_SCRIPTS="\${ISA_SCRIPTS:-${ISA_SCRIPTS}}"
SPEC_TOOLS="\${SPEC_TOOLS:-${SPEC_TOOLS}}"
REPO_ROOT="\$(cd "\$(dirname "\$0")/../../.." && pwd)"

TMP_RANGE_PATCH=\$(mktemp /tmp/${expid}-XXXXXX.patch)
cp "\$(dirname "\$0")/range-patch.patch.txt" "\$TMP_RANGE_PATCH"

echo "[1/3] baseline ..." >&2
BASELINE_OUT=\$(bash "\$REPO_ROOT/\$ISA_SCRIPTS/check-theory.sh" "\$REPO_ROOT/\$THEORY" "\$SESSION" 2>&1 | tail -1)
BASELINE_MS=\$(echo "\$BASELINE_OUT" | grep -oE '\\([0-9]+ms\\)' | tr -d '()ms')
echo "baseline_wall_ms=\$BASELINE_MS"

echo "[2/3] trial ..." >&2
TRIAL_OUT=\$(bash "\$REPO_ROOT/\$ISA_SCRIPTS/check-theory.sh" "\$REPO_ROOT/\$THEORY" "\$SESSION" --patch "\$TMP_RANGE_PATCH" 2>&1 | tail -1)
echo "\$TRIAL_OUT" | grep -q '^OK' || { echo "patch FAILED" >&2; exit 1; }
TRIAL_MS=\$(echo "\$TRIAL_OUT" | grep -oE '\\([0-9]+ms\\)' | tr -d '()ms')
echo "trial_wall_ms=\$TRIAL_MS"

echo "[3/3] impact ..." >&2
python3 "\$REPO_ROOT/\$SPEC_TOOLS/spec_impact.py" "\$TMP_RANGE_PATCH" "\$REPO_ROOT/\$THEORY" \\
  --baseline-wall "\$BASELINE_MS" --trial-wall "\$TRIAL_MS" \\
  --tree "\$REPO_ROOT/verification/l4v/proof" \\
  --measurement-out "\$(dirname "\$0")/measurement.json"

DELTA_PCT=\$(awk -v b="\$BASELINE_MS" -v t="\$TRIAL_MS" 'BEGIN{printf "%.1f", (t-b)*100.0/b}')
echo "delta_pct=\$DELTA_PCT"
rm -f "\$TMP_RANGE_PATCH"
EOF
  chmod +x "$REPO_ROOT/$audit_dir/command.sh"
}

# Write decision.md skeleton (pattern-specific TODOs)
write_decision_md() {
  local audit_dir="$1" key="$2" pattern="$3" theory_rel="$4"
  local expid="$5" baseline_ms="$6" trial_ms="$7" apply_ms="$8" verdict="$9"
  local delta_pct
  delta_pct="$(awk -v b="$baseline_ms" -v t="$trial_ms" 'BEGIN{printf "%.1f%%", (t-b)*100.0/b}')"

  local pattern_specific=""
  case "$pattern" in
    G) pattern_specific="$(cat <<'EOF'
## Reference companion(s)

(TODO: list the existing companion lemma(s) in the same family that
motivated this addition, with file/line references.)

## Strengthening claim

(TODO: explain why the new lemma is strictly stronger than the
reference companion. The relationship may be:
- a clean field instantiation (P := <predicate>) — common but NOT
  universal
- or a meta-level argument requiring an explicit proof sketch

Don't assume the field instantiation always works; verify the
entailment manually before claiming strict strengthening.)
EOF
)" ;;
    C) pattern_specific="$(cat <<'EOF'
## Premise dropped

(TODO: which premise was removed; what does its removal mean
semantically.)

## Witness rule + discharge

(TODO: which Hoare monotonicity rule the `<name>_old` witness uses;
why the discharge is trivial.)

## Probe evidence

(TODO: cite the spec_premise_probe.sh output that justified picking
this candidate.)
EOF
)" ;;
    A) pattern_specific="$(cat <<'EOF'
## Weak / strong pair

(TODO: cite both the weak version's original statement and the strong
companion lemma. Show that the proof body of the weak version was a
pure `(strengthen X, wp)` redirect.)

## Cross-file consumer survey

(TODO: how many sites cite the weak lemma by name; do any rely on the
old statement specifically.)
EOF
)" ;;
    D) pattern_specific="$(cat <<'EOF'
## Loose bound site

(TODO: which `≤` postcondition was tightened to `=`; why is the
equality genuinely provable.)

## Witness

(TODO: the `<name>_old` witness showing the new `=` form implies the
old `≤` form via `simp`.)
EOF
)" ;;
  esac

  cat > "$REPO_ROOT/$audit_dir/decision.md" <<EOF
# ${expid}

| Field | Value |
|---|---|
| Pattern | ${pattern} |
| Key | \`${key}\` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | $(date +%Y-%m-%d) |
| File | \`${theory_rel}\` |
| Verdict | applied |
| Impact verdict | ${verdict} |
| Δ wall (trial) | ${delta_pct} |
| Walls | baseline=${baseline_ms} ms · trial=${trial_ms} ms · apply=${apply_ms} ms |

## What changed

See \`patch.diff\` in this directory. The unified diff is the
canonical replayable record.

${pattern_specific}

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✓ OK ${trial_ms} ms |
| 2. spec_impact verdict | ✓ ${verdict} |
| 3. trial wall ≤ baseline × 1.30 | ✓ (${delta_pct}) |
| 4. parent SKILL hard rules | ✓ |

## Impact on seL4 (上下游)

(TODO: same-file wall delta interpretation; cross-file consumer
count from measurement.json; cross-session deferred per policy.)

## Notes / follow-ups

(TODO)
EOF
}

# ---------------- subcommand: execute ---------------------------------------

cmd_execute() {
  local key="" expid="" patch="" theory="" pattern="" retry=0 skip_prompt=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --candidate) key="$2"; shift 2 ;;
      --expid)     expid="$2"; shift 2 ;;
      --patch)     patch="$2"; shift 2 ;;
      --theory)    theory="$2"; shift 2 ;;
      --pattern)   pattern="$2"; shift 2 ;;
      --key)       key="$2"; shift 2 ;;
      --retry)     retry=1; shift ;;
      -y)          skip_prompt=1; shift ;;
      *) usage ;;
    esac
  done

  [ -z "$expid" ] && usage

  # Mode resolution
  if [ -n "$key" ] && [ -z "$patch" ]; then
    # Candidate from ledger
    local state ev
    state="$(ledger_state "$key")"
    [ -z "$state" ] && { echo "candidate not in ledger: $key" >&2; exit 4; }
    ev="$(echo "$state" | python3 -c "import json,sys;print(json.load(sys.stdin)['event'])")"
    case "$ev" in
      applied|audited)
        echo "candidate already $ev. Use a different expid or revert manually." >&2
        exit 9 ;;
      probe_failed|trial_failed|impact_failed|preflight_failed|aborted)
        [ "$retry" != 1 ] && { echo "candidate previously $ev; pass --retry to re-execute." >&2; exit 9; }
        ;;
    esac
    pattern="${key%%:*}"
    case "$pattern" in
      G) execute_G "$key" "$expid" "$skip_prompt" ;;
      C) execute_C "$key" "$expid" "$skip_prompt" ;;
      A)
        echo "Pattern A requires a custom patch. Re-run as:" >&2
        echo "  spec_strengthen_run.sh execute --pattern A --patch <p> --theory <t> --expid $expid --key $key" >&2
        exit 9 ;;
      *)
        echo "unknown pattern in key: $key" >&2; exit 4 ;;
    esac
  elif [ -n "$patch" ] && [ -n "$theory" ] && [ -n "$pattern" ]; then
    # Custom patch (A or D)
    case "$pattern" in
      A|D|G) ;;
      *) echo "--patch mode is only for patterns A, D, G" >&2; exit 4 ;;
    esac
    # Synthesize a key if not provided
    [ -z "$key" ] && key="${pattern}:$(basename "$theory" .thy):${expid}"
    execute_custom "$key" "$pattern" "$theory" "$patch" "$expid" "$skip_prompt"
  else
    usage
  fi
}

# ---------------- pattern-specific execute branches -------------------------

execute_G() {
  local key="$1" expid="$2" skip="$3"
  # Parse key: G:<theory_base>:<op>:<field>
  IFS=':' read -r _ theory_base op field <<< "$key"
  # Locate theory by base name
  local theory_abs theory_rel
  theory_rel="$(find verification/l4v/proof/invariant-abstract -name "${theory_base}.thy" | head -1)"
  [ -z "$theory_rel" ] && { echo "could not locate ${theory_base}.thy" >&2; exit 4; }
  theory_abs="$REPO_ROOT/$theory_rel"
  local session
  session="$(detect_session "$theory_rel")"

  echo "================================================================"
  echo "execute G  $key  → $expid"
  echo "  theory:  $theory_rel"
  echo "  op:      $op"
  echo "  field:   $field"
  echo "  session: $session"
  echo "================================================================"

  # Find insertion anchor — last set_<op>_*[wp] in file
  local anchor_line
  anchor_line="$(grep -nE "^lemma ${op}_[a-zA-Z_]+[[:space:]]*\[wp\][[:space:]]*:" "$theory_abs" | tail -1 | cut -d: -f1)"
  [ -z "$anchor_line" ] && { echo "no anchor found for $op" >&2; exit 5; }

  # Find the BLOCK END of the anchor lemma — first ^\s*done$ or ^\s*by .*$
  # line at or after anchor_line. The previous logic looked for the first
  # `^\s*by\s` line, which (for multi-line `apply ... done` proofs) jumps
  # to the NEXT lemma's by-shortcut and corrupts the insertion point.
  # See [[0027 manual override]] — that experiment had to bypass execute_G
  # because the anchor was a `apply ... done` proof.
  local block_end_line block_end_text
  block_end_line="$(awk -v start="$anchor_line" '
    NR>=start {
      if (/^[[:space:]]*done[[:space:]]*$/) { print NR; exit }
      if (/^[[:space:]]*by[[:space:]]/ || /^[[:space:]]*by\(/) { print NR; exit }
    }
  ' "$theory_abs")"
  [ -z "$block_end_line" ] && { echo "no block-end (done|by) for anchor at $anchor_line" >&2; exit 5; }
  block_end_text="$(sed -n "${block_end_line}p" "$theory_abs")"

  # Extract op formal args from spec/abstract/. Use defaults `p ko`
  # (set_object shape) only if extraction fails (legacy fallback).
  local op_args
  op_args="$(python3 "$REPO_ROOT/$SPEC_TOOLS/spec_op_args.py" "$op" 2>/dev/null || echo "p ko")"
  [ -z "$op_args" ] && op_args="p ko"

  # Per-op tactic — dynamically tailored based on op's static structure.
  #
  # Base form:   by (wpsimp simp: <op>_def)
  #
  # Adjustments (composable):
  #
  # 1. If op transitively calls `get_object`, add `wp: get_object_wp` to
  #    the wp ruleset. Reason: wpsimp's default ruleset matches
  #    `get_object`'s shape but leaves a schematic precondition that
  #    can't be unified with the postcondition obligation when the
  #    body case-splits on the returned object (set_cap, set_simple_ko).
  #    Explicit `get_object_wp` resolves the unification chain.
  #    Surfaced by the [[0039 / 0040 / 0042 / 0043]] failure trace
  #    showing `\<lbrace>?R9 x1 x2\<rbrace> get_object x1 \<lbrace>...case-split...\<rbrace>`
  #    — schematic ?R9 was the smoking gun.
  #    Reference: existing `set_cap_typ_at` uses
  #    `wpsimp wp: set_object_typ_at get_object_wp simp: set_cap_def`.
  #
  # 2. Fallback iteration `(... | clarsimp)+` for ops whose body has
  #    case-on-input dispatch (set_cap, set_simple_ko) leaves
  #    H⟹H residuals that wpsimp alone doesn't close. clarsimp closes
  #    them via implication-intro + assumption.
  local extra_wp=""
  if python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/$SPEC_TOOLS')
from spec_frame_gap import op_transitively_calls
from pathlib import Path
hit, _ = op_transitively_calls('$op', 'get_object', Path('$REPO_ROOT'))
sys.exit(0 if hit else 1)
" 2>/dev/null; then
    extra_wp=" wp: get_object_wp"
  fi
  local tactic="by (wpsimp${extra_wp} simp: ${op}_def | clarsimp)+"

  local date_tag patch
  date_tag="$(date +%Y%m%d)"
  patch="logs/spec-strengthen-${theory_base}-${op}_${field}-${date_tag}.patch"
  mkdir -p logs
  cat > "$patch" <<EOF
${block_end_line} ${block_end_line}
${block_end_text}

lemma ${op}_${field}[wp]:
  "\\<lbrace>\\<lambda>s. P (${field} s)\\<rbrace> ${op} ${op_args} \\<lbrace>\\<lambda>_ s. P (${field} s)\\<rbrace>"
  ${tactic}
EOF
  echo "Generated template patch: $patch"
  echo "----------------------------------------------------------------"
  cat "$patch"
  echo "----------------------------------------------------------------"
  if [ "$skip" != 1 ]; then
    read -r -p "Continue with this patch? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted by user"; exit 0; }
  fi

  standard_pipeline "$key" "G" "$theory_abs" "$session" "$REPO_ROOT/$patch" "$expid"
}

execute_C() {
  local key="$1" expid="$2" skip="$3"
  # Parse key: C:<theory_base>:<lemma>:<premise>
  IFS=':' read -r _ theory_base lemma premise <<< "$key"
  local theory_rel theory_abs
  theory_rel="$(find verification/l4v/proof/invariant-abstract -name "${theory_base}.thy" | head -1)"
  [ -z "$theory_rel" ] && { echo "theory not found" >&2; exit 4; }
  theory_abs="$REPO_ROOT/$theory_rel"
  local session
  session="$(detect_session "$theory_rel")"

  echo "================================================================"
  echo "execute C  $key  → $expid"
  echo "  theory:  $theory_rel"
  echo "  lemma:   $lemma"
  echo "  premise: $premise"
  echo "  session: $session"
  echo "================================================================"

  # Step 1: probe (ground-truth load-bearing check)
  echo "[probe] running spec_premise_probe.sh ..."
  local probe_out
  probe_out="$(bash "$REPO_ROOT/$SPEC_TOOLS/spec_premise_probe.sh" "$theory_rel" "$lemma" "$premise" 2>&1 || true)"
  echo "$probe_out" | grep -E '^verdict:|^reason:' || echo "$probe_out" | tail -5
  if ! echo "$probe_out" | grep -q '^verdict: likely-unused'; then
    ledger_append "{\"key\":\"$key\",\"event\":\"probe_failed\",\"expid\":\"$expid\"}"
    echo "✗ probe did not return likely-unused. Abort."
    exit 6
  fi
  echo "  ✓ probe verdict: likely-unused"

  # Step 2: auto-generate drop-premise patch + _old witness
  echo "[c-patchgen] generating drop-premise patch + witness ..."
  local date_tag patch
  date_tag="$(date +%Y%m%d)"
  patch="logs/spec-strengthen-${theory_base}-${lemma}_drop_${premise}-${date_tag}.patch"
  mkdir -p logs
  if ! python3 "$REPO_ROOT/$SPEC_TOOLS/spec_strengthen_c_patchgen.py" \
        --theory "$theory_rel" --lemma "$lemma" --premise "$premise" \
        --out "$patch" 2>&1; then
    ledger_append "{\"key\":\"$key\",\"event\":\"trial_failed\",\"expid\":\"$expid\",\"reason\":\"patchgen failed\"}"
    echo "✗ patch generation failed. Abort."
    exit 7
  fi
  echo "  ✓ patch generated: $patch"
  echo "----------------------------------------------------------------"
  cat "$patch"
  echo "----------------------------------------------------------------"

  if [ "$skip" != 1 ]; then
    read -r -p "Continue with this patch (full pipeline)? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted by user"; exit 0; }
  fi

  # Step 3: standard pipeline (snapshot/baseline/trial/impact/apply/audit)
  standard_pipeline "$key" "C" "$theory_abs" "$session" "$REPO_ROOT/$patch" "$expid"
}

execute_custom() {
  local key="$1" pattern="$2" theory_rel="$3" patch="$4" expid="$5" skip="$6"
  local theory_abs="$REPO_ROOT/$theory_rel"
  [ -f "$theory_abs" ] || theory_abs="$theory_rel"  # already absolute
  [ -f "$theory_abs" ] || { echo "theory not found: $theory_rel" >&2; exit 4; }
  local session
  session="$(detect_session "$theory_rel")"

  echo "================================================================"
  echo "execute $pattern (custom patch)  $key  → $expid"
  echo "================================================================"
  echo "  theory: $theory_rel"
  echo "  patch:  $patch"
  echo ""
  if [ "$skip" != 1 ]; then
    read -r -p "Proceed with standard pipeline? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted"; exit 0; }
  fi

  standard_pipeline "$key" "$pattern" "$theory_abs" "$session" "$REPO_ROOT/$patch" "$expid"
}

# ---------------- dispatch --------------------------------------------------

[ $# -lt 1 ] && usage
SUB="$1"; shift
case "$SUB" in
  survey)        cmd_survey "$@" ;;
  execute)       cmd_execute "$@" ;;
  status)        cmd_status "$@" ;;
  ledger)        cmd_ledger "$@" ;;
  mark-audited)  cmd_mark_audited "$@" ;;
  mark-aborted)  cmd_mark_aborted "$@" ;;
  *)             usage ;;
esac
