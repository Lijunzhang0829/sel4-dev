"""Repository projection adapter and committed orchestration over existing proof backends.

Legacy schedule replay remains in proof_session_runner; no second proof graph is used.
"""
from __future__ import annotations
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import threading

from regen.execution.proof_commits import ProofCommits, digest, durable, freeze_evidence
from regen.execution.fact_context import manifest as fact_manifest
from regen.planning.proof_graph import (build_graph, validate_graph, assert_green_nodes_unchanged,
    executable_nodes, graph_payload_sha256, write_immutable_graph)


from regen.execution.retry_policy import required_context, extra_round_available


_PROJECTION_LOCK = threading.RLock()


def node_retry_guidance(state, node):
    binding = state.get('retry_guidance_binding', {})
    if binding != {'node_id': node['id'], 'statement': node['statement'], 'context': node.get('context', [])}:
        return ''
    decision = state.get('retry_guidance', {})
    return '\n'.join(str(decision.get(k, '')) for k in ('retrieval_query', 'repair_guidance'))


def resume_round_feedback(theory_dir, state, node_id):
    """Recover checker-bound feedback, without granting proof or refunding costs."""
    from regen.execution.proof_session_runner import recover_latest_attempt_feedback, select_resume_feedback
    current = state.get('last_failure')
    if not current or current.get('node_id') != node_id:
        current = None
    recovered = recover_latest_attempt_feedback(theory_dir, state['graph'], node_id)
    if recovered and recovered['round_id'] > state['rounds'].get(node_id, 0):
        recovered = None  # Uncharged candidate directories are not accepted history.
    return select_resume_feedback(current, recovered)


def read(path): return json.loads(Path(path).read_text())


def is_repository(run_dir):
    path = run_dir/'run.json'
    return path.is_file() and read(path).get('execution_mode') == 'repository-projection-v1'


def projection_package(projection, plan):
    if projection['schema_version']>=3:
        targets={c['contract_id']:c for t in plan['theories'] for c in t['contracts']}
        intents=projection['auxiliary_intents']
        providers=projection['suggested_providers']
    else:
        targets={n['contract_id']:n['contract'] for n in plan['nodes'] if n['role']=='contract'}
        intents=[n for n in projection['nodes'] if n['role']=='auxiliary']
        ids={dep for n in projection['nodes'] for dep in n['suggested_dependencies']}
        providers=[n for n in plan['nodes'] if n['id'] in ids]
    support=sorted({c for n in intents for c in n['for_contracts']})
    body = {'theory':projection['theory'], 'file_slot':projection['file_slot'],
        'contracts':projection['contracts'], 'support_contracts':[targets[c] for c in support],
        'repository_projection':{**projection,
            'suggested_providers':providers},
        'plan_id':plan['plan_id'], 'projection_sha256':digest(projection)}
    return {**body, 'package_payload_sha256':digest(body)}


def init_run(run_dir, input_root, run_id, schedule):
    from regen.planning.repository_planner import load_file_projection
    input_root, schedule = input_root.resolve(), schedule.resolve()
    listing = read(schedule)
    if not listing.get('files'): raise ValueError('empty repository file list')
    direct = listing.get('schema_version') == 5
    if direct:
        from regen.planning.plan_revisions import verify_revisions
        revision = verify_revisions(schedule.parent/'planning')
        plan = read(schedule.parent/'repository-plan.json')
        if (
            revision['plan_id'] != plan.get('plan_id')
            or revision['revision'] != plan.get('revision')
            or revision['plan'] != {k:v for k,v in plan.items()
                if k not in {'plan_id','revision','revision_id','parent_revision_id'}}
            or listing.get('plan_id') != plan.get('plan_id')
            or listing.get('plan_revision') != plan.get('revision')
        ):
            raise ValueError('file list is not bound to the current plan revision')
        for item in listing['files']:
            projection = read(schedule.parent/item['projection'])
            if (projection.get('plan_id') != plan['plan_id']
                or projection.get('plan_revision') != plan['revision']
                or digest(projection) != item['projection_sha256']):
                raise ValueError('repository file projection drift')
    else:
        # Compatibility path for sealed environment/dataflow plans.
        load_file_projection(input_root, schedule.parent, listing['files'][0]['theory'])
        plan = read(schedule.parent/'repository-plan.json')
    if direct:
        from regen.preparation.checker_context import load_context
        checker_context = load_context(input_root)
        context = {
            'premise_database':checker_context['premise_database_path'],
            'premise_snapshot':checker_context['premise_audit_context_path'],
            'semantic_workspace':checker_context['semantic_workspace'],
            'heaps':checker_context['heaps'], 'l4v':checker_context['l4v'],
        }
        runtime = checker_context
    else:
        from regen.execution.proof_session_runner import environment_context
        context = environment_context(input_root)
        runtime = context['gate']['runtime']
    run_dir.mkdir(parents=True, exist_ok=True)
    packages = [projection_package(read(schedule.parent/f['projection']), plan) for f in listing['files']]
    files = [{'theory':p['theory'], 'file_slot':p['file_slot'], 'order':i, 'layer':i,
              'contract_ids':[c['contract_id'] for c in p['contracts']], 'status':'pending',
              'generation_ids':[], 'seal_id':None} for i,p in enumerate(packages)]
    if direct:
        isabelle_dir=checker_context['isabelle']; image=checker_context['image']
        isabelle_sha256=checker_context['isabelle_sha256']
        budgets=checker_context['budgets']
    else:
        recipe = read(input_root/'.preparation/recipe.json')
        toolchain = read(input_root/'Toolchain/identity.json')
        isabelle_dir=Path(recipe['repo'])/'verification/isabelle'
        image=toolchain['image']['image_id'];isabelle_sha256=toolchain['isabelle_sha256']
        budgets=read(input_root/'Environment-Freeze/campaign-manifest.json')['budgets']
    implementation = {str(p.relative_to(Path(__file__).resolve().parents[2])):
                      __import__('hashlib').sha256(p.read_bytes()).hexdigest()
                      for folder in ('execution','planning','infrastructure','analysis')
                      for p in (Path(__file__).resolve().parents[1]/folder).iterdir() if p.suffix in {'.py','.ML'}}
    from regen.infrastructure.audited_llm import model_settings, MODEL_CONFIG
    implementation[str(MODEL_CONFIG.relative_to(Path(__file__).resolve().parents[2]))] = __import__('hashlib').sha256(MODEL_CONFIG.read_bytes()).hexdigest()
    run = {'schema_version':2, 'execution_mode':'repository-projection-v1', 'run_id':run_id,
        'execution_input_kind':'refine-dataset' if direct else 'legacy-environment',
        **({'checker_context_root':str(input_root),
            'checker_context_id':checker_context['checker_context_id']} if direct
           else {'environment_run':str(input_root)}),
        'target':'Refine', 'state':'ready',
        'execution_models':model_settings()['role_models'],
        'checker_policy':'fixed-prefix-cold-green-v1',
        'premise_database':str(context['premise_database']), 'premise_snapshot':str(context['premise_snapshot']),
        'semantic_workspace':str(context['semantic_workspace']), 'l4v':str(context['l4v']),
        'base_session':runtime['base_session'], 'base_import':runtime['base_import'],
        'base_catalog':runtime['base_catalog'],
        'isabelle_dir':str(isabelle_dir), 'image':image,
        'isabelle_sha256':isabelle_sha256, 'budgets':budgets,
        'implementation':implementation,
        'contract_package_dir':str((run_dir/'packages').resolve()),
        'session_progress_ledger':str((run_dir/'session-progress-ledger.json').resolve()),
        'session_schedule':{
            'source':str(schedule), 'file_list_sha256':digest(listing),
            'plan_id':plan['plan_id'],
            **({'plan_revision':plan['revision']} if direct else {}),
            **({} if direct else {
                'campaign_id':plan['campaign_id'],
                'environment_gate_id':plan['environment_gate_id'],
                'environment_dataflow_id':plan['environment_dataflow_id'],
            })}}
    # Immutable run identity; re-init cannot reset accepted proofs or change code.
    durable(run_dir/'run.json', run, immutable=True)
    durable(run_dir/'execution-input.json', {'file_list':listing, 'files':files,
            'package_hashes':{p['file_slot']:p['package_payload_sha256'] for p in packages}}, immutable=True)
    for p in packages: durable(run_dir/'packages'/(p['file_slot']+'.json'), p, immutable=True)
    if not (run_dir/'heaps').exists(): shutil.copytree(context['heaps'], run_dir/'heaps')
    (run_dir/'generated').mkdir(exist_ok=True)
    progress(run_dir)
    return run


