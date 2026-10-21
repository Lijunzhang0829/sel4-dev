"""Independent whole-repository gate over exact, journal-verified theory seals."""
import hashlib
from pathlib import Path
from uuid import uuid4

from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.execution.check_tasks import CheckCancelled, acceptance_guard
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.planning.import_contract import required_imports
from regen.execution.checker_policy import resolve_complete_context_policy, policy_binding


def verify_environment(run):
    # The operator guarantees stable environment contents throughout the run.
    context_id = None
    if run.get('execution_input_kind') == 'refine-dataset':
        from regen.preparation.checker_context import load_context
        context = load_context(Path(run['checker_context_root']))
        context_id = context['checker_context_id']
        if context_id != run['checker_context_id']:
            raise ValueError('integration checker context differs from frozen run')
        for key, asset in [('isabelle_dir','isabelle'),('l4v','l4v'),('semantic_workspace','semantic_workspace')]:
            if Path(run[key]).resolve() != Path(context[asset]).resolve():
                raise ValueError('integration asset path differs from checker context: '+key)
        for key in ('image','base_session','base_import','base_catalog'):
            if run[key] != context[key]:
                raise ValueError('integration configuration differs from checker context: '+key)
    return {'environment_policy':'operator-managed','checker_context_id':context_id,
            'environment_sha256':digest({key:run.get(key) for key in
                ('image','l4v','semantic_workspace','base_session','base_import','base_catalog')})}


def expected_sealed_source(run, graph, proofs, imports, generated):
    renderer = object.__new__(IsabelleChecker)
    renderer.complete_context_policy = resolve_complete_context_policy(run)
    renderer.imports = lambda: ['Generated_Refine.'+name for name in imports] if imports else [run['base_import']]
    return renderer.complete_source(graph, proofs, theory_name=generated)


def integration_manifest(run_dir):
    workspace = SharedProofWorkspace(run_dir)
    run = workspace.run
    repo = Path(__file__).resolve().parents[2]
    if any(hashlib.sha256((repo/path).read_bytes()).hexdigest()!=sha for path,sha in run['implementation'].items()):
        raise ValueError('integration implementation differs from run; create a new run')
    theories, contracts, sources = [], [], {}
    for name in workspace.entries:
        package = workspace._package(name)
        snapshot = workspace._snapshot(name)
        if snapshot is None:
            raise ValueError('theory has no accepted seal: '+name)
        state = snapshot['state']
        if state['status']=='retired' and not package['contracts']:
            continue
        if state['status']!='sealed':
            raise ValueError('every fixed contract must belong to a sealed theory: '+name)
        nodes = {node['id']:node for node in state['graph']['nodes']}
        expected = {contract['contract_id'] for contract in package['contracts']}
        actual = {key for key,node in nodes.items() if node['role']=='contract'}
        if expected != actual or not expected <= state['proofs'].keys() or set(state['proofs'])!=set(nodes):
            raise ValueError('integration contract/active-node coverage mismatch: '+name)
        # _snapshot replays the graph and seal validators, including exact statements,
        # contexts, provider bindings and embedded audit/build evidence.
        directory = snapshot['directory']
        records = sorted((Path(directory)/'acceptance/commits').glob('*.json'))
        import json
        from regen.infrastructure.incremental_log import read_artifact
        record = read_artifact(records[-1])
        if record['commit_id'] != snapshot['commit_id'] or record['event']!='seal':
            raise ValueError('integration snapshot is not the exact terminal seal')
        evidence = record['evidence']
        source = evidence['source_text']
        if digest(source)!=evidence['source_hash']:
            raise ValueError('sealed source identity mismatch')
        generated = generated_checkpoint_theory(package['file_slot'])
        if generated in sources:
            raise ValueError('duplicate generated theory name')
        imports_path = Path(directory)/'imports.json'
        imports = json.loads(imports_path.read_text()) if imports_path.exists() else []
        planned = [generated_checkpoint_theory(workspace.entries[row['theory']]['file_slot'])
                   for row in required_imports(package['repository_projection'])]
        if package['repository_projection'].get('schema_version',0)>=5 and imports!=planned:
            raise ValueError('actual imports differ from the frozen plan')
        expected_source = expected_sealed_source(run, state['graph'], state['proofs'], imports, generated)
        if source != expected_source:
            raise ValueError('sealed source does not render the accepted declarations/proofs')

        published = Path(run_dir)/'generated'/(generated+'.thy')
        if not published.is_file() or published.read_text()!=source:
            raise ValueError('published source differs from accepted seal: '+name)
        sources[generated] = source
        contracts.extend(sorted(expected))
        theories.append({'theory':name,'generated_theory':generated,'commit_id':snapshot['commit_id'],
            'seal_id':state['seal_id'],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'contract_ids':sorted(expected),'required_imports':[row['theory'] for row in required_imports(package['repository_projection'])],
            'seal_evidence_sha256':digest(evidence)})
    environment = verify_environment(run)
    fixed = workspace.fixed_contract_ids
    if len(set(fixed))!=len(fixed) or len(set(contracts))!=len(contracts) or set(contracts)!=set(fixed):
        raise ValueError('whole-repository exact-once contract coverage failed')
    names = {row['theory'] for row in theories}
    if any(not set(row['required_imports'])<=names for row in theories):
        raise ValueError('retired/missing provider in integration imports')
    pending = {row['theory']:set(row['required_imports']) for row in theories}
    ordered = []
    while pending:
        ready = [name for name,providers in pending.items() if providers<=set(ordered)]
        if not ready:
            raise ValueError('cyclic integration imports')
        for name in ready:
            ordered.append(name); del pending[name]
    by_name = {row['theory']:row for row in theories}
    manifest = {'schema_version':2,'run_id':run['run_id'],'environment':environment, **policy_binding(run),
        'file_list_sha256':run['session_schedule']['file_list_sha256'],
        'implementation_sha256':digest(run['implementation']),
        'contracts':sorted(contracts),'theories':[by_name[name] for name in ordered]}
    return run,manifest,sources


