"""Real Isabelle smoke of synthetic independent-entry lifecycle; no target/model calls."""
import argparse
from copy import deepcopy
from pathlib import Path
import time

from regen.execution.check_executor import CheckExecutor, CURRENT_RESOURCES, NativeResources
from regen.execution.check_tasks import TERMINAL
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import ProofCommits, durable, freeze_evidence
from regen.execution.repository_execution import context_graph, validator
from regen.infrastructure.progress_events import event_scope
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.independent_document_checks import run_checks
from regen.planning.proof_graph import build_graph
from regen.planning.theory_document import DocumentStore, block, header
from regen.preparation.checker_context import load_context


def run(dataset, output):
    started = time.monotonic()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    ctx = load_context(Path(dataset))
    resources = NativeResources(1, 4, 1, cpus=4, memory_mb=16384)
    executor = CheckExecutor(output/'tasks', workers=1)
    token = CURRENT_RESOURCES.set(resources)
    report = {'pass':False, 'model_calls':0, 'target_contracts_proved':0,
              'scope':'synthetic source author, real native checks and ordinary journal; not real target generation',
              'policy':resources.policy}
    try:
        def execute(task_dir):
            with event_scope(task_dir, run_id='independent-entry-native-smoke', theory='Bounded_Smoke'):
                clone_heaps(Path(ctx['heaps']), task_dir/'heaps')
                checker = IsabelleChecker(task_dir,task_dir/'isabelle',image=ctx['image'],
                    architecture=ctx['architecture'],timeout=60,heaps_dir=task_dir/'heaps',
                    l4v_dir=ctx['l4v'],semantic_workspace=ctx['semantic_workspace'],
                    base_session=ctx['base_session'],base_import=ctx['base_import'],
                    base_catalog=ctx['base_catalog'],isabelle_dir=ctx['isabelle'],
                    generated_dir=task_dir/'generated')
                checker.batch_candidate_timeout = 30
                report['candidate_budget'] = {'attribute':'batch_candidate_timeout', 'seconds':30}
                package = {'theory':'Bounded_Smoke','file_slot':'Bounded_Smoke.thy',
                    'repository_projection':{'nodes':[]},'contracts':[{'contract_id':'target',
                    'name':'target','exact_statement':'lemma target: "True"','contract_owned_context':[]},
                    {'contract_id':'dependent','name':'dependent',
                     'exact_statement':'lemma dependent: "(7::nat) < 8"','contract_owned_context':[]}]}
                propositions = {'first':'True & True','blocker':'True | False','pending':'True --> True'}
                helpers = [{'id':key,'name':key,'role':'helper','statement':f'lemma {key}: "{propositions[key]}"',
                            'context':[],'dependencies':[],'strategy':''}
                           for key in ['first','blocker','pending']]
                graph = context_graph(build_graph(package,{'generated_nodes':helpers},
                    run_id='bounded-native-synthetic',revision=1),[])
                state = {'graph':graph,'proofs':{},'accepted_order':[], 'rounds':{},
                         'revisions':{},'status':'planned','seal_id':None,'batch_requests':1}
                journal = ProofCommits(task_dir/'acceptance',{'run':'bounded-native-synthetic'},validator(package))
                try:
                    initial = freeze_evidence(checker.check_graph(graph))
                    if initial['verdict'] != 'green':
                        raise RuntimeError('synthetic declarations failed: '+initial.get('diagnostic',''))
                    journal.append('initial',state,{'check':initial})
                    by_id = {n['id']:n for n in graph['nodes']}
                    source = header(package['theory'],checker.imports())+''.join(
                        block(by_id[key], 'by fail' if key=='blocker' else 'using blocker by simp' if key=='dependent' else 'by simp')
                        for key in ['first','blocker','dependent','target','pending'])+'end\n'
                    store = DocumentStore(task_dir/'document',package,state,checker.imports())
                    store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                                'source':source},{'synthetic_fixture':True})
                    origin = {'sealable_generation':True,'synthetic_author_not_model':True,
                              'transcript_prefix':'synthetic-no-provider','parent_request_number':1}
                    result = run_checks(package,state,source,checker,task_dir/'checks',
                        evidence_origin=origin,commit=lambda new,ev:journal.append('document',new,ev))
                    restored = ProofCommits(task_dir/'acceptance',{'run':'bounded-native-synthetic'},
                                           validator(package)).replay()
                    rows = result['node_results']
                    result = {'pass': result['new_green']==['first','target','pending']
                        and [r['verdict'] for r in rows]==['green','red','red','green','green']
                        and set(restored['proofs'])=={'first','target','pending'}
                        and store.read()['source']==source,
                        'per_entry':[{k:r[k] for k in ('node_id','verdict')} for r in rows],
                        'accepted_ids':restored['accepted_order'],
                        'draft_preserved':store.read()['source']==source,
                        'journal_replayed':True,
                        'failed_entry_not_a_fact':'blocker' not in restored['proofs']}
                    durable(task_dir/'result-checks.json',result,immutable=True)
                    return result
                finally:
                    checker.close_prefix()
        row = executor.submit(kind='proof_batch',theory='Bounded_Smoke',
            binding={'checker_context_id':ctx['checker_context_id']},
            request={'synthetic':True,'no_model':True},execute=execute)
        while row['status'] not in TERMINAL:
            if time.monotonic()-started > 285:
                executor.cancel(row['task_id'])
            row = executor.wait([row['task_id']],timeout=5)[0]
        report.update(task=row,pass_=row['status']=='completed' and row.get('result',{}).get('pass',False))
        report['pass'] = report.pop('pass_')
    finally:
        executor.close()
        resources.close()
        CURRENT_RESOURCES.reset(token)
        report.update(wall_seconds=time.monotonic()-started, peak_native_active=resources.peak_active,
                      native_operations=deepcopy(resources.metrics))
        durable(output/'report.json',report,immutable=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.dataset,args.output)
    print({'pass':result['pass'],'wall_seconds':result['wall_seconds']})
    raise SystemExit(0 if result['pass'] else 1)