def package_for(run, slot):
    package = read(Path(run['contract_package_dir'])/(slot+'.json'))
    body = {k:v for k,v in package.items() if k!='package_payload_sha256'}
    expected = read(Path(run['contract_package_dir']).parent/'execution-input.json')['package_hashes'][slot]
    if digest(body)!=expected or package.get('package_payload_sha256')!=expected:
        raise ValueError('execution package drift')
    return package


def context_graph(graph, order):
    graph = deepcopy(graph)
    graph['context_policy'] = 'committed-prefix-v1'
    graph['accepted_order'] = list(order)
    graph['graph_payload_sha256'] = graph_payload_sha256(graph)
    return graph


def validator(package, runtime=None):
    def validate(old, new, event, evidence):
        def verify_embedded(value):
            if isinstance(value,list):
                for v in value: verify_embedded(v)
            if isinstance(value,dict):
                import hashlib
                for key in ('source','log'):
                    if key+'_content' in value and hashlib.sha256(value[key+'_content'].encode()).hexdigest()!=value.get(key+'_sha256'):
                        raise ValueError('checker evidence bytes mismatch')
                for v in value.values(): verify_embedded(v)
        verify_embedded(evidence)
        if event=='initial' and 'continuation' in evidence:
            validate_continuation(package, runtime, new, evidence['continuation'])
        if event in {'initial','revision','revoke','green'}:
            check=evidence.get('check',{})
            if (check.get('returncode')!=0 or not check.get('source_content')
                    or 'log_content' not in check):
                raise ValueError('accepted checker evidence is incomplete')
        graph = new['graph']; validate_graph(graph, package)
        if graph['context_policy'] != 'committed-prefix-v1': raise ValueError('context policy drift')
        governed = package['repository_projection'].get('schema_version',0)>=4
        if governed and graph.get('initial_graph_owner')!='File Planner':
            raise ValueError('initial graph must be owned by File Planner')
        if governed and graph.get('runtime_graph_owner')!='Graph Revision Agent':
            raise ValueError('runtime graph revisions must be owned by Graph Revision Agent')
        nodes = {n['id']:n for n in graph['nodes']}
        proofs = new['proofs']; order = new['accepted_order']
        if len(set(order))!=len(order) or set(order)!=set(proofs) or not set(proofs)<=nodes.keys():
            raise ValueError('invalid committed proof prefix')
        if graph['accepted_order']!=order: raise ValueError('graph prefix differs from accepted proofs')
        if old:
            if old['status'] in {'sealed','retired'}: raise ValueError('terminal theory is immutable')
            assert_green_nodes_unchanged(old['graph'], graph, set(old['proofs']))
            if order[:len(old['accepted_order'])]!=old['accepted_order'] or any(proofs.get(k)!=v for k,v in old['proofs'].items()):
                raise ValueError('GREEN proof/prefix changed')
            if old.get('runtime_policy') and new.get('runtime_policy')!=old['runtime_policy']:
                raise ValueError('committed runtime budget changed')
            for field in ('rounds','revisions'):
                if any(new[field].get(k,0)<v for k,v in old[field].items()):
                    raise ValueError('cumulative cost counters decreased')
        elif event!='initial': raise ValueError('first accepted event must be initial graph')
        if event in {'initial','revision','revoke'}:
            if any(n['statement'] not in evidence['check']['source_content'] for n in graph['nodes']):
                raise ValueError('graph evidence does not cover accepted declarations')
            if evidence.get('check',{}).get('verdict')!='green': raise ValueError('graph lacks validation')
            if old and (proofs!=old['proofs'] or order!=old['accepted_order']): raise ValueError('revision changes proof state')
            removed = set(n['id'] for n in old['graph']['nodes'])-nodes.keys() if old else set()
            if governed and removed:
                raise ValueError('schema-4 graph nodes are retained; record failure instead of deleting helpers')
            if removed and event!='revoke' and evidence.get('decision',{}).get('operation')!='DELETE':
                raise ValueError('node removal requires explicit revocation')
            if removed and not evidence.get('reason'): raise ValueError('revocation reason missing')
            decision = evidence.get('decision',{})
            if old and event=='revision' and decision.get('operation')=='SPLIT':
                key=evidence['node_id']; before=next(n for n in old['graph']['nodes'] if n['id']==key)
                if key not in nodes or any(nodes[key][f]!=before[f] for f in ('statement','context','name')):
                    raise ValueError('SPLIT lost/changed its parent')
        elif event=='green':
            key=evidence['node_id']
            if old and graph != context_graph(old['graph'],order): raise ValueError('GREEN modified its graph')
            if old is None or set(proofs)-set(old['proofs'])!={key} or graph['revision']!=old['graph']['revision']:
                raise ValueError('invalid GREEN transition')
            if evidence['check'].get('verdict')!='green' or not evidence.get('sealable_generation'):
                raise ValueError('GREEN lacks actual checker/provider evidence')
            if not set(nodes[key]['dependencies'])<=old['proofs'].keys(): raise ValueError('unproved prerequisite')
            source=evidence['check']['source_content']
            if nodes[key]['statement'] not in source or proofs[key] not in source:
                raise ValueError('GREEN evidence does not bind statement and proof')
            if evidence['visibility']!=fact_manifest(old['graph'],old['proofs'],key): raise ValueError('GREEN context mismatch')
        elif event not in {'attempt','revision-attempt','red','failed','seal','resume'}:
            raise ValueError('unknown commit event')
        if event in {'attempt','revision-attempt','red','failed','seal','resume'} and old:
            if graph!=old['graph'] or proofs!=old['proofs']: raise ValueError('non-proof event changed graph/proofs')
        if old and new.get('runtime_policy',{}).get('retry_policy')=='residual-context-v1':
            before=old.get('attempt_contexts',{})
            if event=='attempt':
                key=evidence['node_id']; policy=new['runtime_policy']
                if new['rounds']!={**old['rounds'],key:old['rounds'].get(key,0)+1}:
                    raise ValueError('invalid attempt charge')
                if old['rounds'].get(key,0)>=policy['max_node_rounds'] and not extra_round_available(old,key,policy['max_node_rounds'],policy['context_retry_rounds']):
                    raise ValueError('no certified context retry credit')
                if new.get('attempt_contexts')!={**before,key:required_context(old,key)}:
                    raise ValueError('attempt context differs from certified dependencies')
            elif new.get('attempt_contexts',{})!=before:
                raise ValueError('attempt context changed outside charged attempt')
        if event=='seal':
            if evidence.get('impact',{}).get('verdict')!='pass' or evidence.get('dependencies',{}).get('verdict')!='green':
                raise ValueError('seal lacks dependency/impact audit')
            if set(proofs)!=nodes.keys() or evidence.get('complete',{}).get('verdict')!='green' or evidence.get('build',{}).get('verdict')!='green':
                raise ValueError('seal requires complete checked proofs and session build')
        if new['status']=='retired' and (nodes or package['contracts']): raise ValueError('only empty auxiliary theory may retire')
        if new['status']=='sealed' and event!='seal': raise ValueError('seal event required')
    return validate