def _integrate_repository(run_dir, *, timeout=900):
    root = Path(run_dir).resolve()
    output = root/'integration'/uuid4().hex
    output.mkdir(parents=True)
    try:
        run,manifest,sources = integration_manifest(root)
        durable(output/'manifest.json',manifest,immutable=True)
        generated, heaps = output/'generated', output/'heaps'
        generated.mkdir()
        # Copy only the trusted run-parent heap tree; force a fresh generated build
        # in an isolated output tree, never load a prior generated heap as evidence.
        from regen.infrastructure.resource_budget import clone_heaps
        durable(output/'heap-copy-budget.json', clone_heaps(root/'heaps',heaps))
        for path in list(heaps.rglob('Generated_Refine*')):
            if path.is_file():
                path.unlink()
        for name,source in sources.items():
            (generated/(name+'.thy')).write_text(source)
        checker = IsabelleChecker(root,output/'isabelle',timeout=timeout,
            image=run['image'],isabelle_dir=Path(run['isabelle_dir']),
            heaps_dir=heaps,generated_dir=generated,l4v_dir=Path(run['l4v']),
            semantic_workspace=Path(run['semantic_workspace']),base_session=run['base_session'],
            base_import=run['base_import'],base_catalog=run['base_catalog'])
        try:
            build = freeze_evidence(checker.build_generated_session([row['generated_theory'] for row in manifest['theories']]))
        finally:
            checker.close_prefix()
        # Re-read authoritative seals after the build, not mutable status summaries.
        _,after,after_sources = integration_manifest(root)
        unchanged = after==manifest and after_sources==sources
        result = {'repository_verified':build.get('verdict')=='green' and build.get('returncode')==0 and unchanged,
            'manifest_sha256':digest(manifest),'contract_count':len(manifest['contracts']),
            'theory_count':len(manifest['theories']),'unchanged_inputs':unchanged,
            'build':build,'artifact_dir':str(output)}
    except CheckCancelled:
        raise
    except Exception as error:
        result = {'repository_verified':False,'error_type':type(error).__name__,
                  'diagnostic':str(error),'artifact_dir':str(output)}
    with acceptance_guard():
        durable(output/'result.json',result,immutable=True)
        durable(root/'repository-verification.json',{'artifact':str(output/'result.json'),
                'result_sha256':digest(result),'repository_verified':result['repository_verified']})
    return result


def current_verification(run_dir):
    """A receipt is success only while the exact sealed manifest still matches."""
    import json
    root = Path(run_dir).resolve()
    pointer = root/'repository-verification.json'
    if not pointer.is_file():
        return False
    try:
        reference = json.loads(pointer.read_text())
        artifact = Path(reference['artifact']).resolve()
        artifact.relative_to(root/'integration')
        result = json.loads(artifact.read_text())
        if digest(result)!=reference['result_sha256'] or not result.get('repository_verified'):
            return False
        _,manifest,_ = integration_manifest(root)
        return digest(manifest)==result['manifest_sha256']
    except (OSError, ValueError, KeyError, TypeError):
        return False


def integrate_repository(run_dir, *, timeout=900):
    """Synchronous CLI compatibility over the same durable native task service."""
    import json
    from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
    from regen.execution.check_tasks import TERMINAL
    root = Path(run_dir).resolve()
    run = json.loads((root/'run.json').read_text())
    resources = CURRENT_RESOURCES.get()
    owned = resources is None
    resources = resources or NativeResources(1,4,1)
    marker = CURRENT_RESOURCES.set(resources)
    executor = None
    try:
        executor = CheckExecutor(root/'integration-tasks',workers=1)
        row = executor.submit(kind='integration',theory='repository',
            binding={'run_sha256':digest(run)},request={'timeout':timeout,**resources.policy},
            execute=lambda _: _integrate_repository(root,timeout=timeout))
        while row['status'] not in TERMINAL:
            row = executor.wait([row['task_id']],timeout=30)[0]
        if row['status'] != 'completed':
            return {'repository_verified':False,'task_id':row['task_id'],
                    'status':row['status'],'diagnostic':row.get('diagnostic','')}
        return {**row['result'],'task_id':row['task_id']}
    finally:
        try:
            if executor is not None:
                executor.close()
            if owned:
                resources.close()
        finally:
            CURRENT_RESOURCES.reset(marker)
