"""Model-free, real-Isabelle shadow acceptance for VERO parent/child seals.

This synthetic fixture uses the frozen local checker dataset. It never writes
to a formal experiment run and does not invoke a model or the VERO launcher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest.mock import patch

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.preparation.checker_context import load_context


PARENT = 'Vero_Dependency_Parent_Smoke'
CHILD = 'Vero_Dependency_Child_Smoke'
PARENT_ID = 'c0001'
CHILD_ID = 'c0002'
GIB = 1024 ** 3


class Stop:
    """Only the budget publication methods needed for this native fixture."""
    def __init__(self, ledger):
        self.ledger = ledger
        self.event = threading.Event()
        self.theories = {PARENT:{}, CHILD:{}}
        self.sealed = set()
        self.selected = None

    def check(self):
        if self.event.is_set():
            raise RuntimeError('native smoke cancelled')

    def select(self, theory):
        self.selected = theory

    def observe(self, *args, **kwargs):
        pass

    def mark_sealed(self, theory):
        self.sealed.add(theory)

    def invalidate_seal(self, theory, **data):
        self.ledger.append('seal_invalidated', theory=theory, **data)
        self.sealed.discard(theory)

    def begin_exclusion(self, reason):
        return reason

    def end_exclusion(self, handle):
        pass


def theory_source(theory, contract, imports, proof='by simp', *, dynamic_imports=False):
    if dynamic_imports:
        extra=imports.replace('Vero_Support','').strip()
        header=(f'theory {theory}\nimports Vero_Support\n'
            f'(* VERO START {theory} imports *)\n{extra}\n'
            f'(* VERO END {theory} imports *)\nbegin\n')
    else:
        header=f'theory {theory}\nimports {imports}\nbegin\n'
    return (header+
        f'vero_prove "{contract}" {contract}_prove: "True"\n{proof}\n'
        f'vero_disprove "{contract}" {contract}_disprove: "False"\noops\n'
        'end\n')


def configure_vero_source(path):
    """Bind the fixture to an explicit VERO release, not ambient PYTHONPATH."""
    root=Path(path).resolve()
    package=next((candidate for candidate in (root/'src',root)
        if (candidate/'vero/isabelle/checker.py').is_file()
        and (candidate/'vero/isabelle/Vero_Support.thy').is_file()),None)
    if package is None:
        raise ValueError('VERO source lacks src/vero/isabelle/checker.py and Vero_Support.thy')
    sys.path.insert(0,str(package))
    return package


def verify_native(stage, result, rendered, theory, contract, *, blocked, sealed):
    expected_source = (rendered / (theory + '.thy')).read_bytes()
    native = result['native_evidence']
    source = Path(native['source']).read_bytes()
    log = Path(native['log']).read_bytes()
    receipt = native.get('native_receipt')
    source_files=(receipt.get('source_files_sha256')
        if isinstance(receipt,dict) else None)
    source_files_digest=(hashlib.sha256(json.dumps(source_files,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()
        if isinstance(source_files,dict) else None)
    marker = f'VERO_RESULT {contract} prove clean'.encode()
    allowed={PARENT_ID+':prove'} if theory==PARENT else {
        PARENT_ID+':prove', CHILD_ID+':prove'}
    # A cold check of the child also loads the local parent, so the full log
    # legitimately includes both VERO_RESULT markers. Require this theory's
    # marker while rejecting any ID outside the effective import closure.
    if (native['returncode'] != 0 or contract + ':prove' not in result['audited']
            or not set(result['audited'])<=allowed
            or source != expected_source or marker not in log
            or not isinstance(receipt, dict)
            or receipt.get('check_scope') != 'cold-full-theory'
            or not isinstance(source_files,dict)
            or receipt.get('source_sha256') != source_files_digest
            or source_files.get('candidate/'+theory+'.thy') != hashlib.sha256(source).hexdigest()
            or any(source_files.get('generated/'+path.name)
                != hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(native['source']).parent.glob('*.thy'))
            or receipt.get('log_sha256') != hashlib.sha256(log).hexdigest()):
        raise AssertionError(f'{stage}: native contract receipt/audit mismatch')
    manifest = json.loads(Path(result['source_manifest']).read_text())
    if any(manifest[name] != hashlib.sha256(path.read_bytes()).hexdigest()
            for path in rendered.glob('*.thy') for name in [path.name]):
        raise AssertionError(f'{stage}: source manifest mismatch')
    if sealed:
        build = result['build_evidence']
        build_receipt = build.get('native_receipt') if isinstance(build,dict) else None
        if (result['status'] != 'ok' or result['seal_status'] != 'sealed'
                or result['certification_blocked_by']
                or not isinstance(build_receipt,dict)
                or build_receipt.get('check_scope') != 'generated-session-build'
                or build_receipt.get('returncode') != 0
                or build_receipt.get('log_sha256') != hashlib.sha256(
                    Path(build['log']).read_bytes()).hexdigest()):
            raise AssertionError(f'{stage}: independent seal build mismatch')
    elif (result['status'] != 'blocked_dependency'
            or result['seal_status'] != 'waiting_for_imports'
            or result['certification_blocked_by'] != blocked
            or result['build_evidence'] is not None):
        raise AssertionError(f'{stage}: dependency block mismatch')
    return {'stage':stage, 'theory':theory, 'status':result['status'],
        'audited':result['audited'], 'seal_status':result['seal_status'],
        'certification_blocked_by':result['certification_blocked_by'],
        'reused_exact_check':result['reused_exact_check'],
        'source_identity':result['source_identity'],
        'native_elapsed_seconds':result['native_elapsed_seconds'],
        'seal_elapsed_seconds':result['seal_elapsed_seconds'],
        'native_request_id':receipt['request_id'],
        'seal_request_id':build_receipt['request_id'] if sealed else None}


def run(dataset, support, vero_source, output, *, timeout=900,
        dynamic_imports=False):
    dataset = Path(dataset).resolve()
    support = Path(support).resolve()
    output = Path(output).resolve()
    vero_package=configure_vero_source(vero_source)
    if support != vero_package/'vero/isabelle/Vero_Support.thy':
        raise ValueError('support template must come from the selected VERO source release')
    context = load_context(dataset)
    if shutil.disk_usage(output.parent).free < 3 * GIB:
        raise RuntimeError('less than 3 GiB free; will not clone Isabelle heaps')
    output.mkdir(parents=True, exist_ok=False)
    rendered = output / 'rendered'
    rendered.mkdir()
    raw_support = support.read_text()
    if raw_support.count('IMPORT_BASE') != 1 or 'VERO_RESULT' not in raw_support:
        raise ValueError('expected unmodified VERO support template')
    (rendered / 'Vero_Support.thy').write_text(
        raw_support.replace('IMPORT_BASE', context['base_import']))
    (rendered / (PARENT + '.thy')).write_text(
        theory_source(PARENT, PARENT_ID, 'Vero_Support',
            dynamic_imports=dynamic_imports))
    (rendered / (CHILD + '.thy')).write_text(
        theory_source(CHILD, CHILD_ID, PARENT + ' Vero_Support',
            dynamic_imports=dynamic_imports))
    ledger = Ledger(output / 'ledger', 'vero-dependency-native-smoke')
    ledger.append('experiment_started',model_calls=0)
    benchmark = {'schema_version':2 if dynamic_imports else 1,
        'contracts':[{'id':PARENT_ID,'module':PARENT},
                              {'id':CHILD_ID,'module':CHILD}]}
    stop = Stop(ledger)
    observer = VeroObserver(ledger,stop,rendered,benchmark)
    report = {'schema_version':1,'model_calls':0,'formal_experiment':False,
        'dynamic_imports':dynamic_imports,
        'dataset':str(dataset),'checker_context_id':context['checker_context_id'],
        'vero_source':str(vero_package),
        'support_sha256':hashlib.sha256(raw_support.encode()).hexdigest(),
        'output':str(output),'stages':[],'passed':False}
    native_calls = []
    seal_calls = []
    original_check = IsabelleChecker._check
    original_build = IsabelleChecker.build_generated_session

    def native(checker, *args, **kwargs):
        native_calls.append(time.monotonic())
        return original_check(checker,*args,**kwargs)

    def seal(checker, *args, **kwargs):
        seal_calls.append(time.monotonic())
        return original_build(checker,*args,**kwargs)

    def stage(checker, label, theory, contract, *, blocked=None, sealed=True,
              native_calls_expected=1,seal_calls_expected=None):
        before_native,before_seal=len(native_calls),len(seal_calls)
        print(label+': start',flush=True)
        result=checker.check(rendered,theory)
        row=verify_native(label,result,rendered,theory,contract,
                          blocked=blocked,sealed=sealed)
        row['new_native_calls']=len(native_calls)-before_native
        row['new_seal_build_calls']=len(seal_calls)-before_seal
        if seal_calls_expected is None:seal_calls_expected=int(sealed)
        if (row['new_native_calls']!=native_calls_expected
                or row['new_seal_build_calls']!=seal_calls_expected):
            raise AssertionError(f'{label}: unexpected native/build invocation count')
        report['stages'].append(row)
        print(json.dumps(row,sort_keys=True),flush=True)
        return result

    try:
        with patch.object(IsabelleChecker,'_check',native), \
                patch.object(IsabelleChecker,'build_generated_session',seal), \
                VeroCheckerAdapter(dataset,output/'native',timeout=timeout,
                    observer=observer) as checker:
            checker.set_frozen_plan({'imports':{PARENT:[],CHILD:[] if dynamic_imports else [PARENT]},
                'plan_sha256':hashlib.sha256(b'dependency-smoke-plan').hexdigest(),
                'allocation_sha256':hashlib.sha256(b'dependency-smoke-allocation').hexdigest(),
                'imports_sha256':hashlib.sha256(b'dependency-smoke-imports').hexdigest()})
            if dynamic_imports:
                stage(checker,'child_unsealed_dynamic_parent',CHILD,CHILD_ID,
                    blocked=[PARENT],sealed=False)
            stage(checker,'parent_initial',PARENT,PARENT_ID)
            stage(checker,'child_initial',CHILD,CHILD_ID)
            if dynamic_imports:
                cached=stage(checker,'child_exact_reuse',CHILD,CHILD_ID,
                    native_calls_expected=0,seal_calls_expected=0)
                if not cached['reused_exact_check'] or not cached['seal_reused']:
                    raise AssertionError('unchanged dynamic closure was not reused')
            if stop.sealed != {PARENT,CHILD}:
                raise AssertionError('initial parent and child were not both sealed')

            (rendered / (PARENT+'.thy')).write_text(
                theory_source(PARENT,PARENT_ID,'Vero_Support','by blast',
                    dynamic_imports=dynamic_imports))
            stage(checker,'child_after_parent_change',CHILD,CHILD_ID,
                  blocked=[PARENT],sealed=False)
            invalidations=[r['data'] for r in ledger.rows
                           if r['kind']=='seal_invalidated']
            expected=[(PARENT,'own_source_changed'),
                      (CHILD,'import_source_changed')]
            if ([(r['theory'],r['reason']) for r in invalidations]!=expected
                    or any(r.get('changed_sources')!=[PARENT+'.thy']
                           for r in invalidations)
                    or stop.sealed):
                raise AssertionError('parent edit did not invalidate both seals')
            blocked_summary=experiment_summary(ledger)
            if (blocked_summary['current_valid_contract_ids']
                    or blocked_summary['current_valid_sealed_theories']
                    or blocked_summary['historical_first_green_contract_ids']
                       != sorted([PARENT_ID,CHILD_ID])):
                raise AssertionError('blocked-child ledger projection mismatch')
            # Check that durable receipts replay to the same revoked state.
            replay=VeroObserver(Ledger(output/'ledger',ledger.run_id),
                                Stop(ledger),rendered,benchmark)
            if replay.seals:
                raise AssertionError('replayed observer restored an invalid seal')
            report['invalidation']={'theories':[r['theory'] for r in invalidations],
                'reasons':[r['reason'] for r in invalidations],
                'changed_sources':[r['changed_sources'] for r in invalidations],
                'historical_first_green_contract_ids':
                    blocked_summary['historical_first_green_contract_ids'],
                'current_valid_contract_ids':
                    blocked_summary['current_valid_contract_ids']}

            stage(checker,'parent_resealed',PARENT,PARENT_ID)
            parent_summary=experiment_summary(ledger)
            if (parent_summary['current_valid_contract_ids'] != [PARENT_ID]
                    or parent_summary['current_valid_sealed_theories'] != [PARENT]):
                raise AssertionError('parent-only recertification mismatch')
            stage(checker,'child_resealed',CHILD,CHILD_ID)
            final=experiment_summary(ledger)
            if (final['current_valid_contract_ids']!=sorted([PARENT_ID,CHILD_ID])
                    or final['current_valid_sealed_theories']!=sorted([PARENT,CHILD])
                    or final['historical_first_green_contract_ids']
                       !=sorted([PARENT_ID,CHILD_ID])):
                raise AssertionError('final parent/child recertification mismatch')
            report['final']={key:final[key] for key in (
                'historical_first_green_contract_ids','current_valid_contract_ids',
                'current_valid_sealed_theories')}
            if dynamic_imports:
                (rendered/(PARENT+'.thy')).unlink()
                try:checker.check(rendered,CHILD)
                except ValueError as error:
                    if 'closure source absent' not in str(error):raise
                else:raise AssertionError('missing dynamic parent was accepted')
                absent=experiment_summary(ledger)
                if (stop.sealed or absent['current_valid_contract_ids']
                        or absent['current_valid_sealed_theories']):
                    raise AssertionError('missing dynamic parent left current GREEN or seal')
                report['source_missing']={'current_valid_contract_ids':
                    absent['current_valid_contract_ids'],
                    'current_valid_sealed_theories':
                    absent['current_valid_sealed_theories']}
        report['native_calls']=len(native_calls)
        report['seal_build_calls']=len(seal_calls)
        if (report['native_calls']!=(6 if dynamic_imports else 5)
                or report['seal_build_calls']!=4):
            raise AssertionError('aggregate native/build call count mismatch')
        report['passed']=True
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        durable(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--vero-source',type=Path,required=True,
        help='VERO release root or its src directory; must contain vero/isabelle/checker.py')
    parser.add_argument('--support',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--dynamic-imports',action='store_true',
        help='exercise additive VERO imports outside the frozen base DAG')
    args=parser.parse_args()
    report=run(args.dataset,args.support,args.vero_source,args.output,
        timeout=args.timeout,dynamic_imports=args.dynamic_imports)
    print(json.dumps(report,indent=2,sort_keys=True))


if __name__=='__main__':
    main()