def journal(run_dir, run, package):
    binding = {**run['session_schedule'], 'run_id':run['run_id'], 'implementation':digest(run['implementation']),
               'package_sha256':package['package_payload_sha256'],
               'imports':read(run_dir/'theories'/Path(package['file_slot']).stem/'imports.json')
                   if (run_dir/'theories'/Path(package['file_slot']).stem/'imports.json').exists() else []}
    return ProofCommits(run_dir/'theories'/Path(package['file_slot']).stem/'acceptance', binding, validator(package, run))


def _progress_unlocked(run_dir):
    run=read(run_dir/'run.json'); data=read(run_dir/'execution-input.json'); files=deepcopy(data['files'])
    # Published facts are a projection of accepted seals; never trust old catalog rows.
    durable(run_dir/'generated-premises.json',{'records':[]})
    for entry in files:
        p=package_for(run,entry['file_slot']); state=journal(run_dir,run,p).replay()
        if state:
            directory=run_dir/'theories'/Path(p['file_slot']).stem
            durable(directory/'active-graph.json',state['graph'])
            durable(directory/'progress.json',state)
            if state['status']=='sealed':
                from regen.execution.proof_session_runner import publish_generated_records, generated_checkpoint_theory
                record=read(sorted((directory/'acceptance/commits').glob('*.json'))[-1])
                evidence=record['evidence']; source=evidence['source_text']
                if digest(source)!=evidence['source_hash']: raise ValueError('sealed source identity mismatch')
                destination=run_dir/'generated'/(generated_checkpoint_theory(p['file_slot'])+'.thy')
                if destination.exists() and destination.read_text()!=source: raise ValueError('published source drift')
                if not destination.exists(): destination.write_text(source)
                publish_generated_records(run_dir,state['graph'],state['seal_id'],p)
            entry.update(status=state['status'], graph_revision=state['graph']['revision'],
                generation_ids=[run['run_id']+'--'+Path(entry['file_slot']).stem],
                seal_id=state.get('seal_id'), reason=state.get('reason',''))
    complete=all(f['status'] in {'sealed','retired'} for f in files)
    value={'schema_version':2,'artifact_type':'repository-proof-progress','files':files,
        'session_status':'complete' if complete else 'partial', 'contracts':sum(len(f['contract_ids']) for f in files),
        'proved_contracts':sum(len(f['contract_ids']) for f in files if f['status']=='sealed')}
    durable(Path(run['session_progress_ledger']),value)
    return value


