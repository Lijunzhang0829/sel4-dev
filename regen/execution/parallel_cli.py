"""Current Ours launch surface and explicit decoding of frozen legacy commands.

New experiments use Document, one experiment clock and baseline/reviewed-v2. Retired
arguments are accepted only where necessary to resume their frozen configuration.
No decoder can reset consumed costs or change a frozen budget policy.
"""
import argparse
from pathlib import Path

from regen.infrastructure.incremental_log import read_artifact
from regen.planning.generation_policy import CURRENT_STRATEGIES, POLICIES
from regen.infrastructure.experiment_runtime_budget import MODE, DEFAULT_SECONDS


def add_arguments(parser):
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--theory', action='append', dest='theories', help='Repeat to select theories; omit for all')
    parser.add_argument('--planner-model', help='Must match the frozen File Planner model')
    parser.add_argument('--call-timeout', type=int, default=600)
    parser.add_argument('--check-timeout', type=int, default=900)
    parser.add_argument('--candidate-timeout', type=int, default=300)
    parser.add_argument('--max-parallel-theories', type=int, default=1)
    parser.add_argument('--native-slots', type=int, default=1)
    parser.add_argument('--native-cpus', type=float, default=4)
    parser.add_argument('--native-memory-mb', type=int, default=16384)
    parser.add_argument('--native-pids-limit', type=int, default=1024)
    parser.add_argument('--native-threads', type=int, default=4)
    parser.add_argument('--provider-build-timeout', type=int, default=1800)
    parser.add_argument('--build-ml-heap-mb', type=int, default=None)
    parser.add_argument('--resident-limit', type=int, default=1)
    parser.add_argument('--generation-strategy', choices=tuple(POLICIES), default=None,
        metavar='{'+','.join(CURRENT_STRATEGIES)+'}',
        help='New runs: baseline or sketch-reviewed-v3 (default); resume: exact frozen version')
    parser.add_argument('--budget-seconds', type=float, default=None,
        help='Effective experiment seconds, including planning; default 172800s. Pauses and evidenced outages are excluded')
    parser.add_argument('--planning-budget', type=Path,
        help='Completed planning receipt; required on the first experiment launch, imported once')
    parser.add_argument('--resume-authorization',
        help='Audit reference authorizing a paused run to continue; costs and limits remain bound')

    # Input compatibility, not choices advertised to new experiments. Old service
    # manifests can keep their exact argv; altered frozen policies still fail.
    parser.add_argument('--allow-external-call', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--codex-home', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--focus-contract-id', help=argparse.SUPPRESS)
    parser.add_argument('--file-planner-interface', choices=('document', 'graph'), help=argparse.SUPPRESS)
    parser.add_argument('--execution-policy', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--budget-mode', choices=('normal-runtime', 'effective-time'), help=argparse.SUPPRESS)
    parser.add_argument('--effective-seconds', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--wall-seconds', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--extend-effective-seconds', type=float, help=argparse.SUPPRESS)


def _legacy_allowance(root):
    # This is only a read-only input hint. The budget constructor subsequently
    # validates the complete ledger chain, run/configuration identity and limits.
    for path in sorted((root/'effective-generation-budget/events').glob('*.json'), reverse=True):
        row = read_artifact(path)
        if row['kind'] == 'time_budget_checkpoint':
            return row['data']['state']['base_seconds']
    raise ValueError('frozen legacy allowance unavailable; supply --budget-seconds explicitly')


def launch_options(args):
    root = args.run_dir.resolve()
    policy_path = root/'parallel-workspace-policy.json'
    frozen = read_artifact(policy_path) if policy_path.exists() else None
    interface = frozen.get('file_planner_interface', 'document') if frozen else 'document'
    if args.file_planner_interface is not None and args.file_planner_interface != interface:
        raise ValueError('new experiments use document; a frozen interface cannot be changed')
    strategy = args.generation_strategy
    if strategy is not None and strategy not in CURRENT_STRATEGIES:
        if not frozen or strategy != frozen.get('generation_strategy', {}).get('name'):
            raise ValueError('new experiments accept only baseline and sketch-reviewed-v3')
    execution = frozen.get('execution_policy') if frozen else None
    if args.execution_policy is not None:
        supplied = read_artifact(args.execution_policy)
        if frozen and supplied != execution:
            from regen.planning.parallel_file_planner import _execution_policy
            supplied = _execution_policy(supplied, frozen['selected_theories'])
        if not frozen or supplied != execution:
            raise ValueError('execution-policy is retired; only the exact frozen recovery policy may be replayed')
    mode = frozen.get('budget_mode', 'effective-time') if frozen else MODE
    if args.budget_mode is not None and args.budget_mode != mode:
        raise ValueError('new experiments use one experiment clock; the frozen clock cannot be changed')
    if not frozen and any(v is not None for v in
            (args.effective_seconds, args.wall_seconds, args.extend_effective_seconds)):
        raise ValueError('legacy budget flags require a frozen run; use --budget-seconds for a new experiment')
    if mode in {'normal-runtime',MODE}:
        # Old normal-runtime launchers sometimes supplied an unused legacy
        # effective allowance. Preserve its original no-effect semantics.
        seconds = args.budget_seconds if args.budget_seconds is not None else (
            frozen['budget_seconds'] if frozen else DEFAULT_SECONDS)
        if frozen and seconds != frozen['budget_seconds']:
            raise ValueError('normal-runtime allowance is frozen; resume cannot replace or reset it')
    else:
        if args.budget_seconds is not None and args.effective_seconds is not None:
            if args.budget_seconds != args.effective_seconds:
                raise ValueError('conflicting budget allowances')
        seconds = args.budget_seconds if args.budget_seconds is not None else args.effective_seconds
        if seconds is None:
            seconds = _legacy_allowance(root)
    return dict(theories=args.theories, model=args.planner_model,
        max_parallel_theories=args.max_parallel_theories, native_slots=args.native_slots,
        file_planner_interface=interface, native_threads=args.native_threads,
        resident_limit=args.resident_limit, native_cpus=args.native_cpus,
        native_memory_mb=args.native_memory_mb, native_pids_limit=args.native_pids_limit,
        provider_build_timeout=args.provider_build_timeout, build_ml_heap_mb=args.build_ml_heap_mb,
        generation_strategy_name=strategy, focus_contract_id=args.focus_contract_id,
        effective_seconds=seconds, budget_mode=mode,
        budget_seconds=seconds if mode in {'normal-runtime',MODE} else None,
        wall_seconds=args.wall_seconds, planning_budget=args.planning_budget,
        resume_authorization=args.resume_authorization,
        extend_effective_seconds=args.extend_effective_seconds, execution_policy=execution,
        call_timeout=args.call_timeout, check_timeout=args.check_timeout,
        candidate_timeout=args.candidate_timeout, allow_external_call=True,
        codex_home=args.codex_home)
