"""Model-free serial production-path acceptance; synthetic goals only.

Creates a fresh, self-contained tiny repository. No retired experiment is read.
Private heap copies are removed after the test, while evidence is retained.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable, freeze_evidence, digest
from regen.execution.repository_execution import journal
from regen.execution.repository_integration import integrate_repository
from regen.execution.seal_evidence import validate_seal
from regen.infrastructure.incremental_log import enable, FORMAT
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.batch_file_planner import seal_theory
from regen.planning.theory_document import render, candidate, check_document
from regen.preparation.checker_context import load_context
from regen.tests.current.test_proof_commits import fixture


def require_native_receipt(check, *, name):
    """Check the new common metadata against embedded immutable native bytes."""
    receipt=check.get('native_receipt')
    if not isinstance(receipt,dict) or receipt.get('schema')!='native-check-receipt-v1':
        raise AssertionError(name+' lacks a common native check receipt')
    if receipt.get('returncode')!=check.get('returncode') or receipt.get('verdict')!=check.get('verdict'):
        raise AssertionError(name+' native receipt changed the checker verdict')
    log=check.get('log_content')
    if not isinstance(log,str) or receipt.get('log_sha256')!=hashlib.sha256(log.encode()).hexdigest():
        raise AssertionError(name+' native receipt log hash differs')
    if not isinstance(receipt.get('source_sha256'),str) or len(receipt['source_sha256'])!=64:
        raise AssertionError(name+' native receipt lacks source identity')
    return receipt


def run(dataset, output, *, proof_bodies=None, native_memory_mb=16384,
        native_threads=4, native_cpus=4, and_or_helper_selection=False,
        shared_host_lock_dir=None, shared_host_slots=1):
    # Validate caps before creating a run or copying any heaps. This lets the
    # same full acceptance path qualify constrained hosts without monkeypatching
    # the real native resource policy.
    resources = NativeResources(1, native_threads, 1, memory_mb=native_memory_mb,
                                cpus=native_cpus,
                                shared_host_lock_dir=shared_host_lock_dir,
                                shared_host_slots=shared_host_slots)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = load_context(Path(dataset))
    enable(output)
    package, initial = fixture()
    initial['graph']['run_id'] = 'serial-acceptance'
    package['package_payload_sha256'] = digest(package)
    listing = {'files':[{'theory':package['theory'], 'file_slot':package['file_slot']}]}
    config = dict(run_id='serial-acceptance', execution_mode='repository-projection-v1',
        execution_input_kind='refine-dataset', checker_context_root=str(Path(dataset).resolve()),
        checker_context_id=context['checker_context_id'], implementation={},
        contract_package_dir=str(output/'packages'),
        session_schedule={'file_list_sha256':digest(listing)},
        premise_snapshot=str(context['premise_audit_context_path']),
        isabelle_dir=str(context['isabelle']), l4v=str(context['l4v']),
        semantic_workspace=str(context['semantic_workspace']),
        complete_context_policy='arch-reuse-v1', log_storage=FORMAT,
        **{key:context[key] for key in ('image','base_session','base_import','base_catalog')})
    durable(output/'run.json', config)
    durable(output/'execution-input.json', {'file_list':listing,'files':listing['files'],
        'package_hashes':{package['file_slot']:package['package_payload_sha256']}})
    durable(output/'packages'/(package['file_slot']+'.json'),package)
    durable(output/'heap-copy-budget.json',clone_heaps(context['heaps'],output/'heaps'))
    checker = IsabelleChecker(output,output/'checks',generated_dir=output/'generated',
        heaps_dir=output/'heaps',l4v_dir=context['l4v'],
        semantic_workspace=context['semantic_workspace'],isabelle_dir=context['isabelle'],
        image=context['image'],base_session=context['base_session'],base_import=context['base_import'],
        base_catalog=context['base_catalog'],timeout=300,complete_context_policy='arch-reuse-v1')
    checker.checker_context_id=context['checker_context_id']
    token = CURRENT_RESOURCES.set(resources)
    report = {'passed':False,'model_calls':0,'synthetic_goals_only':True,
              'method_adapter_acceptance':False,'cases':[], 'resource_policy':resources.policy}
    started = time.monotonic()
    def note(name, **result):
        report['cases'].append({'name':name,**result})
        print(json.dumps(report['cases'][-1]),flush=True)
    try:
        log = journal(output,config,package)
        query = checker.resolve_symbols(['True'])
        if query['True']['status'] != 'resolved':
            raise AssertionError('native symbol query failed')
        durable(output/'symbol-query.json',query)
        note('native_symbol_query',status='resolved')
        check = freeze_evidence(checker.check_declarations(initial['graph']))
        require_native_receipt(check,name='initial declarations')
        log.append('initial',initial,{'check':check})
        source = render(package,initial,checker.imports())
        old = initial
        def selection(expected):
            from regen.planning.helper_selection import recommend
            from regen.planning.contract_and_or import SCHEMA
            problem = {'schema_version':1, 'method':SCHEMA, 'root_goal':'contract', 'goals':[
                {'id':'contract','role':'contract','routes':[
                    {'id':'direct','requires':[],'estimate_seconds':100},
                    {'id':'via-helper','requires':['helper'],'estimate_seconds':1}]},
                {'id':'helper','role':'helper','routes':[
                    {'id':'prove-helper','requires':[],'estimate_seconds':1}]}]}
            advice = recommend(problem, package, old['graph']['nodes'], old['proofs'], log.replay())
            decision = advice['decision']
            actual = decision.get('goal_id', decision['action'])
            if actual != expected:
                raise AssertionError('AND/OR did not select expected local frontier: '+str(advice))
            note('and_or_selection', expected=expected, advice=advice)
        if and_or_helper_selection:
            selection('helper')
        bodies = proof_bodies or {'helper':'by simp','contract':'using helper by (rule conjunct1)'}
        for name, body, success in [('helper',bodies['helper'],True),
                                    ('invalid_contract','by fail',False),
                                    ('contract',bodies['contract'],True)]:
            trial_source = source.replace('oops',body,1)
            new = candidate(package,old,trial_source,checker.imports())
            evidence = check_document(checker,old,new,trial_source)
            require_native_receipt(evidence['check'],name=name)
            evidence.update(sealable_generation=True,provenance='synthetic-native-acceptance-only')
            if success:
                log.append('document',new,evidence)
                old,source = new,trial_source
            else:
                if evidence['check']['verdict']=='green':
                    raise AssertionError('invalid proof passed native check')
                try:
                    log.append('document',new,evidence)
                except ValueError:
                    pass
                else:
                    raise AssertionError('invalid proof accepted')
            if journal(output,config,package).replay()!=old:
                raise AssertionError('fresh replay changed committed GREEN')
            note(name,verdict=evidence['check']['verdict'],accepted=list(old['proofs']))
            if and_or_helper_selection:
                selection('SOLVED' if name=='contract' else 'contract')
        directory=output/'theories'/package['theory']
        sealed,evidence=seal_theory(output,config,package,directory,checker,[],old)
        if sealed is None:
            raise ValueError('seal failed: '+str(evidence))
        require_native_receipt(evidence['build'],name='seal build')
        log.append('seal',sealed,evidence)
        if journal(output,config,package).replay()!=sealed:
            raise AssertionError('seal replay differs')
        note('complete_audit_build_seal',status=sealed['status'])
        for key in ('complete','dependencies','build'):
            bad=deepcopy(evidence)
            bad[key]['returncode']=124
            changed=deepcopy(sealed)
            changed['seal_id']='theory-seal:'+digest(bad)[:20]
            try:
                validate_seal(changed,bad,runtime=config)
            except ValueError:
                note('reject_rehashed_'+key, rejected=True)
            else:
                raise AssertionError('nonzero native returncode accepted')
        integrated=integrate_repository(output,timeout=300)
        if not integrated['repository_verified']:
            raise ValueError('integration failed: '+str(integrated))
        note('independent_repository_integration',repository_verified=True)
        report['passed']=True
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        try:
            resources.close()
        finally:
            CURRENT_RESOURCES.reset(token)
            report['elapsed_seconds']=time.monotonic()-started
            # Only copies created inside this fresh acceptance output are removed.
            heaps=[output/'heaps',*sorted((output/'integration').glob('*/heaps'))]
            for path in heaps:
                if path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(output):
                    shutil.rmtree(path)
            report['private_heaps_removed']=[str(path.relative_to(output)) for path in heaps]
            durable(output/'result.json',report)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--native-memory-mb',type=int,default=16384)
    parser.add_argument('--native-threads',type=int,default=4)
    parser.add_argument('--native-cpus',type=float,default=4)
    parser.add_argument('--shared-host-lock-dir',type=Path)
    parser.add_argument('--shared-host-slots',type=int,default=1)
    parser.add_argument('--and-or-helper-selection',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(args.dataset,args.output,native_memory_mb=args.native_memory_mb,
        native_threads=args.native_threads,native_cpus=args.native_cpus,
        shared_host_lock_dir=args.shared_host_lock_dir,
        shared_host_slots=args.shared_host_slots,
        and_or_helper_selection=args.and_or_helper_selection),indent=2))


if __name__=='__main__':
    main()
