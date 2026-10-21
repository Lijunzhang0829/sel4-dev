"""Paired native replay of randomly selected committed helper/parent successes.

This is a checker/interface experiment, never an online generation benchmark.
Arm A imports the archived application in a separate process; arm B imports the
current application. Historical AI proofs are explicit, frozen replay inputs.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(source, output, seed, count):
    from regen.execution.proof_commits import durable, digest
    from regen.infrastructure.incremental_log import read_artifact as read
    from regen.execution.proof_batches import _binding
    from regen.planning.theory_document import parse, block, header, candidate, validate_document_commit
    from regen.tools.proof_idle_watch import projection
    source, output = source.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('experiment output must be new and independent')
    # Cheap journal projections avoid decoding every large inherited state.
    population = []
    for directory in sorted((source/'theories').iterdir()):
        commits = sorted((directory/'acceptance/commits').glob('*.json'))
        previous = None
        for path in commits:
            info, _ = projection(path)
            if previous and info.get('event') == 'document':
                record, before = read(path), read(previous)
                old, new = before['state'], record['state']
                nodes = {n['id']:n for n in new['graph']['nodes']}
                delta = [k for k in new['accepted_order'] if k not in old['proofs']]
                targets = [k for k in delta if nodes[k]['role']=='contract']
                helpers = [k for k in delta if nodes[k]['role']!='contract']
                if targets and helpers and record['evidence']['check']['verdict']=='green':
                    population.append({'theory':directory.name,'commit':path.name,
                        'before_commit':previous.name,'targets':targets,'helpers':helpers})
            previous = path
    if not 1 <= count <= len(population):
        raise ValueError('sample size exceeds eligible population')
    selected = random.Random(seed).sample(population, count)
    output.mkdir(parents=True)
    manifest = {'scope':'historical AI proof replay; no online generation',
        'seed':seed,'population':population,'selected':selected,'model_calls':0,
        'production_acceptance_writes':0,'sampling_unit':'committed step with new helpers and contract',
        'source_run':str(source),'source_run_sha256':sha(source/'run.json'),
        'candidate_timeout_seconds':120,'command_timeout_seconds':900,
        'resource_policy':{'native_slots':1,'threads':4,'resident_limit':1,'cpus':4,'memory_mb':16384},
        'revision_probe':'append a comment to the last new helper proof; identical statements and tactics',
        'limitations':['one paired replay per sampled step','successful-step selection bias',
            'conditional usefulness does not establish helper truth or difficulty reduction',
            'revision is a synthetic equivalent edit, not a historical repair']}
    durable(output/'manifest.json',manifest,immutable=True)
    fixtures = []
    for index, row in enumerate(selected):
        directory = source/'theories'/row['theory']
        old_record = read(directory/'acceptance/commits'/row['before_commit'])
        saved_record = read(directory/'acceptance/commits'/row['commit'])
        old, saved, evidence = old_record['state'], saved_record['state'], saved_record['evidence']
        package = read(source/'packages'/(row['theory']+'.thy.json'))
        validate_document_commit(package,old,saved,evidence)
        if evidence['document_old_binding'] != _binding(old) or evidence['new_binding'] != _binding(saved):
            raise ValueError('historical acceptance binding differs')
        entries, proofs = parse(evidence['document_source'],package,evidence['imports'])
        positions = {n['id']:i for i,n in enumerate(entries)}
        groups = [{'target_entry_id':target,'helper_ids':[k for k in row['helpers']
            if positions[k]<positions[target]]} for target in row['targets']]
        if any(not g['helper_ids'] for g in groups):
            raise ValueError('sampled parent has no preceding helper group')
        included = set(old['proofs']) | set(row['targets']) | {k for g in groups for k in g['helper_ids']}
        # Drop unrelated draft auxiliaries, keeping every fixed contract slot.
        source_text = header(package['theory'],evidence['imports']) + ''.join(
            block(n,proofs.get(n['id']) if n['id'] in included else None)
            for n in entries if n['role']=='contract' or n['id'] in included) + 'end\n'
        new = candidate(package,old,source_text,evidence['imports'])
        if any(new['proofs'].get(k)!=v for k,v in old['proofs'].items()):
            raise ValueError('replay changed historical accepted proofs')
        root = output/'fixtures'/f'{index:02}-{row["theory"]}-{row["commit"][:-5]}'
        fixture = {**row,'old':old,'package':package,'source':source_text,'groups':groups,
            'imports':evidence['imports'],'run':read(source/'run.json'),
            'ancestors':read(directory/'imports.json'),
            'new_ids':[k for k in new['accepted_order'] if k not in old['proofs']],
            'excluded_new_ids':sorted(set(row['helpers'])-included),
            'old_state_sha256':digest(old),'source_sha256':hashlib.sha256(source_text.encode()).hexdigest(),
            'historical':{'commit_id':saved_record['commit_id'],'pre_commit_id':old_record['commit_id'],
                'native_evidence_validated':True,'wall_ms':evidence['check'].get('wall_ms'),
                'prefix_prepare_ms':evidence['check'].get('prefix_prepare_ms'),
                'input_sha256':{str(p):sha(p) for p in [directory/'acceptance/commits'/row['commit'],
                    directory/'acceptance/commits'/row['before_commit']]}}}
        durable(root/'fixture.json',fixture,immutable=True)
        fixtures.append(root/'fixture.json')
    return manifest, fixtures


def arm(fixture_path, root, variant, assets):
    # These imports resolve against the arm's independently pinned application.
    from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
    from regen.execution.proof_commits import durable, digest
    from regen.execution.repository_execution import checker_for
    from regen.planning.theory_document import candidate, check_document, baseline, validate_document_commit, block
    fixture = json.loads(fixture_path.read_text())
    root.mkdir(parents=True,exist_ok=False)
    shutil.copytree(assets/'generated',root/'generated')
    checker = checker_for(root,fixture['run'],root/'isabelle',900,fixture['ancestors'],heaps_dir=assets/'heaps')
    checker.batch_candidate_timeout = 120
    if checker.imports()!=fixture['imports']:
        raise ValueError('replay imports differ')
    resources = NativeResources(1,4,1,cpus=4,memory_mb=16384)
    token = CURRENT_RESOURCES.set(resources)
    old, package, source = fixture['old'],fixture['package'],fixture['source']
    report = {'pass':False,'arm':variant,'checks':[],'model_calls':0,
        'old_state_sha256':digest(old),'input_source_sha256':fixture['source_sha256'],
        'application_root':str(Path(sys.modules['regen'].__file__).resolve().parent.parent)}
    started = time.monotonic()
    def checked(stage, before, text):
        new = candidate(package,before,text,checker.imports())
        base, mode = baseline(before,new)
        begin = time.monotonic()
        evidence = check_document(checker,before,new,text)
        elapsed = time.monotonic()-begin
        durable(root/(stage+'-check.json'),evidence,immutable=True)
        result=evidence['check']
        row={'stage':stage,'verdict':result['verdict'],'mode':mode,'wall_seconds':elapsed,
            'prefix_prepare_seconds':result.get('prefix_prepare_ms',0)/1000,
            'native_seconds':result.get('wall_ms',0)/1000,'worker':result.get('worker'),
            'reused_prefix_count':len(base['proofs']),
            'rechecked_ids':[k for k in new['accepted_order'] if k not in base['proofs']],
            'audited_facts':result.get('audited_facts',[])}
        report['checks'].append(row)
        durable(root/'progress.json',report)
        print(json.dumps({'arm':variant,**row}),flush=True)
        if result['verdict']!='green':
            raise RuntimeError('native '+stage+' failed: '+result.get('diagnostic','')[-1600:])
        validate_document_commit(package,before,new,evidence)
        row['native_acceptance_validated']=True
        return new
    try:
        if variant=='B':
            from regen.planning.sketch_check import check_groups, authorize
            from regen.planning.theory_document import DocumentStore
            store=DocumentStore(root/'document',package,old,checker.imports(),require_sketch=True)
            edited=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                'source':source,'sketches':fixture['groups']},{'historical_ai_replay':True})
            begin=time.monotonic()
            sketch=check_groups(checker,store,edited['source_sha256'])
            report['sketch']={'wall_seconds':time.monotonic()-begin,**sketch}
            durable(root/'sketch-check.json',sketch,immutable=True)
            if sketch['status']!='conditional_closed':
                raise RuntimeError('historically successful helper group did not conditionally close')
            worker=checker._batch_worker
            if worker.pending is not None:
                raise AssertionError('sketch polluted native accepted context')
            report['conditional_trial_discarded']=True
            authorize(store,source,fixture['new_ids'])
            print(json.dumps({'arm':variant,'stage':'conditional','wall_seconds':report['sketch']['wall_seconds'],
                'status':sketch['status']}),flush=True)
        accepted=checked('original',old,source)
        # Promote the proven batch before probing an earlier checkpoint.
        # align() is the normal commit-following worker publication path.
        from regen.execution.batch_checker import body
        from regen.execution.proof_batches import _render
        label='Batch_Check_'+digest(checker.imports())[:16]
        with resources.lease(checker):
            checker._batch_worker.align(body(_render(accepted['graph'],accepted['proofs'],checker.imports(),label)))
        nodes={n['id']:n for n in accepted['graph']['nodes']}
        helpers=[k for k in fixture['new_ids'] if nodes[k]['role']!='contract']
        key=helpers[-1]
        old_block=block(nodes[key],accepted['proofs'][key])
        if source.count(old_block)!=1:
            raise ValueError('revision proof block is not unique')
        revised=source.replace(old_block,block(nodes[key],accepted['proofs'][key]+'\n(* equivalent replay revision *)'),1)
        if variant=='B':
            store.state=accepted
            store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],'source':revised},
                {'synthetic_equivalent_revision':True})
            authorize(store,revised,[key])
        final=checked('equivalent-revision',accepted,revised)
        report.update(pass_=set(fixture['new_ids']) <= set(final['proofs']),
            targets_verified=fixture['targets'],new_helpers_verified=helpers,
            revision_helper_id=key)
        report['pass']=report.pop('pass_')
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        resources.close(); CURRENT_RESOURCES.reset(token)
        report['total_wall_seconds']=time.monotonic()-started
        report['native_metrics']=resources.metrics
        durable(root/'report.json',report,immutable=True)
    return report


def run(source, output, baseline_app, seed=20261013, count=3, prepare_only=False):
    from regen.execution.proof_commits import durable
    from regen.infrastructure.resource_budget import clone_heaps
    from regen.tools.harness_acceptance_baseline import protected_snapshot
    source,output,baseline_app=map(lambda p:p.resolve(),(source,output,baseline_app))
    before=protected_snapshot(source)
    manifest,fixtures=prepare(source,output,seed,count)
    current=Path(__file__).resolve().parents[2]
    applications={'A':baseline_app,'B':current}
    binding={arm:{str(p.relative_to(app)):sha(p) for p in sorted((app/'regen').rglob('*'))
        if p.is_file() and p.suffix in {'.py','.ML'} and '__pycache__' not in p.parts}
        for arm,app in applications.items()}
    durable(output/'applications.json',binding,immutable=True)
    if prepare_only:
        return {'prepared':True,'samples':len(fixtures)}
    report={'pass':False,'scope':manifest['scope'],'model_calls':0,'cases':[],
        'replicates_per_case_arm':1,'baseline_application':str(baseline_app),
        'variant_application':str(current)}
    try:
        for index,path in enumerate(fixtures):
            fixture=json.loads(path.read_text())
            assets=output/'assets'/fixture['theory']
            if not assets.exists():
                shutil.copytree(source/'theories'/fixture['theory']/'generated',assets/'generated')
                clone_heaps(source/'theories'/fixture['theory']/'heaps',assets/'heaps')
            row={'fixture':str(path),'theory':fixture['theory'],'commit':fixture['commit'],
                'targets':fixture['targets'],'arms':{},'historical':fixture['historical'],
                'excluded_new_ids':fixture['excluded_new_ids']}
            # Alternate order to expose, rather than hide, systematic warming.
            row['order']=['A','B'] if index%2==0 else ['B','A']
            report['cases'].append(row)
            for variant in row['order']:
                app=applications[variant]
                root=output/'arms'/(path.parent.name+'--'+variant)
                env={**os.environ,'PYTHONPATH':str(app),'PYTHONDONTWRITEBYTECODE':'1'}
                command=[sys.executable,'-u',str(Path(__file__).resolve()),'arm',
                    '--fixture',str(path),'--output',str(root),'--arm',variant,'--assets',str(assets)]
                durable(output/'progress.json',{'active_arm':str(root),'report':report})
                print(json.dumps({'starting':str(root),'application':str(app)}),flush=True)
                result=subprocess.run(command,env=env,cwd=output,timeout=3600)
                arm_report=json.loads((root/'report.json').read_text())
                if result.returncode or not arm_report['pass']:
                    raise RuntimeError('arm failed: '+str(root))
                if Path(arm_report['application_root'])!=app:
                    raise ValueError('arm imported a different application')
                row['arms'][variant]=arm_report
                durable(output/'progress.json',{'active_arm':None,'report':report})
        report['pass']=True
    finally:
        report['protected_source_unchanged']=protected_snapshot(source)==before
        report['pass'] &= report['protected_source_unchanged']
        durable(output/'report.json',report,immutable=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prepare_parser=commands.add_parser('run')
    prepare_parser.add_argument('--source',type=Path,required=True)
    prepare_parser.add_argument('--output',type=Path,required=True)
    prepare_parser.add_argument('--baseline-app',type=Path,required=True)
    prepare_parser.add_argument('--seed',type=int,default=20261013)
    prepare_parser.add_argument('--count',type=int,default=3)
    prepare_parser.add_argument('--prepare-only',action='store_true')
    arm_parser=commands.add_parser('arm')
    arm_parser.add_argument('--fixture',type=Path,required=True)
    arm_parser.add_argument('--output',type=Path,required=True)
    arm_parser.add_argument('--arm',choices=['A','B'],required=True)
    arm_parser.add_argument('--assets',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='run':
        result=run(args.source,args.output,args.baseline_app,args.seed,args.count,args.prepare_only)
    else:
        result=arm(args.fixture,args.output,args.arm,args.assets)
    print(json.dumps({'pass':result.get('pass'),'prepared':result.get('prepared')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
