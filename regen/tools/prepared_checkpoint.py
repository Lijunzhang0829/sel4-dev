"""Pinned immutable checkpoint fixtures; independent copies retain journal lineage.

Preparation invokes the normal full-chain continuation validator. A derived arm
retains that journal's run_id, but has a distinct instance identity and writable
files. This is NOT a persistent validator cache: normal runner startup still
validates the journal. Manifest hashes must be pinned outside the fixture.
"""
import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

from regen.execution.proof_commits import durable

SCHEMA = 'prepared-checkpoint-v1'
RELOCATABLE = ('contract_package_dir', 'session_progress_ledger')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def application_binding():
    root = Path(__file__).resolve().parents[2]
    return {str(p.relative_to(root)): sha(p) for p in sorted((root/'regen').rglob('*'))
            if p.is_file() and p.suffix in {'.py','.ML','.sh','.json'}
            and '__pycache__' not in p.parts}


def inventory(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('fixture contains symlink: '+str(path))
        if path.is_file():
            result[str(path.relative_to(root))] = {'sha256':sha(path),'bytes':path.stat().st_size}
        elif not path.is_dir():
            raise ValueError('fixture contains special file: '+str(path))
    return result


def external_binding(run):
    """Bind immutable native sources and frozen input files, not their names alone."""
    from regen.infrastructure.path_identity import source_identity
    files = {}
    # Model configuration belongs to the new application/run binding, whereas
    # these proof-context assets must remain identical across continuation.
    for key in ('premise_database','premise_snapshot'):
        if run.get(key):
            path=Path(run[key]).resolve()
            files[str(path)]=sha(path)
    if run.get('checker_context_root'):
        root=Path(run['checker_context_root'])
        for name in ('checker-context.json','repository-planning-input.json','premise-audit-context.json'):
            path=(root/name).resolve()
            files[str(path)]=sha(path)
    if run.get('session_schedule',{}).get('source'):
        path=Path(run['session_schedule']['source']).resolve();files[str(path)]=sha(path)
    trees={str(Path(run[key]).resolve()):source_identity(Path(run[key]).resolve(),allow_links=True)
           for key in ('l4v','semantic_workspace','isabelle_dir') if run.get(key)}
    return {'files':files,'source_trees':trees}


def readonly(root):
    for path in sorted(Path(root).rglob('*'), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    Path(root).chmod(0o555)


@contextmanager
def measured_subphases(result):
    """Inclusive diagnostic timings; nested phases overlap and must not be summed."""
    names = {'node':'read_verify_object','decode':'decode',
             'continuation_state':'verify_chain','_carry_workspace_continuation':'publish',
             'copy2':'copy','copyfile':'copy_file','progress':'projection'}
    active = {}
    prior = sys.getprofile()
    def profile(frame, event, arg):
        if '/regen/' not in frame.f_code.co_filename and frame.f_code.co_name not in {'copy2','copyfile'}:
            return
        name = names.get(frame.f_code.co_name)
        if name is None:
            return
        key = id(frame)
        if event == 'call':
            active[key] = time.monotonic()
        elif event == 'return' and key in active:
            row = result.setdefault(name, {'seconds':0.0,'calls':0})
            row['seconds'] += time.monotonic()-active.pop(key)
            row['calls'] += 1
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(prior)


def prepare(source, checkpoint_path, fixture, *, profile_subphases=False):
    """Validate one historical checkpoint using the existing full fork, then freeze."""
    overall_started = time.monotonic()
    from regen.execution.repository_execution import fork_workspace_continuation
    source, checkpoint_path, fixture = map(lambda p:Path(p).resolve(),
                                          (source, checkpoint_path, fixture))
    if fixture == source or source in fixture.parents:
        raise ValueError('fixture must be outside original run')
    checkpoint_sha256 = sha(checkpoint_path)
    source_sha256 = sha(source/'run.json')
    checkpoint = json.loads(checkpoint_path.read_text())
    external = external_binding(json.loads((source/'run.json').read_text()))
    if checkpoint.get('source_run') != str(source):
        raise ValueError('checkpoint source mismatch')
    app = application_binding()
    fixture.mkdir(parents=True, exist_ok=False)
    run_dir = fixture/'run'
    timing = {'inclusive_subphases':{}, 'profile_subphases':profile_subphases,
              'notice':'Optional profiled subphases overlap and add overhead; do not compare profiled wall to unprofiled wall.',
              'input_read_and_binding_seconds':time.monotonic()-overall_started}
    started = time.monotonic()
    with (measured_subphases(timing['inclusive_subphases']) if profile_subphases else nullcontext()):
        run = fork_workspace_continuation(source, run_dir, checkpoint=checkpoint)
    timing['full_validated_fork_seconds'] = time.monotonic()-started
    if sha(checkpoint_path)!=checkpoint_sha256 or sha(source/'run.json')!=source_sha256:
        raise ValueError('checkpoint/source identity changed while preparing')
    if application_binding() != app:
        raise ValueError('application changed while preparing checkpoint')
    if external_binding(run) != external:
        raise ValueError('external proof context changed while preparing checkpoint')
    snapshot_started = time.monotonic()
    files = inventory(run_dir)
    timing['inventory_hash_seconds'] = time.monotonic()-snapshot_started
    timing['prepare_before_manifest_seconds'] = time.monotonic()-overall_started
    manifest = {'schema':SCHEMA,'source_run':str(source),'source_run_sha256':source_sha256,
        'checkpoint':checkpoint,'checkpoint_sha256':checkpoint_sha256,
        'application':app,'external_context':external,'lineage_run_id':run['run_id'],'files':files,
        'run_binding':{k:v for k,v in run.items() if k not in RELOCATABLE},
        'prepared_root':str(run_dir),'timing':timing,
        'scope':'Independent arm copies; journal lineage retained; cold startup validation is not bypassed.',
        'model_calls':0,'native_calls':0}
    durable(fixture/'manifest.json',manifest,immutable=True)
    readonly(fixture)
    return {'manifest_sha256':sha(fixture/'manifest.json'),'fixture':str(fixture),'timing':timing}


def prepare_existing_pristine(run_root, provenance_sha256, fixture):
    """Freeze an already-created target-pristine run as an arm fixture.

    The target-pristine journal is the semantic authority.  This manifest is
    only the copy/application/external-context authority; it pins, but never
    replaces, the pristine provenance and complete ProofCommits replay.
    """
    from regen.execution.proof_commits import digest
    from regen.tools.target_pristine_checkpoint import verify as verify_pristine

    started = time.monotonic()
    run_root, fixture = Path(run_root).resolve(), Path(fixture).resolve()
    if (fixture == run_root or fixture in run_root.parents
            or run_root in fixture.parents):
        raise ValueError('prepared fixture must be independent of pristine run')
    if fixture.exists() or fixture.is_symlink():
        raise FileExistsError('prepared fixture already exists')

    provenance = verify_pristine(run_root, provenance_sha256)
    source_run_path = run_root/'run.json'
    source_run_sha256 = sha(source_run_path)
    source_run = json.loads(source_run_path.read_text())
    source_files = inventory(run_root)
    source_inventory_sha256 = digest(source_files)
    app = application_binding()
    external = external_binding(source_run)
    fixture_created = False
    try:
        fixture.mkdir(parents=True, exist_ok=False)
        fixture_created = True
        prepared_root = fixture/'run'
        shutil.copytree(run_root, prepared_root, copy_function=shutil.copyfile)
        copied = inventory(prepared_root)
        if copied != source_files:
            raise ValueError('target-pristine copy differs before relocation')
        for relative in source_files:
            before, after = (run_root/relative).stat(), (prepared_root/relative).stat()
            if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
                raise ValueError('mutable hardlink to target-pristine source')
        if (inventory(run_root) != source_files
                or sha(source_run_path) != source_run_sha256):
            raise ValueError('target-pristine source changed during fixture copy')

        prepared_run = json.loads((prepared_root/'run.json').read_text())
        if prepared_run != source_run:
            raise ValueError('copied target-pristine runtime changed before relocation')
        prepared_run.update(
            contract_package_dir=str(prepared_root/'packages'),
            session_progress_ledger=str(prepared_root/'session-progress-ledger.json'))
        durable(prepared_root/'run.json', prepared_run)
        run_binding = {key:value for key,value in prepared_run.items()
                       if key not in RELOCATABLE}
        relocation = {
            'source_run': str(run_root),
            'source_run_sha256': source_run_sha256,
            'run_binding': run_binding,
        }
        # Replay again after the only permitted byte transformation.  This is
        # the same repository validator over the copied provider/target chains.
        verify_pristine(prepared_root, provenance_sha256,
                        prepared_relocation=relocation)
        if external_binding(prepared_run) != external:
            raise ValueError('target-pristine external context changed during copy')
        if application_binding() != app:
            raise ValueError('application changed while freezing target-pristine run')
        files = inventory(prepared_root)
        timing = {'total_seconds': time.monotonic()-started,
                  'notice': 'No model/native calls; full journal replay and byte inventory only.'}
        semantic_receipt = {key:provenance[key] for key in (
            'schema', 'theory', 'target_contract_id', 'target_package_sha256',
            'derived_imports', 'reused_sealed_providers', 'initial_commit_id',
            'initial_state_sha256', 'model', 'reasoning_effort',
            'execution_models', 'accepted_proofs', 'model_calls')}
        manifest = {
            'schema': SCHEMA,
            'source_kind': 'target-pristine-existing-v1',
            'source_run': str(run_root),
            'source_run_sha256': source_run_sha256,
            'source_inventory_sha256': source_inventory_sha256,
            'pristine_provenance_sha256': provenance_sha256,
            'pristine_semantic_receipt': semantic_receipt,
            'application': app,
            'external_context': external,
            'lineage_run_id': prepared_run['run_id'],
            'files': files,
            'run_binding': run_binding,
            'prepared_root': str(prepared_root),
            'timing': timing,
            'scope': ('Independent arm copies of a replayed target-pristine run; '
                      'journal lineage retained; cold validation is not bypassed.'),
            'model_calls': 0,
            'native_calls': 0,
        }
        durable(fixture/'manifest.json', manifest, immutable=True)
        readonly(fixture)
        return {'manifest_sha256': sha(fixture/'manifest.json'),
                'fixture': str(fixture), 'timing': timing}
    except BaseException:
        if fixture_created and fixture.exists() and not fixture.is_symlink():
            shutil.rmtree(fixture)
        raise


def verify(fixture, expected_manifest_sha256, *, check_application=True):
    fixture = Path(fixture).resolve()
    if not isinstance(expected_manifest_sha256,str) or len(expected_manifest_sha256)!=64:
        raise ValueError('external pinned manifest SHA256 required')
    if sha(fixture/'manifest.json') != expected_manifest_sha256:
        raise ValueError('fixture manifest mismatch')
    manifest = json.loads((fixture/'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA:
        raise ValueError('unsupported prepared checkpoint')
    if check_application and application_binding() != manifest['application']:
        raise ValueError('prepared checkpoint application changed')
    if inventory(fixture/'run') != manifest['files']:
        raise ValueError('prepared checkpoint content mismatch')
    run = json.loads((fixture/'run/run.json').read_text())
    if {k:v for k,v in run.items() if k not in RELOCATABLE} != manifest['run_binding']:
        raise ValueError('prepared checkpoint runtime binding changed')
    if external_binding(run) != manifest['external_context']:
        raise ValueError('prepared checkpoint external context changed')
    if manifest.get('source_kind') == 'target-pristine-existing-v1':
        from regen.tools.target_pristine_checkpoint import verify as verify_pristine
        provenance_path = fixture/'run'/'target-pristine.json'
        if sha(provenance_path) != manifest.get('pristine_provenance_sha256'):
            raise ValueError('prepared target-pristine provenance changed')
        provenance = verify_pristine(
            fixture/'run', manifest['pristine_provenance_sha256'],
            prepared_relocation={
                'source_run': manifest['source_run'],
                'source_run_sha256': manifest['source_run_sha256'],
                'run_binding': manifest['run_binding'],
            })
        expected = {key:provenance[key] for key in (
            'schema', 'theory', 'target_contract_id', 'target_package_sha256',
            'derived_imports', 'reused_sealed_providers', 'initial_commit_id',
            'initial_state_sha256', 'model', 'reasoning_effort',
            'execution_models', 'accepted_proofs', 'model_calls')}
        if expected != manifest.get('pristine_semantic_receipt'):
            raise ValueError('prepared target-pristine semantic receipt changed')
    return manifest


def derive(fixture, destination, *, expected_manifest_sha256):
    """Copy pinned fixture; never share mutable files or silently rebind journals."""
    fixture, destination = Path(fixture).resolve(), Path(destination).resolve()
    if destination == fixture or fixture in destination.parents:
        raise ValueError('arm must be outside immutable fixture')
    started = time.monotonic()
    manifest = verify(fixture,expected_manifest_sha256)
    verified = time.monotonic()
    shutil.copytree(fixture/'run',destination,copy_function=shutil.copyfile)
    # copyfile deliberately creates fresh inodes. Directories need writable
    # permissions even though the source fixture is sealed read-only.
    for path in [destination,*destination.rglob('*')]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    copied = time.monotonic()
    if inventory(destination) != manifest['files']:
        raise ValueError('derived copy failed content verification')
    for relative in manifest['files']:
        before, after = (fixture/'run'/relative).stat(), (destination/relative).stat()
        if (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino):
            raise ValueError('mutable hardlink to fixture')
    run = json.loads((destination/'run.json').read_text())
    old_root = Path(manifest['prepared_root'])
    expected = {'contract_package_dir':str(old_root/'packages'),
                'session_progress_ledger':str(old_root/'session-progress-ledger.json')}
    if any(run.get(k)!=v for k,v in expected.items()):
        raise ValueError('unexpected prepared runtime relocation')
    run.update(contract_package_dir=str(destination/'packages'),
               session_progress_ledger=str(destination/'session-progress-ledger.json'))
    durable(destination/'run.json',run)
    if manifest.get('source_kind') == 'target-pristine-existing-v1':
        from regen.tools.target_pristine_checkpoint import verify as verify_pristine
        verify_pristine(destination, manifest['pristine_provenance_sha256'],
            prepared_relocation={
                'source_run': manifest['source_run'],
                'source_run_sha256': manifest['source_run_sha256'],
                'run_binding': manifest['run_binding'],
            })
    instance = {'schema':'prepared-checkpoint-arm-v1','instance_id':uuid4().hex,
        'lineage_run_id':run['run_id'],'fixture':str(fixture),
        'fixture_manifest_sha256':expected_manifest_sha256,
        'journal_binding_unchanged':True,'cold_start_validation_required':True,
        'timing':{'fixture_verify_seconds':verified-started,'copy_seconds':copied-verified,
                  'copy_verify_and_relocate_seconds':time.monotonic()-copied,
                  'total_seconds':time.monotonic()-started}}
    durable(destination/'arm-instance.json',instance,immutable=True)
    return instance


def recover_prelaunch_arm(source, destination, *, expected_initial_state_sha256,
                          authorization, wall_deadline_unix=None):
    """New physical instance after a pre-model startup fault; costs stay recorded.

    This narrow recovery refuses model/native/task history or changed journals.
    It preserves the lineage run ID and proof authority, relocates only the same
    two physical paths as derive(), and requires ordinary cold validation again.
    The caller retains the original case deadline across both physical instances.
    """
    from regen.execution.proof_commits import digest
    from regen.tools import parallel_workspace_service as service
    from regen.tools.proof_idle_watch import projection
    from regen.infrastructure.incremental_log import read_artifact
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if (not authorization or destination.exists() or destination.is_relative_to(source)
            or source.is_relative_to(destination)):
        raise ValueError('authorized new independent recovery destination required')
    if (source/'service-launch.json').exists() and service.status(source)['running']:
        raise ValueError('source service still running')
    for folder in ('parallel-sessions','check-tasks'):
        if (source/folder).exists():
            raise ValueError('prelaunch recovery refuses model/task history')
    for pattern in ('theories/*/batch-requests/*','theories/*/document/last-edit.json'):
        if next(source.glob(pattern),None) is not None:
            raise ValueError('prelaunch recovery refuses generated actions')
    budget=source/'effective-generation-budget/events'
    if budget.exists():
        rows=[read_artifact(p) for p in sorted(budget.glob('*.json'))]
        if any(r['kind'] in {'parallel_budget_invocation','response','model_work_observed',
                              'time_budget_worker_selected'} for r in rows):
            raise ValueError('prelaunch recovery refuses consumed generation history')
    continuation=read_artifact(source/'workspace-continuation.json')
    unfinished=continuation['theories']
    if len(unfinished)!=1:
        raise ValueError('prelaunch recovery requires one inherited unfinished theory')
    theory=unfinished[0]['theory']
    commits=sorted((source/'theories'/theory/'acceptance/commits').glob('*.json'))
    if len(commits)!=1 or projection(commits[0])[0]['event']!='initial':
        raise ValueError('prelaunch recovery refuses new accepted work')
    initial=read_artifact(commits[0])
    if digest(initial['state'])!=expected_initial_state_sha256:
        raise ValueError('inherited state differs from verified preparation')
    before={str(p.relative_to(source)):sha(p) for p in source.glob('theories/*/acceptance/commits/*.json')}
    skip={'effective-generation-budget','parallel-workspace-policy.json','diagnostic-window.json',
        'experiment.json','trial-stop.json','service-control.lock','service-launch.json',
        'service-invocation.json','service-status.json','service-runner.log','.runner-locks'}
    shutil.copytree(source,destination,copy_function=shutil.copyfile,
        ignore=lambda directory,names: [n for n in names if Path(directory)==source and n in skip])
    run=read_artifact(destination/'run.json')
    run.update(contract_package_dir=str(destination/'packages'),
               session_progress_ledger=str(destination/'session-progress-ledger.json'))
    durable(destination/'run.json',run)
    if any(sha(destination/relative)!=value or sha(source/relative)!=value for relative,value in before.items()):
        raise ValueError('recovery changed proof authority')
    receipt={'schema':'pre-model-startup-recovery-v1','instance_id':uuid4().hex,
        'source_instance':str(source),'lineage_run_id':run['run_id'],
        'authorization':authorization,'wall_deadline_unix':wall_deadline_unix,
        'initial_state_sha256':expected_initial_state_sha256,'model_calls_before_recovery':0,
        'accepted_journal_sha256':before,'historical_proof_counters_reset':False,
        'source_budget_and_failure_records_preserved':True,'cold_start_validation_required':True}
    durable(destination/'arm-instance.json',receipt,immutable=True)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('--source',required=True,type=Path)
    p.add_argument('--checkpoint',required=True,type=Path);p.add_argument('--fixture',required=True,type=Path)
    p.add_argument('--profile-subphases',action='store_true')
    p=sub.add_parser('derive');p.add_argument('--fixture',required=True,type=Path)
    p.add_argument('--destination',required=True,type=Path);p.add_argument('--manifest-sha256',required=True)
    p=sub.add_parser('prepare-existing-pristine');p.add_argument('--run-root',required=True,type=Path)
    p.add_argument('--provenance-sha256',required=True);p.add_argument('--fixture',required=True,type=Path)
    args=parser.parse_args()
    if args.action == 'prepare':
        result=prepare(args.source,args.checkpoint,args.fixture,
                       profile_subphases=args.profile_subphases)
    elif args.action == 'prepare-existing-pristine':
        result=prepare_existing_pristine(
            args.run_root,args.provenance_sha256,args.fixture)
    else:
        result=derive(args.fixture,args.destination,
                      expected_manifest_sha256=args.manifest_sha256)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
