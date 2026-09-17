#!/usr/bin/env bash
# Run one complete, auditable .thy generation attempt.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DEFAULT_CAMPAIGN="$REPO_ROOT/reports/regen/refine-arm-session"

usage() {
  cat <<'EOF'
Usage:
  run_theory_generation.sh --theory Refine.NAME [options]

Options:
  --campaign-dir DIR       Campaign directory.
  --pipeline NAME          auto, support-checkpoint, bits-contract, or archacc-contract.
  --provider NAME          codex or claude.
  --model NAME             Provider model (default: gpt-5.5 / opus).
  --timeout SECONDS        Timeout for each LLM call (default: 1800).
  --generation-id ID       Use an explicit new generation ID.
  --resume ID              Resume the same theory-generation attempt.
  --resume latest          Resume the latest recorded ID for this theory.
  --allow-external-payload Acknowledge sending bounded proof context to provider.
  --fresh-proofs          Regenerate every proof; retain old green proofs only as evidence.
  --helper-bundle-contract NAME  Atomically replan one red ArchAcc_R contract.
  --helper-bundle-source-generation ID  Read prior machine residuals from ID.
  --helper-bundle-only     Stop after the replacement helper bundle is green.
  --dry-run                Create/resolve the ID and print commands only.
  -h, --help               Show this help.

A generation ID names one complete attempt to generate one .thy file. It is
shared by preflight, planning, all lemma/proof/repair calls, Isabelle checks, assembly,
heap build, smoke, and seal. It is not a lemma ID or an LLM-call ID.
EOF
}

THEORY=""
CAMPAIGN_DIR="$DEFAULT_CAMPAIGN"
PIPELINE="auto"
PROVIDER="codex"
MODEL=""
TIMEOUT="1800"
GENERATION_ID=""
RESUME_ID=""
ALLOW_EXTERNAL=0
FRESH_PROOFS=0
HELPER_BUNDLE_CONTRACT=""
HELPER_BUNDLE_SOURCE_GENERATION=""
HELPER_BUNDLE_ONLY=0
DRY_RUN=0

while (($#)); do
  case "$1" in
    --theory) THEORY="${2:?missing theory}"; shift 2 ;;
    --campaign-dir) CAMPAIGN_DIR="${2:?missing campaign directory}"; shift 2 ;;
    --pipeline) PIPELINE="${2:?missing pipeline}"; shift 2 ;;
    --provider) PROVIDER="${2:?missing provider}"; shift 2 ;;
    --model) MODEL="${2:?missing model}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?missing timeout}"; shift 2 ;;
    --generation-id) GENERATION_ID="${2:?missing generation ID}"; shift 2 ;;
    --resume) RESUME_ID="${2:?missing resume ID}"; shift 2 ;;
    --allow-external-payload) ALLOW_EXTERNAL=1; shift ;;
    --fresh-proofs) FRESH_PROOFS=1; shift ;;
    --helper-bundle-contract) HELPER_BUNDLE_CONTRACT="${2:?missing contract name}"; shift 2 ;;
    --helper-bundle-source-generation) HELPER_BUNDLE_SOURCE_GENERATION="${2:?missing source generation ID}"; shift 2 ;;
    --helper-bundle-only) HELPER_BUNDLE_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$THEORY" ]] || { echo "--theory is required" >&2; exit 2; }
[[ "$THEORY" =~ ^Refine\.[A-Za-z][A-Za-z0-9_]*$ ]] || {
  echo "invalid or unsupported theory name: $THEORY" >&2
  exit 2
}
[[ "$PROVIDER" == "codex" || "$PROVIDER" == "claude" ]] || {
  echo "--provider must be codex or claude" >&2
  exit 2
}
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "--timeout must be a positive integer" >&2
  exit 2
}
[[ -z "$GENERATION_ID" || -z "$RESUME_ID" ]] || {
  echo "--generation-id and --resume are mutually exclusive" >&2
  exit 2
}
((DRY_RUN || ALLOW_EXTERNAL)) || {
  echo "real provider generation requires --allow-external-payload" >&2
  exit 2
}
if ((HELPER_BUNDLE_ONLY)); then
  [[ -n "$HELPER_BUNDLE_CONTRACT" && -n "$HELPER_BUNDLE_SOURCE_GENERATION" ]] || {
    echo "--helper-bundle-only requires --helper-bundle-contract and --helper-bundle-source-generation" >&2
    exit 2
  }
