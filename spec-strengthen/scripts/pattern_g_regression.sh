#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DETECTOR="$ROOT/spec-strengthen/scripts/spec_frame_gap.py"
TMPDIR="${TMPDIR:-/tmp}/pattern-g-regression-$$"
KEEP=0

usage() {
  cat <<'EOF'
pattern_g_regression.sh [--keep-tmp]

Run the retroactive regression harness for the Pattern G detector.
It checks two sample sets:
  1. EXPECTED-FAIL: candidates that must now be preflight_failed
  2. CONTROL: candidates that must remain clean in the current tree
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-tmp) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$TMPDIR"
cleanup() {
  if [ "$KEEP" -ne 1 ]; then
    rm -rf "$TMPDIR"
  else
    echo "tmp kept at: $TMPDIR"
  fi
}
trap cleanup EXIT

run_detector() {
  local theory="$1"
  local out="$2"
  python3 "$DETECTOR" "$ROOT/$theory" > "$out"
}

assert_rg() {
  local desc="$1"
  local pattern="$2"
  shift 2
  if rg -q "$pattern" "$@"; then
    echo "  [PASS] $desc"
  else
    echo "  [FAIL] $desc" >&2
    echo "         pattern: $pattern" >&2
    echo "         files:   $*" >&2
    return 1
  fi
}

echo "[1/3] run detector on regression sample files"
IPC="$TMPDIR/ipc.jsonl"
TCBACC="$TMPDIR/tcbacc.jsonl"
KHEAP="$TMPDIR/kheap.jsonl"
run_detector 'verification/l4v/proof/invariant-abstract/Ipc_AI.thy' "$IPC"
run_detector 'verification/l4v/proof/invariant-abstract/TcbAcc_AI.thy' "$TCBACC"
run_detector 'verification/l4v/proof/invariant-abstract/KHeap_AI.thy' "$KHEAP"

echo "[2/3] EXPECTED-FAIL assertions"
assert_rg 'Ipc_AI:set_mrs:machine_state -> dmo_path' '"key": "G:Ipc_AI:set_mrs:machine_state".*"status": "preflight_failed:dmo_path"' "$IPC"
assert_rg 'TcbAcc_AI:set_mrs:machine_state -> dmo_path' '"key": "G:TcbAcc_AI:set_mrs:machine_state".*"status": "preflight_failed:dmo_path"' "$TCBACC"
assert_rg 'Ipc_AI:set_extra_badge:machine_state -> dmo_path' '"key": "G:Ipc_AI:set_extra_badge:machine_state".*"status": "preflight_failed:dmo_path"' "$IPC"
assert_rg 'TcbAcc_AI:set_thread_state:domain_index -> dxo_path' '"key": "G:TcbAcc_AI:set_thread_state:domain_index".*"status": "preflight_failed:dxo_path"' "$TCBACC"
assert_rg 'TcbAcc_AI:set_thread_state:domain_time -> dxo_path' '"key": "G:TcbAcc_AI:set_thread_state:domain_time".*"status": "preflight_failed:dxo_path"' "$TCBACC"

echo "[3/3] CONTROL assertions"
assert_rg 'KHeap_AI:set_ep:machine_state stays clean' '"key": "G:KHeap_AI:set_ep:machine_state".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_ep:domain_index stays clean' '"key": "G:KHeap_AI:set_ep:domain_index".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_ep:domain_time stays clean' '"key": "G:KHeap_AI:set_ep:domain_time".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_ep:arch_state stays clean' '"key": "G:KHeap_AI:set_ep:arch_state".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_aobject:machine_state stays clean' '"key": "G:KHeap_AI:set_aobject:machine_state".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_aobject:domain_index stays clean' '"key": "G:KHeap_AI:set_aobject:domain_index".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_aobject:domain_time stays clean' '"key": "G:KHeap_AI:set_aobject:domain_time".*"status": "clean"' "$KHEAP"
assert_rg 'KHeap_AI:set_aobject:arch_state stays clean' '"key": "G:KHeap_AI:set_aobject:arch_state".*"status": "clean"' "$KHEAP"

echo
echo 'Pattern G detector regression: PASS'