def progress(run_dir):
    with _PROJECTION_LOCK:
        return _progress_unlocked(run_dir)


def checker_for(run_dir, run, directory, timeout, ancestors, *, heaps_dir=None):
    from regen.execution.isabelle_node_checker import IsabelleChecker
    from regen.infrastructure.path_identity import source_identity
    if source_identity(Path(run['isabelle_dir']),allow_links=True)!=run['isabelle_sha256']:
        raise ValueError('execution Isabelle installation differs from frozen toolchain')
    context_root=Path(run.get('checker_context_root',run.get('environment_run',run_dir)))
    direct=run.get('execution_input_kind')=='refine-dataset'
    generated=directory.parent/'generated' if direct else run_dir/'generated'
    heaps=heaps_dir or (directory.parent/'heaps' if direct else run_dir/'heaps')
    return IsabelleChecker(context_root,directory,timeout=timeout,
        previous_theories=ancestors,generated_dir=generated,heaps_dir=heaps,
        l4v_dir=Path(run['l4v']),semantic_workspace=Path(run['semantic_workspace']),
        base_session=run['base_session'],base_import=run['base_import'],base_catalog=run['base_catalog'],
        image=run['image'],isabelle_dir=Path(run['isabelle_dir']),import_all_previous=True)


def setup(run_dir, theory, timeout):
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    run=read(run_dir/'run.json'); data=read(run_dir/'execution-input.json')
    # Execution implementation is frozen separately from preparation, which is already sealed.
    repo=Path(__file__).resolve().parents[2]
    import hashlib
    if any(hashlib.sha256((repo/p).read_bytes()).hexdigest()!=h for p,h in run['implementation'].items()):
        raise ValueError('execution code changed after run initialization; use a new proof run')
    entry=next(f for f in data['files'] if f['theory']==theory)
    package=package_for(run,entry['file_slot']); directory=run_dir/'theories'/Path(entry['file_slot']).stem
    directory.mkdir(parents=True,exist_ok=True)
    with _PROJECTION_LOCK:
        states=progress(run_dir)['files']
        catalog=read(run_dir/'generated-premises.json')['records']
    ancestry_path=directory/'imports.json'
    if ancestry_path.exists():
        ancestors=read(ancestry_path)
    elif package['repository_projection'].get('schema_version',0)>=4:
        providers=[
            item['theory']
            for item in package['repository_projection'].get('suggested_predecessors',[])
        ]
        by_theory={item['theory']:item for item in states}
        unavailable=[
            provider for provider in providers
            if provider not in by_theory or by_theory[provider]['status']!='sealed'
        ]
        if unavailable:
            raise ValueError(
                'theory is not ready; explicit providers are not sealed: '
                + ', '.join(sorted(unavailable))
            )
        ancestors=[
            generated_checkpoint_theory(by_theory[provider]['file_slot'])
            for provider in providers
        ]
    else:
        # Compatibility for pre-schema-4 runs.
        ancestors=[generated_checkpoint_theory(f['file_slot']) for f in states if f['status']=='sealed' and f['theory']!=theory]
    durable(ancestry_path,ancestors,immutable=True)
    visible=[record for record in catalog if any(record['canonical_name'].startswith(name+'.') for name in ancestors)]
    durable(directory/'sealed-premises.json',{'records':visible})
    heaps=run_dir/'heaps'
    if package['repository_projection'].get('schema_version',0)>=4:
        heaps=directory/'heaps'
        if not heaps.exists(): shutil.copytree(run_dir/'heaps',heaps)
    checker=checker_for(run_dir,run,directory/'isabelle',timeout,ancestors,heaps_dir=heaps)
    if run.get('execution_input_kind')=='refine-dataset':
        # Freeze only the providers and their transitive imports. ROOT, logs and
        # Generated_Refine heaps belong to this theory, never to other workers.
        pending=list(ancestors); copied=set()
        by_checkpoint={generated_checkpoint_theory(f['file_slot']):f for f in states}
        while pending:
            name=pending.pop()
            if name in copied: continue
            copied.add(name)
            provider=by_checkpoint[name]
            provider_dir=run_dir/'theories'/Path(provider['file_slot']).stem
            pending.extend(read(provider_dir/'imports.json'))
            source=run_dir/'generated'/(name+'.thy')
            target=checker.generated_dir/source.name
            if target.exists() and target.read_bytes()!=source.read_bytes():
                raise ValueError('frozen provider source drift')
            if not target.exists(): shutil.copy2(source,target)
        ready=directory/'provider-session.json'
        if ready.exists() and read(ready)!={'imports':ancestors}:
            raise ValueError('frozen provider session drift')
        if ancestors and not ready.exists():
            build=checker.build_generated_session(ancestors)
            if build['verdict']!='green':
                raise RuntimeError('provider session build failed: '+build.get('diagnostic',''))
            durable(ready,{'imports':ancestors},immutable=True)
    return run,package,directory,journal(run_dir,run,package),checker,ancestors


