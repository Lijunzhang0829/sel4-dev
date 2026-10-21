"""Replay frozen generated requests 45/46 with whole and audited single batches.

This measures checker costs, not online proof discovery or production acceptance.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.proof_batches import _binding, _render
from regen.execution.proof_commits import durable, digest
from regen.execution.repository_execution import checker_for
from regen.infrastructure.incremental_log import read_artifact as read
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.theory_document import (
    header, block, candidate, baseline, check_document, validate_document_commit,
)
from regen.tools.harness_acceptance_baseline import protected_snapshot

THEORY = 'Untyped_Object_Lifecycle'
SOURCE = Path('reports/regen/refine-astra-medium-20261005-006/proof')
PROVIDERS = Path('reports/regen/provider-bootstrap-repair-20261005/native-validation')/THEORY
REQUESTS = (45, 46)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_fixture(source=SOURCE):
    directory = source/'theories'/THEORY
    journal_path = directory/'acceptance/commits/000002.json'
    record = read(journal_path)
    old = record['state']
    package = read(source/'packages'/(THEORY+'.thy.json'))
    rows, hashes = [], {str(journal_path):sha(journal_path)}
    for number in REQUESTS:
        root = directory/'batch-requests'/f'{number:06}'
        saved, evidence = read(root/'candidate.json'), read(root/'check.json')
        if evidence['old_binding'] != _binding(old) or evidence['new_binding'] != _binding(saved):
            raise ValueError('saved candidate/pre-step context binding mismatch')
        if evidence['check']['verdict'] != 'red' or baseline(old, saved)[1] != 'append':
            raise ValueError('expected saved failed append candidate')
        for name in ('input.json', 'candidate.json', 'check.json', 'document-source.json'):
            hashes[str(root/name)] = sha(root/name)
        rows.append({'request':number, 'state':saved, 'source':evidence['document_source'],
                     'source_sha256':evidence['document_source_sha256']})
    return old, package, rows, evidence['imports'], {
        'pre_step_commit_id':record['commit_id'], 'pre_step_binding':_binding(old),
        'frozen_input_sha256':hashes, 'requests':list(REQUESTS),
        'scope':'Frozen generated candidate replay; no future proof supplied; no model calls',
    }


def one_declaration(package, current, saved, key, imports):
    """Retain accepted bytes; add exactly one previously generated declaration."""
    existing = {n['id']:n for n in current['graph']['nodes']}
    proposed = {n['id']:n for n in saved['graph']['nodes']}
    for accepted in current['proofs']:
        if saved['proofs'].get(accepted) != current['proofs'][accepted] or any(
                proposed[accepted].get(f) != existing[accepted].get(f)
                for f in ('id','name','role','statement','context')):
            raise ValueError('saved sequence changes already accepted prefix')
    nodes = [existing[k] for k in current['accepted_order']]
    nodes.append(proposed[key])
    nodes += [n for n in current['graph']['nodes']
              if n['id'] not in current['proofs'] and n['id'] != key]
    proofs = {**current['proofs'], key:saved['proofs'][key]}
    source = header(package['theory'],imports)+''.join(
        block(n,proofs.get(n['id'])) for n in nodes)+'end\n'
    result = candidate(package,current,source,imports)
    if baseline(current,result)[1] != 'append':
        raise ValueError('split unexpectedly changed accepted prefix')
    return result,source


def run(output, *, prepare_only=False):
    output = output.resolve()
    if output.exists() or output.is_relative_to(SOURCE.resolve()):
        raise ValueError('output must be new and isolated from source')
    old,package,rows,imports,manifest = load_fixture()
    output.mkdir(parents=True)
    manifest.update(model_calls=0,production_acceptance_writes=0,
        resource_policy={'native_slots':1,'threads':4,'memory_mb':16384},
        comparison_order=['whole-45','whole-46','single-45','single-46'],
        initial_prefix_preparation='shared hot worker, charged once and reported separately',
        audit_timing='Native worker combines candidate execution and audit; no independent audit timer',
        limitation='Two frozen failed candidates; replay timing does not estimate online contract success')
    durable(output/'manifest.json',manifest,immutable=True)
    durable(output/'fixture.json',{'old':old,'package':package,'rows':rows,'imports':imports},immutable=True)
    if prepare_only:
        return manifest
    before = protected_snapshot(SOURCE)
    shutil.copytree(PROVIDERS/'generated',output/'generated')
    clone_heaps(PROVIDERS/'heaps',output/'heaps')
    config = read(SOURCE/'run.json')
    ancestors = read(SOURCE/'theories'/THEORY/'imports.json')
    checker = checker_for(output,config,output/'isabelle',900,ancestors,heaps_dir=output/'heaps')
    assert checker.imports() == imports
    checker.batch_candidate_timeout = 120
    resources = NativeResources(1,4,1)
    token = CURRENT_RESOURCES.set(resources)
    report = {'pass':False,'checks':[],'model_calls':0,'production_acceptance_writes':0}
    def checked(arm,number,current,new,source):
        start=time.monotonic()
        evidence=check_document(checker,current,new,source)
        result=evidence['check']
        row={'arm':arm,'request':number,'new_ids':new['accepted_order'][len(current['accepted_order']):],
             'verdict':result['verdict'],'wall_seconds':time.monotonic()-start,
             'prefix_prepare_seconds':result.get('prefix_prepare_ms',0)/1000,
             'candidate_execution_and_audit_seconds':result.get('wall_ms',0)/1000,
             'worker':result.get('worker'),'old_binding':_binding(current),'new_binding':_binding(new)}
        if result['verdict']=='green':
            validate_document_commit(package,current,new,evidence)
            row['context_bound_acceptance_validated']=True
        else:
            try: validate_document_commit(package,current,new,evidence)
            except ValueError: row['red_acceptance_rejected']=True
            else: raise AssertionError('RED accepted')
        report['checks'].append(row)
        durable(output/f'check-{len(report["checks"]):03}.json',evidence,immutable=True)
        durable(output/'progress.json',report)
        return row
    try:
        for item in rows:
            result=checked('whole',item['request'],old,item['state'],item['source'])
            assert result['verdict']=='red'
        current=deepcopy(old)
        for item in rows:
            saved=item['state']
            for key in saved['accepted_order']:
                if key in current['proofs']: continue
                new,source=one_declaration(package,current,saved,key,imports)
                result=checked('single',item['request'],current,new,source)
                if result['verdict']!='green': break
                current=new
        report['accepted_prefix_ids']=current['accepted_order'][len(old['accepted_order']):]
        report['new_contracts']=sum(n['role']=='contract' and n['id'] in report['accepted_prefix_ids']
                                    for n in current['graph']['nodes'])
        # Probe the accepted context after rollback with a rejected helper reference.
        missing=[k for k in rows[-1]['state']['accepted_order'] if k not in current['proofs']]
        names={n['id']:n['name'] for n in rows[-1]['state']['graph']['nodes']}
        worker=checker._batch_worker
        label='Batch_Check_'+digest(imports)[:16]
        source=_render(current['graph'],current['proofs'],imports,label)
        from regen.execution.batch_checker import body
        with resources.lease(checker):
            worker.align(body(source))
            probes=[]
            for key in missing:
                probe=source.rsplit('\nend\n',1)[0]+'\nlemma prefix_isolation_probe: "True"\nusing '+names[key]+' by simp\nend\n'
                evidence=worker.check_batch(probe,['prefix_isolation_probe'])
                diagnostic=evidence.get('log_content','')+'\n'+evidence.get('diagnostic','')
                assert evidence['verdict']=='red' and 'Undefined fact' in diagnostic
                assert worker.pending is None
                probes.append({'fact':names[key],'verdict':evidence['verdict'],'undefined_fact':True})
                durable(output/('isolation-'+names[key]+'.json'),evidence,immutable=True)
        report['rejected_fact_isolation']=probes
        report['pass']=len(report['accepted_prefix_ids'])>=5 and bool(probes)
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        resources.close()
        CURRENT_RESOURCES.reset(token)
        report['protected_source_unchanged']=before==protected_snapshot(SOURCE)
        report['pass'] &= report['protected_source_unchanged']
        report['native_metrics']=resources.metrics
        durable(output/'report.json',report,immutable=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(args.output,prepare_only=args.prepare_only),indent=2),flush=True)
