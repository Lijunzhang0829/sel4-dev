#!/usr/bin/env bash
# Launch a fresh local experiment through the existing planning/proof harness.
# Planning runs in the caller's job; the final proof runner uses its user service.
set -euo pipefail
if [[ $# != 1 || "$1" == --help ]]; then
  echo "Usage: bash regen/config/crefine-owner-blind-sol-xhigh-local.sh NEW_OUTPUT_DIRECTORY"
  echo "Runs Sol/xhigh with one 172800-second effective budget including reviewed planning."
  exit 2
fi

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
output=$(realpath -m -- "$1")
dataset="$repo/reports/regen/crefine-owner-blind-input-20261024-001/dataset"
python=$(command -v python3)
cd "$repo"
export PYTHONPATH="$repo"
export PYTHONDONTWRITEBYTECODE=1

# A changed dataset must get its own explicit configuration and audit.
"$python" - "$dataset" "$output" <<'PY'
import hashlib, json, sys
from pathlib import Path
from regen.preparation.checker_context import load_context
from regen.planning.owner_blind import OwnerBlindView
dataset, output = map(Path, sys.argv[1:])
if output.exists():
    raise ValueError('fresh output required; never reset an existing budget')
raw = (dataset/'repository-planning-input.json').read_bytes()
if hashlib.sha256(raw).hexdigest() != 'ed996b3328b67f63a789a737e434d44e4052453274c0a62b62ae20b8539d94f1':
    raise ValueError('anonymous CRefine input changed')
payload = json.loads(raw)
if (len(payload['contracts']) != 309 or payload['contract_visibility'] != 'owner-blind-v1'
        or payload['theory_import_graph'] or payload['session_graph']
        or any(c['owner_theory'] != 'CRefine.Fixed_Targets' or not c['contract_id'].startswith('goal:')
               for c in payload['contracts'])):
    raise ValueError('owner-blind CRefine contract boundary mismatch')
view = OwnerBlindView(payload).payload
if any('owner_theory' in c or 'owner_file_slot' in c for c in view['contracts']):
    raise ValueError('planner view discloses ownership')
load_context(dataset)
PY

mkdir -- "$output"
# Freeze the latest local implementation, including current uncommitted fixes.
"$python" -m regen.tools.application_release export --repository "$repo" --output "$output/release"
mkdir -- "$output/application"
tar -xzf "$output/release/application.tar.gz" -C "$output/application"
cd "$output/application"
export PYTHONPATH="$output/application"
export REGEN_MODEL_CONFIG="$output/application/regen/config/proof-models-crefine-owner-blind-sol-xhigh-local.json"
"$python" -m regen.tools.application_release verify --repository "$output/application" --manifest "$output/release/manifest.json"

"$python" -u -m regen.tools.reviewed_planning \
  --input "$dataset/repository-planning-input.json" \
  --policy regen/config/repository-planning-crefine-owner-blind-sol-xhigh-local.json \
  --output "$output/planning" \
  --model gpt-5.6-sol --reasoning-effort xhigh \
  --include-planning-time --effective-seconds 172800 \
  --allow-external-call

# Select only the exact reviewed plan bound by the planning budget receipt.
"$python" - "$output" "$dataset" <<'PY'
import json, sys
from pathlib import Path
from regen.execution.proof_commits import durable
from regen.execution.proof_session_runner import init_run
from regen.infrastructure.reviewed_planning_budget import file_hash
output, dataset = map(Path, sys.argv[1:])
planning = output/'planning'
frozen = json.loads((planning/'frozen-plan.json').read_text())
receipt = json.loads((planning/'budget-receipt.json').read_text())
if (frozen['state'] != 'frozen' or receipt['state'] != 'complete'
        or receipt['frozen_plan_sha256'] != file_hash(planning/'frozen-plan.json')
        or receipt['model'] != 'gpt-5.6-sol' or receipt['reasoning_effort'] != 'xhigh'
        or receipt['seconds'] != 172800 or receipt['include_planning'] is not True):
    raise ValueError('incomplete or inconsistent reviewed planning receipt')
proof = output/'proof'
init_run(proof, dataset, run_id=output.name,
         schedule_path=Path(frozen['file_generation_list']), log_storage='regen-merkle-json-v1')
command = [sys.executable, '-u', '-m', 'regen.execution.proof_session_runner',
    'parallel-workspace', '--run-dir', str(proof),
    '--planner-model', 'gpt-5.6-sol', '--file-planner-interface', 'document',
    '--max-parallel-theories', '1', '--native-slots', '1', '--resident-limit', '1',
    '--native-threads', '4', '--native-cpus', '4', '--native-memory-mb', '16384',
    '--native-pids-limit', '1024', '--build-ml-heap-mb', '12288',
    '--provider-build-timeout', '1800', '--call-timeout', '900',
    '--check-timeout', '900', '--candidate-timeout', '300',
    '--generation-strategy', 'sketch-reviewed-v1', '--effective-seconds', '172800',
    '--execution-policy', str(output/'application/regen/config/provider-feedback-retry-local.json'),
    '--planning-budget', str(planning), '--allow-external-call']
durable(proof/'experiment.json', {
    'schema_version': 1, 'cwd': str(output/'application'), 'command': command,
    'contract_visibility': 'owner-blind-v1', 'model': 'gpt-5.6-sol', 'reasoning_effort': 'xhigh',
    'effective_seconds': 172800, 'include_planning': True,
    'planning_budget_receipt_sha256': file_hash(planning/'budget-receipt.json'),
    'resource_requirements': {'min_free_disk_bytes': 8*1024**3,
                              'min_available_memory_bytes': 28*1024**3}}, immutable=True)
PY
exec "$python" -m regen.tools.parallel_workspace_service start --run-dir "$output/proof"