def plan(run_dir,theory,*,provider,planner_model,timeout):
    from regen.planning.file_planner import plan_file,planning_environment
    from regen.execution.proof_session_runner import frozen_role_model
    run,p,d,j,c,ancestors=setup(run_dir,theory,timeout)
    state=j.replay()
    if state: return state['graph']
    if planner_model!=frozen_role_model(run,'File Planner'): raise ValueError('planner model differs from frozen model')
    environment=planning_environment(p)
    outcomes=progress(run_dir)['files']
    retired={f['theory'] for f in outcomes if f['status']=='retired'}
    environment['upstream_outcomes']=outcomes
    environment['suggested_providers']=[n for n in environment.get('suggested_providers',[]) if n['theory'] not in retired]
    generated=d/'sealed-premises.json'
    if generated.exists():
        environment['legal_premises']=[*environment['legal_premises'],*read(generated)['records']]
    feedback=[]
    for attempt in range(3):
        call_index=1+len(list((d/'llm').glob('*.meta.json')))
        try:
            graph=plan_file(p,run_id=run['run_id']+'--'+Path(p['file_slot']).stem,output_dir=d,
                provider=provider,model=planner_model,timeout=timeout,environment=environment,
                graph_bindings={
                    'plan_id':p['plan_id'],
                    'plan_revision':p['repository_projection'].get('plan_revision'),
                    'projection_sha256':p['projection_sha256'],
                    'context_profile_ids':p['repository_projection'].get('context_profile_ids',[]),
                    'sealed_provider_theories':[
                        item['theory'] for item in p['repository_projection'].get(
                            'suggested_predecessors', []
                        )
                    ],
                    'parent_premise_ids':sorted(
                        item['record_id'] for item in environment.get('legal_premises',[])
                    ),
                },
                call_index=call_index,persist_graph=False,rejection_feedback=feedback)
        except (ValueError,KeyError) as error:
            feedback.append(str(error));durable(d/f'initial-rejection-{call_index:03d}.json',{'reason':str(error)},immutable=True);continue
        graph=context_graph(graph,[])
        check=c.check_graph(graph)
        durable(d/f'initial-check-{call_index:03d}.json',freeze_evidence(check))
        if check['verdict']!='green': feedback.append(check['diagnostic']);continue
        state={'graph':graph,'proofs':{},'accepted_order':[],'rounds':{},'revisions':{},'status':'planned','seal_id':None}
        j.append('initial',state,{'check':freeze_evidence(check),'provenance':p['repository_projection']})
        write_immutable_graph(d/'graph-v1.json',graph)
        progress(run_dir)
        return graph
    raise ValueError('File Planner statement validation exhausted; see initial-check artifacts')


def revoke_graph(package,graph,key):
    """Legacy schema compatibility; schema-4 commit validation rejects removals."""
    if package['repository_projection'].get('schema_version',0)>=4:
        raise ValueError('schema-4 graph nodes are retained; revocation is forbidden')
    node=next(n for n in graph['nodes'] if n['id']==key)
    if node['role']=='contract' or key in graph['accepted_order']: raise ValueError('cannot revoke fixed/GREEN node')
    proposal={'generated_nodes':[{**n,'dependencies':[dep for dep in n['dependencies'] if dep!=key]} for n in graph['nodes'] if n['role']!='contract' and n['id']!=key],
        'contract_dependencies':{n['id']:[dep for dep in n['dependencies'] if dep!=key] for n in graph['nodes'] if n['role']=='contract'},
        'contract_strategies':{n['id']:n.get('strategy','') for n in graph['nodes'] if n['role']=='contract'}}
    revised=build_graph(package,proposal,run_id=graph['run_id'],revision=graph['revision']+1)
    return context_graph(revised,graph['accepted_order'])