fi
[[ -z "$HELPER_BUNDLE_SOURCE_GENERATION" || -n "$HELPER_BUNDLE_CONTRACT" ]] || {
  echo "--helper-bundle-source-generation requires --helper-bundle-contract" >&2
  exit 2
}

CAMPAIGN_DIR="$(realpath -m "$CAMPAIGN_DIR")"
BASENAME="${THEORY#Refine.}"
THEORY_AUDIT_DIR="$CAMPAIGN_DIR/audit/$BASENAME"
LATEST_ID_PATH="$THEORY_AUDIT_DIR/latest-generation-id"

case "$PIPELINE:$THEORY" in
  auto:Refine.InvariantUpdates_H|auto:Refine.Invocations_R|auto:Refine.Init_R|auto:Refine.SubMonad_R)
    PIPELINE="support-checkpoint"
    ;;
  auto:Refine.Bits_R)
    PIPELINE="bits-contract"
    ;;
  auto:Refine.ArchAcc_R)
    PIPELINE="archacc-contract"
    ;;
  auto:*)
    echo "no production pipeline registered for $THEORY" >&2
    echo "register its deterministic driver in $SCRIPT_DIR/run_theory_generation.sh" >&2
    exit 3
    ;;
  support-checkpoint:Refine.InvariantUpdates_H|support-checkpoint:Refine.Invocations_R|support-checkpoint:Refine.Init_R|support-checkpoint:Refine.SubMonad_R)
    ;;
  bits-contract:Refine.Bits_R)
    ;;
  archacc-contract:Refine.ArchAcc_R)
    ;;
  *)
    echo "pipeline $PIPELINE is not valid for $THEORY" >&2
    exit 3
    ;;
esac

if [[ -z "$MODEL" ]]; then
  if [[ "$PROVIDER" == "claude" ]]; then MODEL="opus"; else MODEL="gpt-5.5"; fi
fi

if ((DRY_RUN == 0)); then
  mkdir -p "$THEORY_AUDIT_DIR"
  exec 9>"$THEORY_AUDIT_DIR/.generation.lock"
  flock -n 9 || {
    echo "another generation process already owns $THEORY" >&2
    exit 5
  }
fi

new_generation_id() {
  local timestamp random
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  random="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
  printf '%s-%s' "$timestamp" "$random"
}

if [[ "$RESUME_ID" == "latest" ]]; then
  [[ -s "$LATEST_ID_PATH" ]] || {
    echo "no latest generation ID for $THEORY" >&2
    exit 4
  }
  RESUME_ID="$(<"$LATEST_ID_PATH")"
fi

