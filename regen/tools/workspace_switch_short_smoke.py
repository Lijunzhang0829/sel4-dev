"""Isolated scripted tool-level smoke with real Isabelle and journal recovery.

No model, benchmark proofs, native verdict mocks or proof-validator overrides.
Only synthetic fixture setup and action provenance are substituted explicitly.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch
from uuid import uuid4

from jsonschema import Draft202012Validator
from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal
from regen.planning.parallel_file_planner import TheoryController, tools
from regen.planning.theory_document import parse, block, header
from regen.preparation.checker_context import load_context
from regen.tools.document_acceptance_validation import (
    MODEL, ScriptedProvenance, readonly_heaps, replay_uncached, require)

THEORIES = ['Switch_A', 'Switch_B']


def fixture(root, context, specifications=None):
    specifications = specifications or {
        'Switch_A': [('first', '(n::nat) + 0 = n'), ('blocker', '(n::nat) < n + 1'),
                     ('dependent', '(7::nat) < 8'), ('last', 'True & True')],
        'Switch_B': [('other', '(p::bool) ==> p')]}
    packages = {}
    for theory, statements in specifications.items():
        package = {'theory': theory, 'file_slot': theory+'.thy',
                   'repository_projection': {'nodes': []}, 'contracts': [
                       {'contract_id': key, 'name':key,
                        'exact_statement':f'lemma {key}: "{proposition}"',
                        'contract_owned_context':[]} for key,proposition in statements]}
        package['package_payload_sha256'] = digest(package)
        packages[theory] = package
    listing = {'files':[{'theory':t,'file_slot':p['file_slot'],'status':'pending','generation_ids':[],
                         'contract_ids':[c['contract_id'] for c in p['contracts']]}
                        for t,p in packages.items()]}
    repository = Path(__file__).resolve().parents[2]
    implementation = {str(p.relative_to(repository)):hashlib.sha256(p.read_bytes()).hexdigest()
                      for folder in ('execution','planning','infrastructure','analysis')
                      for p in (repository/'regen'/folder).iterdir()
                      if p.suffix in {'.py','.ML','.sh'}}
    run = {'run_id':'workspace-switch-smoke-'+uuid4().hex,
           'execution_mode':'repository-projection-v1','execution_input_kind':'refine-dataset',
           'execution_models':{'File Planner':MODEL},'implementation':implementation,
           'contract_package_dir':str(root/'packages'),
           'session_progress_ledger':str(root/'session-progress-ledger.json'),
           'session_schedule':{'file_list_sha256':digest(listing),'plan_id':'synthetic-workspace-smoke'},
           'validation_only':True,'model_calls':0,'base_import':context['base_import'],
           'checker_context_root':str(context['root']) if 'root' in context else '',
           'checker_context_id':context['checker_context_id']}
    durable(root/'run.json',run,immutable=True)
    durable(root/'execution-input.json',{'file_list':listing,'files':listing['files'],
            'package_hashes':{p['file_slot']:p['package_payload_sha256'] for p in packages.values()}},immutable=True)
    for package in packages.values():
        durable(root/'packages'/(package['file_slot']+'.json'),package,immutable=True)
    (root/'generated').mkdir()
    return run,packages


class ScriptedActions(ScriptedProvenance):
    def record(self,prefix,prompt,proposal,metadata,*,theory,model):
        path=self.path(prefix)
        require(path.resolve().is_relative_to(self.root),'foreign script receipt')
        require(model==MODEL and theory in THEORIES,'foreign scripted identity')
        body={'provider':'local-scripted-validation','real_model':False,
              'provider_network_call':False,'model_calls':0,'theory':theory,'model':model,
              'proposal':deepcopy(proposal),'metadata':deepcopy(metadata),'context_snapshot':prompt,
              'eligibility_override':'isolated-test-only-not-model-attestation'}
        checksum=digest(body)
        durable(path,{**body,'receipt_sha256':checksum},immutable=True)
        self.registry[str(path)]=checksum
        return json.dumps(proposal)


def run(dataset, output, *, generation_strategy="sketch-reviewed-v1", native_restart=False):
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    (root/'validation-source.py').write_bytes(Path(__file__).read_bytes())
    report={'pass':False,'model_calls':0,'benchmark_contracts_proved':0,
            'scope':'scripted public tools, real Isabelle and acceptance journals; no model or seal',
            'generation_strategy':generation_strategy,
            'native_restart_control':native_restart,
            'substitutions':['synthetic runtime setup','hash-bound synthetic action provenance'],
            'cases':[]}
    controller=executor=resources=None
    def case(name,**details):
        report['cases'].append({'name':name,'pass':True,**details})
        durable(root/'report.json',report)
    try:
        context=load_context(Path(dataset));config,packages=fixture(root,context)
        resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
        executor=CheckExecutor(root/'check-tasks',workers=1)
        report['resource_policy']=resources.policy
        report['checker_context_id']=context['checker_context_id']
        schemas={t['name']:Draft202012Validator(t['inputSchema']) for t in tools(True)}
        scripted=ScriptedActions(root)
        setup_count=[]
        def setup(requested_root,theory,timeout):
            require(Path(requested_root).resolve()==root and theory in packages,'foreign fixture setup')
            setup_count.append(theory)
            directory=root/'theories'/theory;directory.mkdir(parents=True,exist_ok=True)
            for name,value in [('imports.json',[]),('sealed-premises.json',{'records':[]})]:
                if not (directory/name).exists():durable(directory/name,value,immutable=True)
            checker=IsabelleChecker(root,directory/'isabelle',image=context['image'],
                architecture=context['architecture'],timeout=timeout,heaps_dir=context['heaps'],
                l4v_dir=context['l4v'],semantic_workspace=context['semantic_workspace'],
                base_session=context['base_session'],base_import=context['base_import'],
                base_catalog=context['base_catalog'],isabelle_dir=context['isabelle'],
                generated_dir=directory/'generated')
            original=checker._command
            checker._command=lambda *a,**kw:readonly_heaps(original(*a,**kw))
            checker.declaration_environment=digest(config)
            return config,packages[theory],directory,journal(root,config,packages[theory]),checker,[]
        serial=0
        with ExitStack() as stack:
            stack.enter_context(patch('regen.execution.repository_execution.setup',side_effect=setup))
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',side_effect=scripted.record))
            stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact',side_effect=scripted.validate))
            stack.enter_context(patch('regen.planning.batch_file_planner.call_llm',side_effect=AssertionError('model forbidden')))
            def create(theory):
                c=TheoryController(root,theory,resources=resources,executor=executor,document_mode=True,
                                   model=MODEL,check_timeout=120,candidate_timeout=30,
                                   generation_strategy_name=generation_strategy)
                c.switchable_theories=list(THEORIES)
                return c
            def call(tool,arguments):
                nonlocal serial
                schemas[tool].validate(arguments);serial+=1
                metadata={'thread_id':'synthetic-thread','turn_id':'synthetic-turn','call_id':str(serial)}
                durable(root/'tool-calls'/f'{serial:03d}-request.json',{'tool':tool,'arguments':arguments},immutable=True)
                response=controller.handle(tool,arguments,metadata)
                durable(root/'tool-calls'/f'{serial:03d}-result.json',response,immutable=True)
                return response
            def action(proposal):return call('proof_action',{'theory':controller.theory,'proposal':proposal})
            def read():return call('read_theory',{'theory':controller.theory})
            controller=create('Switch_A')
            draft=read()['document'];entries,_=parse(draft['source'],packages['Switch_A'],draft['draft_imports'])
            nodes={n['id']:n for n in entries}
            proofs={'first':'by simp','blocker':'by fail','dependent':'using blocker by simp','last':'by simp'}
            source=header('Switch_A',draft['draft_imports'])+''.join(block(nodes[k],v) for k,v in proofs.items())+'end\n'
            saved=action({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source})
            result=action({'action':'check_theory','source_sha256':saved['source_sha256']})
            feedback=result['feedback']
            verdicts=[r['verdict'] for r in feedback['entry_results']]
            require(verdicts==['green','red','red','green'],'per-entry checking did not continue past RED')
            require(set(feedback['new_green'])=={'first','last'},'wrong isolated acceptance')
            require(result['publication']=='partially_accepted','partial publication semantics missing')
            require('blocker' in feedback['entry_results'][2]['check'].get('diagnostic',''),
                    'dependent negative control did not reference unavailable blocker')
            before=replay_uncached(root,config,packages['Switch_A'])
            require(before['batch_requests']==1,'one multi-entry tool request charged multiple planner requests')
            case('public_batch_independent_results',verdicts=verdicts,new_green=feedback['new_green'],
                 request_number=result['request_number'])
            runtime=controller._runtime()
            local=action({'action':'switch_target','entry_id':'blocker','reason':'Repair pending route later',
                          'notes':'Other accepted proofs must remain available'})
            require(local['status']=='target_switched' and controller._runtime() is runtime,'local switch rebuilt runtime')
            require(read()['document']['source']==source,'local switch lost draft')
            case('local_switch_warm',setup_count=len(setup_count),draft_preserved=True)
            yielded=action({'action':'switch_target','theory':'Switch_B','reason':'Work on independent ready theory',
                            'notes':'Return to blocker and dependent without resetting earlier work'})
            require(yielded['theory_yield'],'theory switch did not yield')
            latch=action({'action':'check_theory','source_sha256':saved['source_sha256']})
            require(latch.get('theory_yield'),'yielding controller admitted further proof work')
            controller.close();controller=create('Switch_B')
            b=read()['document']
            good=action({'action':'edit_theory','base_sha256':b['source_sha256'],
                         'source':b['source'].replace('\noops\n','\nby assumption\n')})
            other=action({'action':'check_theory','source_sha256':good['source_sha256']})
            require(other['feedback']['status']=='accepted','other theory did not make native progress')
            case('yield_and_other_theory_progress',accepted=other['accepted_entry_ids'])
            controller.close();controller=create('Switch_A')
            resumed=read()
            require(set(resumed['accepted_ids'])=={'first','last'},'accepted facts lost on resume')
            require(resumed['document']['source']==source,'failed draft lost on resume')
            require(replay_uncached(root,config,packages['Switch_A'])['batch_requests']==1,'request costs reset on resume')
            require((root/'theories/Switch_A/document/active-target.json').exists(),'local target selection lost')
            case('fresh_controller_restores_draft_and_commits',accepted=resumed['accepted_ids'],setup_count=len(setup_count))
            entry=action({'action':'read_entry','entry_id':'blocker'})
            fixed=action({'action':'edit_proof','entry_id':'blocker','expected_hash':entry['expected_hash'],'proof_body':'by simp'})
            if native_restart:
                # Kill only this fixture's already started native container,
                # before publishing a candidate request. This exercises real
                # process loss/cleanup/typed task transport, not a fake verdict.
                # Scheduler requeue policy is covered separately by unit tests;
                # here a fresh controller executes the concrete resumed work.
                import subprocess
                from regen.execution.batch_checker import BatchWorker
                from regen.execution.check_tasks import RecoverableNativePreparationError
                original_transaction=BatchWorker.transaction
                killed=[]
                def lose_worker(worker, kind, *args, **kwargs):
                    if kind=='check' and not killed:
                        require(worker.root.resolve().is_relative_to(root), 'foreign fault-injection worker')
                        require(worker.name.startswith('regen-batch-'), 'unexpected worker identity')
                        subprocess.run(['docker','kill',worker.name],check=True,capture_output=True,timeout=30)
                        worker.process.wait(timeout=30)
                        killed.append(worker)
                    return original_transaction(worker,kind,*args,**kwargs)
                with patch.object(BatchWorker,'transaction',lose_worker):
                    try:
                        action({'action':'check_theory','source_sha256':fixed['source_sha256'],'entry_ids':['blocker']})
                    except RecoverableNativePreparationError as error:
                        recovery=error.read_receipt(root)
                        durable(root/'native-recovery-control.json',{'receipt':error.record(),'evidence':recovery},immutable=True)
                    else:
                        raise AssertionError('native worker exit did not retain typed recovery evidence')
                require(len(killed)==1 and killed[0].closed, 'failed native worker was not disposed')
                after_loss=replay_uncached(root,config,packages['Switch_A'])
                require(after_loss['proofs']==before['proofs'],'worker loss changed certified proofs')
                require(after_loss['batch_requests']==2,'failed request cost was lost or doubled')
                controller.close();controller=create('Switch_A')
                restored=read()
                require(restored['document']['source_sha256']==fixed['source_sha256'],'worker loss changed saved draft')
                require(set(restored['accepted_ids'])=={'first','last'},'worker loss lost GREEN prefix')
                case('native_worker_exit_preserves_draft_and_green',kind=recovery['kind'],
                     incident_id=recovery['incident_id'],failed_requests_preserved=True)
            repaired=action({'action':'check_theory','source_sha256':fixed['source_sha256'],'entry_ids':['blocker']})
            require(repaired['feedback']['status']=='accepted','pending contract repair failed')
            completed=action({'action':'check_theory','source_sha256':fixed['source_sha256'],'entry_ids':['dependent']})
            require(completed['feedback']['status']=='accepted','new GREEN predecessor unavailable to dependent')
            final=replay_uncached(root,config,packages['Switch_A'])
            require(set(final['proofs'])==set(proofs),'not all synthetic contracts completed')
            require(final['batch_requests']==3+int(native_restart),'recovery reset or duplicated charged requests')
            case('repair_and_dependent_recheck',accepted_ids=list(final['proofs']),batch_requests=final['batch_requests'])
            report['public_tool_calls']=serial;report['synthetic_contracts_green']=5
            report['runtime_setup_order']=setup_count
            report['pass']=True
    except BaseException as error:
        report['failure']={'error_type':type(error).__name__,'diagnostic':str(error)}
        raise
    finally:
        errors=[]
        for item in (controller,executor,resources):
            if item is not None:
                try:item.close()
                except BaseException as error:errors.append(str(error))
        report['wall_seconds']=time.monotonic()-started
        if errors:report.update(pass_=False,cleanup_errors=errors);report['pass']=False
        durable(root/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--generation-strategy',choices=['baseline','sketch-reviewed-v1','sketch-reviewed-v2'],default='sketch-reviewed-v1')
    parser.add_argument('--native-restart',action='store_true',help='Kill this synthetic native worker once and check recovery')
    args=parser.parse_args()
    result=run(args.dataset,args.output,generation_strategy=args.generation_strategy,native_restart=args.native_restart)
    print(json.dumps({'pass':result['pass'],'wall_seconds':result['wall_seconds'],'cases':len(result['cases'])}))