def run_file(run_dir,theory,*,provider,planner_model,revision_model,prover_model,
             lemma_backend,timeout,max_attempts,max_node_rounds,max_node_revisions,
             graph_timeout=900,think_timeout=180,node_timeout=300,salvage_provider=None,context_retry_rounds=1):
    from regen.execution.lemma_prover import prove_node
    from regen.execution.graph_revision_agent import revise_graph, ProposalRejected
    from regen.execution.proof_session_runner import (next_graph_decision_index,
                                                    generated_checkpoint_theory, frozen_role_model)
    from regen.analysis.retained_semantic_impact import audit_theory
    run,p,d,j,checker,ancestors=setup(run_dir,theory,node_timeout)
    for role,model in [('File Planner',planner_model),('Graph Revision Agent',revision_model),('Lemma Prover',prover_model)]:
        if model!=frozen_role_model(run,role): raise ValueError(f'{role} differs from frozen model')
    checker.persistent = run.get("checker_policy") == "fixed-prefix-cold-green-v1"
    state=j.replay()
    if state is None:
        plan(run_dir,theory,provider=provider,planner_model=planner_model,timeout=timeout)
        state=j.replay()
    budgets=run.get('budgets')
    if budgets is None:
        budgets=read(Path(run['environment_run'])/'Environment-Freeze/campaign-manifest.json')['budgets']
    if max_attempts>budgets.get('whole_proof_attempts_per_logical_round',max_attempts):
        raise ValueError('requested attempts exceed frozen campaign budget')
    if timeout>budgets.get('model_call_timeout_seconds',timeout):
        raise ValueError('requested model timeout exceeds frozen campaign budget')
    runtime_policy={'max_attempts':max_attempts,'max_node_rounds':max_node_rounds,
        'max_node_revisions':max_node_revisions,'timeout':timeout,'graph_timeout':graph_timeout,
        'node_timeout':node_timeout,'think_timeout':think_timeout,'backend':lemma_backend,
        'provider':provider,'salvage_provider':salvage_provider,
        'checker_policy':run.get('checker_policy','cold'),
        'context_retry_rounds':context_retry_rounds,'retry_policy':'residual-context-v1'}
    if any(runtime_policy[k]<=0 for k in ('max_attempts','max_node_rounds','timeout','graph_timeout','node_timeout','think_timeout')) or max_node_revisions<0 or context_retry_rounds not in (0,1):
        raise ValueError('invalid runtime budget')
    durable(d/'runtime-policy.json',runtime_policy,immutable=True)
    graph_checker=checker_for(run_dir,run,d/'isabelle',graph_timeout,ancestors)
    def accept(event,new,evidence):
        nonlocal state
        state=j.append(event,new,freeze_evidence(evidence));progress(run_dir)
    def stop(reason,detail=None):
        new=deepcopy(state);new.update(status='failed',reason=reason)
        new['failure_detail']=detail or {}
        accept('failed',new,{'reason':reason,'failure_detail':new['failure_detail']})
    def accepted_decision_history():
        # j.replay/append validated this chain; candidate decision files are not authority.
        from regen.execution.graph_revision_agent import normalized_retry_directive, retry_directive_sha256, semantic_residual_fingerprints
        history = []
        for path in sorted((j.directory/'commits').glob('*.json')):
            record = read(path); evidence = record['evidence']; decision = evidence.get('decision')
            if not decision or not evidence.get('node_id'): continue
            key = evidence['node_id']; prior = record['state'].get('last_failure', {})
            history.append({'decision_id': path.stem, 'node_id': key,
                'proof_round': record['state']['rounds'].get(key, 0),
                'decision': decision.get('decision'), 'failure_class': decision.get('failure_class'),
                'reason': decision.get('reason'), 'retry_directive': normalized_retry_directive(decision),
                'retry_directive_sha256': retry_directive_sha256(decision),
                'transaction_applied': True, 'transaction_action': record['event'],
                'semantic_residual_fingerprints': semantic_residual_fingerprints(prior) if prior.get('node_id') == key else []})
        return history

    def revise_or_revoke(key,result):
        graph=state['graph']; node=next(n for n in graph['nodes'] if n['id']==key)
        decision={'decision':'ABANDON_GENERATION','reason':'structural revision budget exhausted'}
        rejection=state.get('revision_rejection')
        if rejection and (rejection['node_id']!=key or rejection['base_revision']!=graph['revision']):
            rejection=None
        if rejection:
            decision={'decision':'REVISE_GRAPH','reason':'pending statement correction'}
        while (state['revisions'].get(key,0)<max_node_revisions
               and sum(state['revisions'].values())<budgets.get('graph_revisions_per_generation',16)):
            new=deepcopy(state);new['revisions'][key]=state['revisions'].get(key,0)+1
            accept('revision-attempt',new,{'node_id':key,'result':result})
            decision_index=next_graph_decision_index(d)
            try:
                revised,decision=revise_graph(p,graph,result,set(state['proofs']),output_dir=d,
                    provider=provider,model=revision_model,timeout=graph_timeout,
                    premise_database=Path(run['premise_database']),
                    generated_records=d/'sealed-premises.json',
                    symbol_resolver=lambda names: graph_checker.resolve_symbols(names,node.get('context',[])),
                    decision_index=decision_index,allow_structural_revision=True,
                    decision_context={'phase':'node-proof', 'proof_round':state['rounds'].get(key,0),
                                      'accepted_decision_history':accepted_decision_history(),
                                      'previous_revision_rejections':[rejection] if rejection else [],
                                      'auxiliary_failure_policy':'retain-and-stop',
                                      'split_policy':'preserve-parent', 'proof_rounds_used':state['rounds'].get(key,0),
                                      'proof_retry_limit':max_node_rounds,
                                      'context_retry_rounds_remaining':min(context_retry_rounds,max(0,max_node_rounds+context_retry_rounds-state['rounds'].get(key,0)))})
            except ProposalRejected as error:
                rejection={'node_id':key,'base_revision':graph['revision'],'candidate_revision':None,
                           'proposal':error.proposal,'check':{'verdict':'red','diagnostic':str(error),
                           'isabelle_called':False,'failure_scope':'proposal'}}
                decision={'decision':'REVISE_GRAPH','reason':'unaccepted proposal needs correction'}
                durable(d/f'rejected-decision-{decision_index:03d}.json',freeze_evidence(rejection),immutable=True)
                new=deepcopy(state);new['revision_rejection']=rejection
                accept('red',new,{'node_id':key,'proposal_rejection':rejection})
                continue
            if revised['revision']==graph['revision']:
                # An explicit non-revision decision abandons the rejected proposal.
                rejection=None
                break
            if p['repository_projection'].get('schema_version',0)>=4:
                removed={item['id'] for item in graph['nodes']}-{item['id'] for item in revised['nodes']}
                if removed:
                    rejection={'node_id':key,'base_revision':graph['revision'],
                               'candidate_revision':revised['revision'],'proposal':decision,
                               'check':{'verdict':'red','diagnostic':
                               'node deletion is forbidden; retain failed helpers: '+', '.join(sorted(removed)),
                               'isabelle_called':False,'failure_scope':'proposal'}}
                    durable(d/f'rejected-decision-{decision_index:03d}.json',freeze_evidence(rejection),immutable=True)
                    new=deepcopy(state);new['revision_rejection']=rejection
                    accept('red',new,{'node_id':key,'proposal_rejection':rejection})
                    continue
            revised['initial_graph_owner']=graph.get('initial_graph_owner','File Planner')
            revised['runtime_graph_owner']='Graph Revision Agent'
            revised=context_graph(revised,state['accepted_order'])
            check=graph_checker.check_graph(revised)
            if check['verdict']=='green':
                new=deepcopy(state);new['graph']=revised;new['status']='generating'
                new.pop('revision_rejection',None)
                accept('revision',new,{'check':check,'decision':decision,'node_id':key,
                    'reason':decision.get('reason') or decision.get('repair_guidance') or 'GRA revision'})
                return True
            rejection={'node_id':key,'base_revision':graph['revision'],
                       'candidate_revision':revised['revision'],'proposal':decision,'check':check}
            durable(d/f'rejected-revision-{revised["revision"]}.json',freeze_evidence(rejection),immutable=True)
            # Rejected declarations never replace the accepted graph. Commit the
            # feedback and charged costs so crash recovery can resume correction.
            new=deepcopy(state);new['revision_rejection']=rejection
            accept('red',new,{'node_id':key,'statement_rejection':rejection})
        if rejection and decision.get('decision')=='REVISE_GRAPH':
            stop('GRA proposal/statement correction exhausted the revision budget');return False
        if decision.get('decision')=='RETRY_PROOF':
            new=deepcopy(state);new.pop('revision_rejection',None);new['retry_guidance']=decision
            new['retry_guidance_binding']={'node_id':key,'statement':node['statement'],'context':node.get('context',[])}
            accept('red',new,{'node_id':key,'decision':decision})
        if (decision.get('decision')=='RETRY_PROOF' and state['rounds'].get(key,0)<max_node_rounds):
            return True
        if node['role']=='contract':
            stop('fixed contract stopped: '+str(decision.get('failure_class') or decision.get('reason')),
                 {'node_id':key,'gra_decision':decision,'proof_rounds_used':state['rounds'].get(key,0),
                  'proof_round_limit':max_node_rounds,'gra_calls_used':state['revisions'].get(key,0),
                  'gra_call_limit':max_node_revisions});return False
        stop(
            'generated helper stopped and was retained: '
            + str(decision.get('failure_class') or decision.get('reason')),
            {'node_id':key,'gra_decision':decision,
             'proof_rounds_used':state['rounds'].get(key,0),
             'proof_round_limit':max_node_rounds,
             'gra_calls_used':state['revisions'].get(key,0),
             'gra_call_limit':max_node_revisions,
             'failed_helper_retained':True},
        )
        return False
    if state['status']=='failed':
        new=deepcopy(state);new['status']='generating'
        accept('resume',new,{'reason':'explicit retry preserves cumulative budgets'})
    while state['status'] not in {'sealed','retired','failed'}:
        graph=state['graph']; proofs=state['proofs']
        if state.get('revision_rejection'):
            key=state['revision_rejection']['node_id']
            if not revise_or_revoke(key,state['last_failure']): break
            continue
        if not graph['nodes']:
            new=deepcopy(state);new['status']='retired'
            accept('revoke',new,{'check':graph_checker.check_graph(graph),'reason':'empty auxiliary plan; no theorem proved'})
            break
        ready=executable_nodes(graph,{key:'green' for key in proofs})
        if not ready:
            if len(proofs)!=len(graph['nodes']):
                stop('no executable node in validated active graph');break
            complete=checker.check_complete(graph,proofs)
            if complete['verdict']!='green':
                stop('complete theory check failed; see assembly evidence')
                durable(d/'assembly-failure.json',freeze_evidence(complete));break
            impact=audit_theory(checker=checker,graph=graph,proofs=proofs,
                premise_snapshot_path=Path(run['premise_snapshot']),output_path=d/'retained-semantic-impact.json')
            name=generated_checkpoint_theory(p['file_slot'])
            source=checker.complete_source(graph,proofs,theory_name=name)
            path=checker.generated_dir/(name+'.thy');path.write_text(source)
            build=checker.build_generated_session([*ancestors,name])
            if build['verdict']!='green':
                stop('generated session assembly failed');durable(d/'assembly-failure.json',freeze_evidence(build));break
            proof_deps=checker.last_complete_audit
            if proof_deps.get('verdict')!='green': raise ValueError('generated adoption audit failed')
            evidence={'complete':complete,'build':build,'impact':impact,'dependencies':proof_deps,
                      'source':str(path),'source_text':source,'source_hash':digest(source)}
            new=deepcopy(state);new['status']='sealed';new['seal_id']='theory-seal:'+digest(freeze_evidence(evidence))[:20]
            accept('seal',new,evidence)
            break
        key=ready[0]
        supplemental=extra_round_available(state,key,max_node_rounds,context_retry_rounds)
        if state['rounds'].get(key,0)>=max_node_rounds and not supplemental:
            if not revise_or_revoke(key,state.get('last_failure',{'node_id':key,'status':'red','residual':'proof round budget exhausted','attempts':[]})): break
            continue
        previous_feedback=resume_round_feedback(d,state,key)
        new=deepcopy(state);new['rounds'][key]=new['rounds'].get(key,0)+1;new['status']='generating';new['runtime_policy']=runtime_policy
        new.setdefault('attempt_contexts',{})[key]=required_context(state,key)
        accept('attempt',new,{'node_id':key,'supplemental':supplemental,'policy':'an interrupted attempt remains charged; attempts never disappear on revoke'})
        result=prove_node(graph,key,proofs,checker=checker,premise_database=Path(run['premise_database']),
            output_dir=d,generated_records=d/'sealed-premises.json',provider=provider,model=prover_model,
            timeout=timeout,max_attempts=max_attempts,backend=lemma_backend,round_id=state['rounds'][key],
            think_timeout=think_timeout,salvage_provider=salvage_provider,
            retrieval_hint=node_retry_guidance(state,next(n for n in graph['nodes'] if n['id']==key)),
            previous_round_feedback=previous_feedback)
        if result.get('failure_scope')=='infrastructure' or not result.get('sealable_generation'):
            raise RuntimeError('backend infrastructure/non-sealable result; no GREEN accepted')
        if result['status']=='green':
            accepted={**proofs,key:result['proof_body']}
            # Bind both backends to this exact prefix and exact returned proof body.
            check=checker.check_node(graph,accepted,key)
            if check['verdict']=='green':
                new=deepcopy(state);new['proofs']=accepted;new['accepted_order'].append(key)
                new['graph']=context_graph(graph,new['accepted_order'])
                accept('green',new,{'node_id':key,'check':check,'result':result,
                    'sealable_generation':True,'visibility':fact_manifest(graph,proofs,key)})
                continue
            result={**result,'status':'red','residual':check['diagnostic'],'check':check}
        new=deepcopy(state);new['last_failure']=result
        accept('red',new,{'node_id':key,'result':result})
        if not revise_or_revoke(key,result): break
    checker.close_prefix()
    return next(f for f in progress(run_dir)['files'] if f['theory']==theory)