if [[ -n "$RESUME_ID" ]]; then
  GENERATION_ID="$RESUME_ID"
  [[ "$GENERATION_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || {
    echo "invalid generation ID: $GENERATION_ID" >&2
    exit 2
  }
  [[ -f "$THEORY_AUDIT_DIR/$GENERATION_ID/generation-id.txt" || -f "$THEORY_AUDIT_DIR/$GENERATION_ID/run.json" ]] || {
    echo "cannot resume missing generation: $GENERATION_ID" >&2
    exit 4
  }
else
  GENERATION_ID="${GENERATION_ID:-$(new_generation_id)}"
  [[ "$GENERATION_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || {
    echo "invalid generation ID: $GENERATION_ID" >&2
    exit 2
  }
  [[ ! -e "$THEORY_AUDIT_DIR/$GENERATION_ID" ]] || {
    echo "generation already exists; use --resume $GENERATION_ID" >&2
    exit 4
  }
fi

RUN_DIR="$THEORY_AUDIT_DIR/$GENERATION_ID"

if ((DRY_RUN)); then
  echo "theory=$THEORY"
  echo "generation_id=$GENERATION_ID (preview only; not persisted)"
  echo "pipeline=$PIPELINE provider=$PROVIDER model=$MODEL timeout=$TIMEOUT fresh_proofs=$FRESH_PROOFS"
  case "$PIPELINE" in
    support-checkpoint)
      printf "python3 regen/legacy/refine_arm_session/theory_preflight.py --campaign-dir %q --theory %q --generation-id %q\n" "$CAMPAIGN_DIR" "$THEORY" "$GENERATION_ID"
      printf "python3 regen/legacy/refine_arm_session/theory_export_planner.py --campaign-dir %q --theory %q --provider %q --model %q --timeout %q --generation-id %q --allow-external-payload\n" "$CAMPAIGN_DIR" "$THEORY" "$PROVIDER" "$MODEL" "$TIMEOUT" "$GENERATION_ID"
      printf "python3 regen/legacy/refine_arm_session/theory_checkpoint.py --campaign-dir %q --theory %q --generation-id %q\n" "$CAMPAIGN_DIR" "$THEORY" "$GENERATION_ID"
      ;;
    bits-contract)
      printf "python3 regen/legacy/refine_arm_session/contract_milestone.py --campaign-dir %q --provider %q --model %q --timeout %q --generation-id %q --allow-external-payload\n" "$CAMPAIGN_DIR" "$PROVIDER" "$MODEL" "$TIMEOUT" "$GENERATION_ID"
      ;;
    archacc-contract)
      fresh_args=()
      if ((FRESH_PROOFS)); then fresh_args+=(--fresh-proofs); fi
      if [[ -n "$HELPER_BUNDLE_CONTRACT" ]]; then fresh_args+=(--helper-bundle-contract "$HELPER_BUNDLE_CONTRACT"); fi
      if [[ -n "$HELPER_BUNDLE_SOURCE_GENERATION" ]]; then fresh_args+=(--helper-bundle-source-generation "$HELPER_BUNDLE_SOURCE_GENERATION"); fi
      if ((HELPER_BUNDLE_ONLY)); then fresh_args+=(--helper-bundle-only); fi
      printf "python3 regen/legacy/refine_arm_session/archacc_milestone.py --campaign-dir %q --provider %q --model %q --timeout %q --generation-id %q" "$CAMPAIGN_DIR" "$PROVIDER" "$MODEL" "$TIMEOUT" "$GENERATION_ID"
      if ((${#fresh_args[@]})); then printf " %q" "${fresh_args[@]}"; fi
      printf " --allow-external-payload\n"
      ;;
  esac
  exit 0
fi

if [[ -f "$RUN_DIR/manifest.json" ]]; then
  echo "$THEORY generation $GENERATION_ID is already sealed"
  echo "manifest: $RUN_DIR/manifest.json"
  exit 0
fi

mkdir -p "$RUN_DIR"
printf "%s\n" "$GENERATION_ID" > "$LATEST_ID_PATH"
printf "%s\n" "$GENERATION_ID" > "$RUN_DIR/generation-id.txt"

LOG="$RUN_DIR/launcher.log"
exec > >(tee -a "$LOG") 2>&1

STATUS="failed"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finish() {
  local rc=$?
  local finished_at
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    printf 'theory=%q\n' "$THEORY"
    printf 'generation_id=%q\n' "$GENERATION_ID"
    printf 'pipeline=%q\n' "$PIPELINE"
    printf 'provider=%q\n' "$PROVIDER"
    printf 'model=%q\n' "$MODEL"
    printf 'fresh_proofs=%q\n' "$FRESH_PROOFS"
    printf 'started_at=%q\n' "$started_at"
    printf 'finished_at=%q\n' "$finished_at"
    printf 'status=%q\n' "$STATUS"
    printf 'exit_code=%q\n' "$rc"
  } > "$RUN_DIR/launcher-status.env"
  if ((rc != 0)); then
    echo "generation interrupted/failed; resume with:"
    local fresh_option=""
    if ((FRESH_PROOFS)); then fresh_option=" --fresh-proofs"; fi
    if [[ -n "$HELPER_BUNDLE_CONTRACT" ]]; then fresh_option+=" --helper-bundle-contract $HELPER_BUNDLE_CONTRACT"; fi
    if [[ -n "$HELPER_BUNDLE_SOURCE_GENERATION" ]]; then fresh_option+=" --helper-bundle-source-generation $HELPER_BUNDLE_SOURCE_GENERATION"; fi
    if ((HELPER_BUNDLE_ONLY)); then fresh_option+=" --helper-bundle-only"; fi
    echo "  $0 --theory $THEORY --resume $GENERATION_ID --provider $PROVIDER --model $MODEL --timeout $TIMEOUT${fresh_option} --allow-external-payload"
  fi
}
trap finish EXIT

echo "== theory generation =="
echo "theory=$THEORY"
echo "generation_id=$GENERATION_ID"
echo "pipeline=$PIPELINE provider=$PROVIDER model=$MODEL timeout=$TIMEOUT fresh_proofs=$FRESH_PROOFS"
echo "campaign=$CAMPAIGN_DIR"

run_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if ((DRY_RUN == 0)); then
    "$@"
  fi
}

has_event() {
  local kind="$1" stage="$2"
  [[ -s "$RUN_DIR/events.jsonl" ]] &&
    jq -e --arg kind "$kind" --arg stage "$stage" \
      'select(.kind == $kind and .stage == $stage)' \
      "$RUN_DIR/events.jsonl" >/dev/null
}

cd "$REPO_ROOT"

case "$PIPELINE" in
  support-checkpoint)
    if ! has_event harness planning-complete; then
      if ! has_event harness preflight-complete; then
        run_cmd python3 regen/legacy/refine_arm_session/theory_preflight.py \
          --generation-id "$GENERATION_ID" \
          --campaign-dir "$CAMPAIGN_DIR" --theory "$THEORY"
      else
        echo "[resume] preflight-complete already recorded; skipping preflight"
      fi
      run_cmd python3 regen/legacy/refine_arm_session/theory_export_planner.py \
        --campaign-dir "$CAMPAIGN_DIR" --theory "$THEORY" \
        --provider "$PROVIDER" --model "$MODEL" --timeout "$TIMEOUT" \
        --generation-id "$GENERATION_ID" --allow-external-payload
    else
      echo "[resume] planning-complete already recorded; skipping preflight/planner"
    fi
    run_cmd python3 regen/legacy/refine_arm_session/theory_checkpoint.py \
      --campaign-dir "$CAMPAIGN_DIR" --theory "$THEORY" \
      --generation-id "$GENERATION_ID"
    ;;
  bits-contract)
    run_cmd python3 regen/legacy/refine_arm_session/contract_milestone.py \
      --campaign-dir "$CAMPAIGN_DIR" --provider "$PROVIDER" \
      --model "$MODEL" --timeout "$TIMEOUT" \
      --generation-id "$GENERATION_ID" --allow-external-payload
    ;;
  archacc-contract)
    fresh_args=()
    if ((FRESH_PROOFS)); then fresh_args+=(--fresh-proofs); fi
    if [[ -n "$HELPER_BUNDLE_CONTRACT" ]]; then fresh_args+=(--helper-bundle-contract "$HELPER_BUNDLE_CONTRACT"); fi
    if [[ -n "$HELPER_BUNDLE_SOURCE_GENERATION" ]]; then fresh_args+=(--helper-bundle-source-generation "$HELPER_BUNDLE_SOURCE_GENERATION"); fi
    if ((HELPER_BUNDLE_ONLY)); then fresh_args+=(--helper-bundle-only); fi
    run_cmd python3 regen/legacy/refine_arm_session/archacc_milestone.py \
      --campaign-dir "$CAMPAIGN_DIR" --provider "$PROVIDER" \
      --model "$MODEL" --timeout "$TIMEOUT" \
      --generation-id "$GENERATION_ID" "${fresh_args[@]}" \
      --allow-external-payload
    ;;
esac

if ((DRY_RUN)); then
  STATUS="dry-run"
  echo "dry-run complete; no generation stages executed"
else
  if ((HELPER_BUNDLE_ONLY)); then
    BUNDLE_RESULT="$RUN_DIR/planner/helper-bundle-only-result.json"
    [[ -f "$BUNDLE_RESULT" ]] || {
      echo "driver returned without a green helper-bundle result: $BUNDLE_RESULT" >&2
      exit 6
    }
    jq -e '.status == "helper_bundle_green" and .theory_sealed == false' "$BUNDLE_RESULT" >/dev/null || {
      echo "helper-bundle result did not satisfy the bundle-only success contract" >&2
      exit 6
    }
    STATUS="helper-bundle-complete"
    echo "== helper bundle complete =="
    echo "generation_id=$GENERATION_ID"
    echo "result=$BUNDLE_RESULT"
  else
    [[ -f "$RUN_DIR/manifest.json" ]] || {
      echo "driver returned without a production audit seal: $RUN_DIR/manifest.json" >&2
      exit 6
    }
    STATUS="sealed"
    echo "== sealed =="
    echo "generation_id=$GENERATION_ID"
    echo "manifest=$RUN_DIR/manifest.json"
  fi
fi
