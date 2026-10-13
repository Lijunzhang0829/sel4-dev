"""Prepare an explicitly authorized *incremental diagnostic* checkpoint clock.

This is not a cost migration, production resume, or refund. It may initialize
only an untouched, independently named fork made by fork_workspace_continuation
with a bound historical checkpoint. Source and accepted journals are read-only.
The ordinary parallel runner must subsequently resume this clock with the same
configuration and explicit authorization; no normal runner gate is bypassed.

Keep dependencies limited to APIs present in the frozen 009 application so the
same helper can initialize both old/new application arms by file-path import.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path

from regen.execution.proof_commits import digest
from regen.infrastructure.incremental_log import read_artifact


SCHEMA = 'checkpoint-incremental-diagnostic-budget-v1'
MAX_TRIAL_SECONDS = 3600


def _file(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(label + ' must be a regular, non-symlink file')
    return path


def _sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()


def _unused(root):
    for name in ('effective-generation-budget','service-launch.json','service-invocation.json',
                 'service-status.json','parallel-workspace-policy.json','parallel-sessions'):
        if (root/name).exists():
            raise ValueError('checkpoint trial requires a fresh unused fork; already exists: '+name)
    for pattern in ('**/session-transcript.jsonl','theories/*/document/edits/*',
                    'theories/*/document/last-edit.json','tasks/*','check-tasks/*'):
        if next(root.glob(pattern),None) is not None:
            raise ValueError('checkpoint trial has post-fork execution or edit artifacts: '+pattern)


def _inheritance(root, overview, checkpoint, source_run):
    """Verify accepted authority using normal replay, then check fork shape."""
    from regen.execution.repository_execution import journal
    selected = checkpoint['theories']
    inheritance = {}
    states = {}
    for theory, selection in selected.items():
        package = overview.shared._package(theory)
        slot = Path(package['file_slot'])
        if slot.name != package['file_slot'] or slot.suffix != '.thy':
            raise ValueError('unsafe checkpoint file slot')
        directory = root/'theories'/slot.stem
        if directory.is_symlink() or directory.resolve() != directory:
            raise ValueError('checkpoint theory directory escapes fork')
        commits = sorted((directory/'acceptance/commits').glob('*.json'))
        if len(commits) not in (1,2):
            raise ValueError('checkpoint fork journal has new generation records: '+theory)
        for path in commits:
            _file(path,'checkpoint acceptance record')
        log = journal(root,overview.run,package)
        snapshot = log.replay_snapshot()
        if not snapshot:
            raise ValueError('checkpoint lacks verified inherited state: '+theory)
        records = snapshot['records']
        events = [row['event'] for row in records]
        if events not in (['initial'],['initial','seal']):
            raise ValueError('checkpoint fork journal is not an untouched continuation: '+theory)
        first = read_artifact(commits[0])
        origin_key = 'sealed_reuse' if len(commits)==2 else 'continuation'
        origin = first.get('evidence',{}).get(origin_key)
        if (not isinstance(origin,dict) or not origin.get('commits')
                or digest(origin.get('run')) != digest(source_run)
                or origin['commits'][-1]['commit_id'] != selection.get('commit_id')):
            raise ValueError('initial continuation evidence differs from selected source checkpoint')
        state = snapshot['state']
        origin_state = origin['commits'][-1]['state']
        for field in ('proofs','accepted_order','rounds','revisions','batch_requests'):
            if state.get(field) != origin_state.get(field):
                raise ValueError('inherited proof or cost field changed: '+field)
        requests = state.get('batch_requests',0)
        if type(requests) is not int or requests < 0:
            raise ValueError('invalid inherited request count')
        allowed_artifacts = selection.get('artifacts',{})
        for path in (directory/'batch-requests').glob('**/*'):
            if not path.is_file():
                continue
            relative = str(path.relative_to(directory))
            if relative not in allowed_artifacts:
                raise ValueError('request artifact is not in the selected checkpoint allowlist')
            number = path.relative_to(directory).parts[1]
            if not number.isdigit() or not 0 < int(number) <= requests:
                raise ValueError('request artifact postdates the selected checkpoint')
        draft = directory/'document/draft.thy'
        if draft.exists():
            _file(draft,'checkpoint draft')
            expected = selection.get('draft_version') or allowed_artifacts.get('document/draft.thy')
            if not expected or _sha256(draft) != expected:
                raise ValueError('draft differs from frozen checkpoint source')
        elif selection.get('draft_version') or 'document/draft.thy' in allowed_artifacts:
            raise ValueError('selected checkpoint draft is missing')
        states[theory] = state
        fixed = {row['contract_id'] for row in package['contracts']}
        inheritance[theory] = {
            'source_commit_id':selection['commit_id'],
            'fork_commit_id':snapshot['commit_id'], 'state_sha256':digest(state),
            'proofs_sha256':digest(state['proofs']),
            'accepted_order_sha256':digest(state['accepted_order']),
            'inherited_contract_green':len(fixed & set(state['proofs'])),
            'inherited_helper_or_theorem_green':len(set(state['proofs'])-fixed),
            'inherited_requests':requests,'inherited_rounds':deepcopy(state.get('rounds',{})),
            'inherited_revisions':deepcopy(state.get('revisions',{})),
            'journal_files':{str(path.relative_to(root)):_sha256(path) for path in commits},
            'draft_sha256':_sha256(draft) if draft.exists() else None}
    declared = {Path(overview.shared._package(name)['file_slot']).stem for name in selected}
    for directory in (root/'theories').iterdir():
        if directory.is_dir() and directory.name not in declared:
            if any(directory.rglob('*')):
                raise ValueError('fork contains theory state outside selected checkpoint')
    return inheritance,states


def initialize_checkpoint_trial_budget(root, overview, binding, *, target_contract_id,
                                      authorization, seconds=900, checkpoint_path=None,
                                      rows=None):
    """Create one closed incremental window, then return a provenance receipt.

    ``binding`` must be exactly the workspace_policy the ordinary runner will
    compute; it will independently enforce its hash on launch. ``overview`` is
    that application's normal WorkspaceController. Only one target theory is
    admitted, with at most one hour of *new* diagnostic generation. No original
    elapsed time is assigned a fabricated zero or subtracted. A second call is
    rejected rather than resetting/replacing a window.
    """
    from regen.execution.proof_session_runner import acquire_run_mutation_lock,release_run_mutation_lock
    from regen.infrastructure.effective_time_budget import EffectiveTimeBudget,POLICY
    from regen.infrastructure.experiment_ledger import Ledger

    if not isinstance(authorization,str) or not authorization.strip():
        raise ValueError('explicit checkpoint diagnostic authorization required')
    if (type(seconds) not in (int,float) or not math.isfinite(seconds)
            or not 0 < seconds <= MAX_TRIAL_SECONDS):
        raise ValueError('diagnostic window must be positive and at most 3600 seconds')
    root = Path(root).resolve()
    if not root.is_dir() or Path(overview.root).resolve() != root:
        raise ValueError('overview must belong to the existing independent checkpoint fork')
    token = acquire_run_mutation_lock(root,operation='initialize-checkpoint-diagnostic-budget')
    try:
        _unused(root)
        run_path = _file(root/'run.json','fork run')
        run = read_artifact(run_path)
        if run != overview.run:
            raise ValueError('overview run differs from frozen fork run')
        own_checkpoint_path = _file(root/'checkpoint.json','fork checkpoint')
        manifest_path = _file(checkpoint_path or own_checkpoint_path,'authorized checkpoint')
        checkpoint = read_artifact(own_checkpoint_path)
        if digest(checkpoint) != digest(read_artifact(manifest_path)):
            raise ValueError('fork checkpoint differs from authorized checkpoint')
        if not isinstance(checkpoint.get('theories'),dict) or not checkpoint['theories']:
            raise ValueError('explicit selected checkpoint theories required')
        source = Path(checkpoint['source_run']).resolve()
        if source == root or source in root.parents or root in source.parents:
            raise ValueError('diagnostic fork must be independent of the source run tree')
        source_path = _file(source/'run.json','source run')
        source_run = read_artifact(source_path)
        if source_run['run_id'] == run['run_id']:
            raise ValueError('diagnostic fork requires a new run identity')
        origin = read_artifact(_file(root/'workspace-continuation.json','workspace continuation'))
        if Path(origin['source_run']).resolve() != source:
            raise ValueError('continuation source differs from checkpoint source')
        carried = {row['theory']:row['source_commit'] for row in origin.get('theories',[])}
        sealed = set(origin.get('sealed_theories',[]))
        if set(carried)|sealed != set(checkpoint['theories']):
            raise ValueError('continuation does not match exact checkpoint theory set')
        if any(checkpoint['theories'][name]['commit_id']!=commit for name,commit in carried.items()):
            raise ValueError('continuation selected source commit differs')
        inheritance,states = _inheritance(root,overview,checkpoint,source_run)
        selected = list(overview.selected)
        if len(selected)!=1 or selected!=binding.get('selected_theories'):
            raise ValueError('diagnostic window requires one explicitly selected theory')
        theory = selected[0]
        if theory not in states or states[theory]['status'] in {'sealed','retired'}:
            raise ValueError('diagnostic target must be an unfinished inherited theory')
        target_package = overview.shared._package(theory)
        fixed = {row['contract_id'] for row in target_package['contracts']}
        if target_contract_id not in fixed or target_contract_id in states[theory]['proofs']:
            raise ValueError('diagnostic target must be a fixed unproved contract at the checkpoint')
        rows = overview.list_workspace()['theories'] if rows is None else rows
        theories = {row['theory']:[c['id'] if isinstance(c,dict) else c for c in row['contracts']]
                    for row in rows}
        if (len(theories)!=len(rows) or theory not in theories
                or set(theories)!=set(overview.shared.entries)):
            raise ValueError('invalid diagnostic contract allocation')
        baseline = set()
        for name,ids in theories.items():
            package = overview.shared._package(name)
            if ids != [c['contract_id'] for c in package['contracts']]:
                raise ValueError('clock contract allocation differs from frozen package')
            if name in states:
                baseline.update(set(ids)&set(states[name]['proofs']))
        owners = [uid for ids in theories.values() for uid in ids]
        if len(owners)!=len(set(owners)):
            raise ValueError('duplicate contract in diagnostic allocation')
        identity = {'policy':POLICY,'run_sha256':digest(run),'contracts_sha256':digest(theories),
                    'configuration_sha256':digest(binding)}
        receipt = {'schema':SCHEMA,'scope':'incremental_checkpoint_diagnostic_only',
            'not_complete_experiment_cost':True,'historical_costs_reset':False,
            'historical_time_treatment':'preserved in source; not measured or imported by this new incremental window',
            'source_run':str(source),'source_run_sha256':digest(source_run),
            'run_id':run['run_id'],'run_sha256':digest(run),
            'checkpoint_path':str(manifest_path.resolve()),'checkpoint_sha256':digest(checkpoint),
            'checkpoint_file_sha256':_sha256(manifest_path),
            'workspace_policy':deepcopy(binding),'workspace_policy_sha256':digest(binding),
            'authorization':authorization,'seconds':float(seconds),
            'target_theory':theory,'target_contract_id':target_contract_id,
            'inheritance':inheritance,
            'liveness':'exclusive normal runner mutation lock; no launch/session/edit artifacts',
            'runner_requirement':'normal run_parallel_workspace with identical policy and explicit resume_authorization'}
        ledger = Ledger(root/'effective-generation-budget',run['run_id'])
        ledger.append('parallel_budget_binding',**identity)
        evidence = ledger.append('checkpoint_diagnostic_window_authorized',**receipt)
        stage = EffectiveTimeBudget(ledger,seconds=seconds)
        try:
            stage.register(theories,baseline_green=baseline)
            stage.cancel('checkpoint_diagnostic_window_prepared')
        finally:
            stage.close()
        # A clock write must not change proof authority or reinterpret source
        # work. Check unchanged logical states/files before returning the grant.
        for item in inheritance.values():
            for relative,expected in item['journal_files'].items():
                if _sha256(root/relative)!=expected:
                    raise ValueError('accepted checkpoint changed while preparing diagnostic clock')
        if read_artifact(run_path)!=run or read_artifact(source_path)!=source_run:
            raise ValueError('run binding changed while preparing diagnostic clock')
        completed = ledger.append('checkpoint_diagnostic_window_prepared',
            authorization_event_id=evidence['event_id'],authorization_sha256=digest(receipt),
            initial_incremental_effective_seconds=0,gate_open=False,source_costs_modified=False)
        return {**receipt,'authorization_event_id':evidence['event_id'],
                'prepared_event_id':completed['event_id'],'status':'prepared_closed',
                'requires_resume_authorization':True}
    finally:
        release_run_mutation_lock(token)