def run_session(run_dir,*,retry_failed=False,max_parallel_theories=1,**options):
    if max_parallel_theories < 1:
        raise ValueError('max_parallel_theories must be positive')
    run=read(run_dir/'run.json')
    listing=read(Path(run['session_schedule']['source']))
    direct=listing.get('schema_version')==5
    if not direct or max_parallel_theories==1:
        results=[]
        for entry in progress(run_dir)['files']:
            if entry['status'] in {'sealed','retired'} or (entry['status']=='failed' and not retry_failed):
                results.append(entry);continue
            results.append(run_file(run_dir,entry['theory'],**options))
        return {**progress(run_dir),'results':results}

    data=read(run_dir/'execution-input.json'); results=[]; attempted=set()
    while True:
        state={item['theory']:item for item in progress(run_dir)['files']}
        terminal={name for name,item in state.items() if item['status'] in {'sealed','retired'}}
        ready=[]
        for entry in data['files']:
            item=state[entry['theory']]
            if entry['theory'] in attempted:
                continue
            if item['status'] in {'sealed','retired'} or (item['status']=='failed' and not retry_failed):
                continue
            package=package_for(run,entry['file_slot'])
            providers={p['theory'] for p in package['repository_projection'].get('suggested_predecessors',[])}
            if providers <= terminal:
                ready.append((entry['order'],entry['theory']))
        if not ready: break
        batch=[name for _,name in sorted(ready)[:max_parallel_theories]]
        attempted.update(batch)
        with ThreadPoolExecutor(max_workers=len(batch),thread_name_prefix='theory') as pool:
            futures={pool.submit(run_file,run_dir,name,**options):name for name in batch}
            for future in as_completed(futures):
                results.append(future.result())
    final=progress(run_dir)
    blocked=[item['theory'] for item in final['files'] if item['status']=='pending']
    return {**final,'results':results,'blocked_theories':blocked,
            'max_parallel_theories':max_parallel_theories}


