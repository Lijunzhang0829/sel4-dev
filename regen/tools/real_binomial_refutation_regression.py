"""Maintenance-only real old Binomial refutation via public tools and native Isabelle.

Two original contracts are copied exactly. The sole knowledge intervention is an
allowlisted helper theory containing positive operational lemmas and a concrete
input witness, never a final negative theorem. Action provenance is explicitly
scripted; native checking, receipt validation, and journal replay are genuine.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time
from unittest.mock import patch
from uuid import uuid4
from jsonschema import Draft202012Validator

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.contract_refutation import (OWNED_CONTEXT_POLICY, check_export_scan,
    contract_binding, validate_refutation_certificate)
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.partial_theory_export import build_export_scope, export_graph
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal
from regen.execution.result_scope import environment_bundle, contract_environment_view
from regen.planning.parallel_file_planner import TheoryController, tools
from regen.planning.theory_outcomes import current_dispositions, pending_contract_ids
from regen.preparation.checker_context import load_context
from regen.tools.document_acceptance_validation import (MODEL, THEORY, ScriptedProvenance,
    readonly_heaps, replay_uncached, require)


def run(dataset: Path, fixture: Path, output: Path, *, resume=False):
    root=output.resolve()
    if not resume:root.mkdir(parents=True,exist_ok=False)
    else:
        require((root/'run.json').is_file(),'maintenance resume requires frozen run')
        resume_index=1+len(list(root.glob('report-before-resume*.json')))
        shutil.copyfile(root/'report.json',root/('report-before-resume.json' if resume_index==1 else f'report-before-resume-{resume_index:03d}.json'))
    dataset=dataset.resolve(); fixture=fixture.resolve(); started=time.monotonic()
    (root/(f'continuation-source-{resume_index:03d}.py' if resume else 'validation-source.py')).write_bytes(Path(__file__).read_bytes())
    if not resume:shutil.copytree(fixture,root/'fixture')
    else:
        for name in ('contracts.json','Real_Refutation_Helpers.thy','negative-proof.isar'):
            require((fixture/name).read_bytes()==(root/'fixture'/name).read_bytes(),'resume fixture changed: '+name)
    contracts=json.loads((fixture/'contracts.json').read_text())
    original=json.loads((dataset/'repository-planning-input.json').read_text())['contracts']
    for contract in contracts:
        require(contract==next(c for c in original if c['contract_id']==contract['contract_id']),
                'maintenance target differs from original fixed contract')
    require([c['name'] for c in contracts]==['spec_0054','spec_0023'],'unexpected real targets')
    negative_id,positive_id=[c['contract_id'] for c in contracts]
    proof=(fixture/'negative-proof.isar').read_text()
    helper=(fixture/'Real_Refutation_Helpers.thy').read_text()
    forbidden=['lemma regression_aux_undo_alias_failure','closed_c0047','bn_v2_undo_storage','sorry','oops','axiomatization']
    require(not any(x in helper for x in forbidden),'forbidden helper input')
    context=load_context(dataset)
    helper_hash=hashlib.sha256(helper.encode()).hexdigest()
    context_id='maintenance-context:'+digest({'parent':context['checker_context_id'],'helper_sha256':helper_hash})
    resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
    executor=CheckExecutor(root/'check-tasks',workers=1)
    marker=CURRENT_RESOURCES.set(resources)
    controller=scan_checker=None
    report={'passed':False,'model_calls':0,'benchmark_contracts_proved':0,'benchmark_credit':0,
        'scope':'maintenance-only original spec_0054 refutation and spec_0023 continuation',
        'substitutions':['isolated scripted runtime and action provenance',
            'allowlisted native-checked positive helpers and concrete historical input witness'],
        'excluded':'No old final negative theorem or revised target input is imported',
        'original_checker_context_id':context['checker_context_id'],'maintenance_checker_context_id':context_id,
        'resource_policy':resources.policy,'helper_sha256':helper_hash,'cases':[]}
    previous_seconds=0
    if resume:
        report=json.loads((root/'report.json').read_text());previous_seconds=report['wall_seconds']
        error=report.pop('error',None)
        report.setdefault('initial_fixture_failure',error)
        report.setdefault('fixture_failures',[]).append(error)
        report['maintenance_continuation']=True
        report['resume_attempt']=resume_index
    def save():
        report['wall_seconds']=round(previous_seconds+time.monotonic()-started,3);durable(root/'report.json',report)
    def case(name,**data):
        report['cases'].append({'name':name,'passed':True,**data});save()
        print(json.dumps({'case':name,'passed':True}),flush=True)
    def checker_at(directory):
        work=directory/'isabelle';work.mkdir(parents=True,exist_ok=True)
        (work/'Real_Refutation_Helpers.thy').write_text(helper)
        checker=IsabelleChecker(root,work,image=context['image'],architecture=context['architecture'],
            timeout=180,heaps_dir=context['heaps'],l4v_dir=context['l4v'],
            semantic_workspace=context['semantic_workspace'],base_session=context['base_session'],
            base_import='Real_Refutation_Helpers',base_catalog=context['base_catalog'],
            isabelle_dir=context['isabelle'],generated_dir=directory/'generated')
        original_command=checker._command
        def command(*a,**kw):
            cmd=readonly_heaps(original_command(*a,**kw));at=cmd.index('-l')
            cmd[at:at]=['-o','parallel_proofs=0'];return cmd
        checker._command=command;checker.checker_context_id=context_id
        return checker
    try:
        if resume:
            config=json.loads((root/'run.json').read_text())
            package=json.loads((root/'packages'/(THEORY+'.thy.json')).read_text())
            require(package['contracts']==contracts,'resume contract package changed')
            contract_environment_view(config,contracts[0])
        else:
            scan_checker=checker_at(root/'preparation')
            scan=check_export_scan(scan_checker,contracts,root/'fresh-old-contract-scope',result_scope=OWNED_CONTEXT_POLICY)
            require(scan['passed'],'fresh original-contract scope scan failed')
            require('REAL_REFUTATION_HELPERS_CLEAN' in Path(scan['native_check']['log']).read_text(),
                    'helper native audit did not run')
            bundle=environment_bundle(contracts,scan,context_id)
            durable(root/'environment-evidence.json',bundle,immutable=True)
            case('old_target_fresh_scope_binding',old_contract=contract_binding(contracts[0]),
                environment_id=bundle['contracts'][negative_id]['environment_id'],
                native_negative=scan['records'][negative_id]['negative_obligation_text'],
                fixed_environment_status='not_established',used_revised_statement=False)
            scan_checker.close_prefix();scan_checker=None
            package={'theory':THEORY,'file_slot':THEORY+'.thy','repository_projection':{'nodes':[]},
                     'contracts':deepcopy(contracts)}
            package['package_payload_sha256']=digest(package)
            listing={'files':[{'theory':THEORY,'file_slot':package['file_slot'],
                             'contract_ids':[negative_id,positive_id],'status':'pending','generation_ids':[]}]}
            repository=Path(__file__).resolve().parents[2]
            implementation={str(p.relative_to(repository)):hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ('execution','planning','infrastructure','analysis')
                for p in (repository/'regen'/folder).iterdir() if p.suffix in {'.py','.ML','.sh'}}
            config={'run_id':'real-refutation-maintenance-'+uuid4().hex,
                'execution_mode':'repository-projection-v1','execution_input_kind':'refine-dataset',
                'execution_models':{'File Planner':MODEL},'implementation':implementation,
                'contract_package_dir':str(root/'packages'),
                'session_progress_ledger':str(root/'session-progress-ledger.json'),
                'session_schedule':{'file_list_sha256':digest(listing),'plan_id':'maintenance-real-refutation'},
                'validation_only':True,'model_calls':0,'base_import':'Real_Refutation_Helpers',
                'checker_context_id':context_id,'contract_outcome_policy':'contract-dispositions-v1',
                'unresolved_evidence_policy':'proof-or-refutation-v1','result_scope':OWNED_CONTEXT_POLICY,
                'environment_evidence':{'bundle_sha256':bundle['bundle_sha256'],
                    'path':str(root/'environment-evidence.json'),'checker_context_id':context_id}}
            contract_environment_view(config,contracts[0])
            durable(root/'run.json',config,immutable=True)
            durable(root/'execution-input.json',{'file_list':listing,'files':listing['files'],
                'package_hashes':{package['file_slot']:package['package_payload_sha256']}},immutable=True)
            durable(root/'packages'/(package['file_slot']+'.json'),package,immutable=True)
            (root/'generated').mkdir()
        def setup(requested_root,theory,timeout):
            require(Path(requested_root).resolve()==root and theory==THEORY,'foreign setup')
            directory=root/'theories'/THEORY;directory.mkdir(parents=True,exist_ok=True)
            for name,value in [('imports.json',[]),('sealed-premises.json',{'records':[]})]:
                if not (directory/name).exists():durable(directory/name,value,immutable=True)
            checker=checker_at(directory);checker.declaration_environment=digest(config)
            return config,package,directory,journal(root,config,package),checker,[]
        scripted=ScriptedProvenance(root);schemas={t['name']:Draft202012Validator(t['inputSchema']) for t in tools(True)}
        serial=max([int(p.name.split('-')[0]) for p in (root/'tool-calls').glob('*-request.json')],default=0)
        with ExitStack() as stack:
            native_init=IsabelleChecker.__init__
            def cold_fixture_init(checker,*args,**kwargs):
                native_init(checker,*args,**kwargs)
                require(checker.work_dir.resolve().is_relative_to(root),'foreign maintenance checker')
                # This audit helper is a source extension, not a prebuilt heap.
                # Use the existing native cold-batch path; no proof result or
                # journal validator is substituted.
                checker.batch_incremental=False
            stack.enter_context(patch.object(IsabelleChecker,'__init__',cold_fixture_init))
            report['positive_native_mode']='existing cold batch path; audit helper is not installed in resident heap'
            native_command=IsabelleChecker._command
            def stage_fixture_command(checker,*args,**kwargs):
                # Production creates isolated per-task and per-entry work dirs.
                # Stage only this immutable maintenance helper at each such
                # path; do not alter imports, proof rendering or native result.
                require(checker.work_dir.resolve().is_relative_to(root),'foreign maintenance checker')
                checker.work_dir.mkdir(parents=True,exist_ok=True)
                helper_path=checker.work_dir/'Real_Refutation_Helpers.thy'
                if helper_path.exists():require(hashlib.sha256(helper_path.read_bytes()).hexdigest()==helper_hash,'helper source changed')
                else:helper_path.write_text(helper)
                return readonly_heaps(native_command(checker,*args,**kwargs))
            stack.enter_context(patch.object(IsabelleChecker,'_command',stage_fixture_command))
            if 'immutable helper source staging into isolated checker directories' not in report['substitutions']:
                report['substitutions'].append('immutable helper source staging into isolated checker directories')
            stack.enter_context(patch('regen.execution.repository_execution.setup',side_effect=setup))
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',side_effect=scripted.record))
            stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact',side_effect=scripted.validate))
            stack.enter_context(patch('regen.planning.batch_file_planner.call_llm',side_effect=AssertionError('model forbidden')))
            def create():return TheoryController(root,THEORY,resources=resources,executor=executor,
                document_mode=True,model=MODEL,check_timeout=180,candidate_timeout=60,generation_strategy_name='baseline')
            def call(tool,args):
                nonlocal serial
                schemas[tool].validate(args);serial+=1
                metadata={'thread_id':'maintenance-thread','turn_id':'maintenance-turn','call_id':str(serial)}
                durable(root/'tool-calls'/f'{serial:03d}-request.json',{'tool':tool,'arguments':args},immutable=True)
                response=controller.handle(tool,args,metadata)
                durable(root/'tool-calls'/f'{serial:03d}-result.json',response,immutable=True)
                return response
            def action(proposal):return call('proof_action',{'theory':THEORY,'proposal':proposal})
            controller=create()
            before=call('read_theory',{'theory':THEORY})
            if not resume:
                failed=action({'action':'check_refutation','entry_id':negative_id,'proof_body':'by fail'})
                require(failed['status']=='not_refuted' and not failed['theory_stop'],'failed negative stopped work')
                require(current_dispositions(controller)=={},'failed proof created a disposition')
                require(negative_id in pending_contract_ids(controller),'failed negative target no longer pending')
                case('failed_real_negative_keeps_pending',status=failed['status'])
            result=action({'action':'check_refutation','entry_id':negative_id,'proof_body':proof})
            require(result['status']=='context_refuted','actual fixed-contract negative failed: '+str(result.get('diagnostic',result))[-2000:])
            require(result['contract_stop'] and not result['theory_stop'],'negative stopped siblings')
            require(result['remaining_contract_ids']==[positive_id],'wrong remaining contract set')
            dispositions=current_dispositions(controller);outcome=dispositions[negative_id]
            certificate=outcome['details']['certificate'];native_checker=controller._runtime()[4]
            validate_refutation_certificate(certificate,contracts[0],native_checker,
                expected_result_scope=OWNED_CONTEXT_POLICY)
            require(certificate['contract']==contract_binding(contracts[0]),'original fixed target changed')
            durable(root/'negative-certificate.json',certificate,immutable=True)
            state=replay_uncached(root,config,package)
            require(state is None or negative_id not in state['proofs'],'negative fact became positive')
            case('real_negative_certificate_and_receipt',certificate_id=certificate['certificate_id'],
                disposition_event=outcome['receipt']['event_id'],oracles=certificate['audit']['oracles'],
                skipped=certificate['audit']['skip_proof'],result_scope=certificate['result_scope'],
                remaining=result['remaining_contract_ids'],strict_global_refutation=False)
            controller.close();controller=create()
            require(current_dispositions(controller)[negative_id]['receipt']==outcome['receipt'],
                    'cold controller lost or changed negative receipt')
            require(pending_contract_ids(controller)==[positive_id],'replay lost independent pending goal')
            case('negative_survives_new_controller_replay')
            entry=action({'action':'read_entry','entry_id':positive_id})
            changed=action({'action':'edit_proof','entry_id':positive_id,'expected_hash':entry['expected_hash'],
                            'proof_body':'by (simp add: bytes_frame_def)'})
            checked=action({'action':'check_theory','source_sha256':changed['source_sha256'],'entry_ids':[positive_id]})
            require(positive_id in checked.get('accepted_entry_ids',[]),'independent real contract not accepted')
            state=replay_uncached(root,config,package);require(set(state['proofs'])=={positive_id},'wrong positive proof set')
            require(not pending_contract_ids(controller),'pending target remained after independent positive')
            case('independent_real_goal_after_refutation',contract='spec_0023',accepted_entry_ids=list(state['proofs']))
            dispositions=current_dispositions(controller);native_checker=controller._runtime()[4]
            scope=build_export_scope(package,state,dispositions,runtime=config,expected_imports=list(native_checker.imports()))
            projected=export_graph(state)
            require(scope['omitted_node_ids']==[negative_id] and scope['exported_node_ids']==[positive_id],
                    'partial export did not exclude negative fixed target')
            source=native_checker.complete_source(projected,state['proofs'],theory_name='Maintenance_Positive_Export')
            require('lemma spec_0054' not in source and 'lemma spec_0023' in source,'negative declaration leaked to export')
            native=native_checker.check_complete(projected,state['proofs'])
            require(native['verdict']=='green','positive-only export failed native loading')
            durable(root/'partial-export-scope.json',scope,immutable=True)
            durable(root/'partial-export-native.json',native,immutable=True)
            (root/'Maintenance_Positive_Export.thy').write_text(source)
            case('partial_export_projection_native_checked',omitted=scope['omitted_node_ids'],
                exported=scope['exported_node_ids'],published_seal=False)
            report['passed']=True;save();return report
    except BaseException as error:
        report['error']={'type':type(error).__name__,'message':str(error)};save();raise
    finally:
        if controller is not None:controller.close()
        if scan_checker is not None:scan_checker.close_prefix()
        executor.close();CURRENT_RESOURCES.reset(marker);resources.close()
        report['resource_counters']={'active':len(resources.active),'residents':len(resources.residents),'closed':resources.closed,'operations':len(resources.metrics)};save()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='Continue only this maintenance fixture, retaining its frozen run and initial failure evidence')
    args=parser.parse_args();result=run(args.dataset,args.fixture,args.output,resume=args.resume)
    print(json.dumps({'passed':result['passed'],'wall_seconds':result['wall_seconds'],'cases':len(result['cases'])}))