CONTINUATION_ENV_FIELDS = ('execution_input_kind','environment_run',
    'checker_context_root','checker_context_id','premise_database','premise_snapshot',
    'semantic_workspace','l4v','base_session','base_import','base_catalog',
    'isabelle_dir','image','isabelle_sha256')


def continuation_state(package, origin):
    """Verify every source commit before carrying proof state and charged costs."""
    source_run=origin['run']; records=origin['commits']
    if not records: raise ValueError('continuation has no source commits')
    expected={**source_run['session_schedule'],'run_id':source_run['run_id'],
        'implementation':digest(source_run['implementation']),
        'package_sha256':package['package_payload_sha256'],'imports':origin['imports']}
    old=None; parent=None; contexts={}
    for number,record in enumerate(records,1):
        body={k:v for k,v in record.items() if k!='commit_id'}
        if (record['sequence']!=number or record['parent']!=parent
                or record['binding']!=expected or record['commit_id']!=digest(body)):
            raise ValueError('invalid continuation source chain')
        validator(package,source_run)(old,record['state'],record['event'],record['evidence'])
        if record['event']=='attempt':
            contexts[record['evidence']['node_id']]=required_context(old,record['evidence']['node_id'])
        old=record['state'];parent=record['commit_id']
    if old['status'] in {'sealed','retired'}: raise ValueError('cannot resume terminal theory')
    state=deepcopy(old);state.pop('runtime_policy',None)
    state['attempt_contexts']=contexts
    state['status']='generating'
    return state


def validate_continuation(package, runtime, state, origin):
    if runtime is None: raise ValueError('continuation requires frozen environment binding')
    if any(runtime.get(k)!=origin['run'].get(k) for k in CONTINUATION_ENV_FIELDS):
        raise ValueError('continuation changed proof environment')
    if state!=continuation_state(package,origin):
        raise ValueError('continuation changed proofs, graph, or cumulative costs')


def fork_continuation(source_dir, destination, theory):
    """Create a new implementation binding with the old verified history embedded."""
    source_dir=source_dir.resolve(); destination=destination.resolve()
    if destination.exists(): raise ValueError('continuation destination already exists')
    source=read(source_dir/'run.json')
    entry=next(f for f in read(source_dir/'execution-input.json')['files'] if f['theory']==theory)
    package=package_for(source,entry['file_slot'])
    old_dir=source_dir/'theories'/theory
    origin={'run':source,'imports':read(old_dir/'imports.json'),
            'commits':[read(p) for p in sorted((old_dir/'acceptance/commits').glob('*.json'))]}
    state=continuation_state(package,origin)
    input_root=Path(source.get('checker_context_root') or source['environment_run'])
    run=init_run(destination,input_root,destination.name,
                 Path(source['session_schedule']['source']))
    if any(run.get(k)!=source.get(k) for k in CONTINUATION_ENV_FIELDS):
        raise ValueError('continuation environment mismatch')
    new_dir=destination/'theories'/theory
    shutil.copytree(old_dir,new_dir,ignore=shutil.ignore_patterns(
        'acceptance','isabelle','progress.json','active-graph.json','runtime-policy.json',
        'execution-stop.json','PROOF-RUN-REPORT.md'))
    graph_check=next(r['evidence']['check'] for r in reversed(origin['commits'])
                     if r['event'] in {'initial','revision','revoke'})
    # Embedded bytes are already immutable evidence; do not re-read mutable paths.
    journal(destination,run,package).append('initial',state,
        {'check':graph_check,'continuation':origin})
    durable(destination/'continuation.json',{'source_run':str(source_dir),
        'source_commit':origin['commits'][-1]['commit_id'],'theory':theory,
        'reason':'explicitly authorised continuation after harness optimisation',
        'cost_policy':'preserve charged rounds and revisions; one certified-context reserve',
        'developer_assisted_probe_imported':False},immutable=True)
    progress(destination)
    return run
