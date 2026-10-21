"""Model-free native smoke for VERO's accepted-source proof projection.

This synthetic fixture is not a formal generation experiment. It creates a
fresh output, checks a later contract with an earlier OPEN slot, then submits
an invalid draft in that slot. Only the original native GREEN may survive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from regen.execution.proof_commits import durable
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.preparation.checker_context import load_context
from regen.tools.vero_dependency_native_smoke import Stop, configure_vero_source


THEORY = 'Vero_Accepted_Prefix_Smoke'
EARLIER = 'c0001'
LATER = 'c0002'


def source(earlier_proof, *, helper=''):
    def contract(uid, proof):
        return (f'vero_prove "{uid}" {uid}_prove: "True"\n'
            f'(* VERO START {uid} prove *)\n{proof}\n'
            f'(* VERO END {uid} prove *)\n'
            f'vero_disprove "{uid}" {uid}_disprove: "False"\n'
            f'(* VERO START {uid} disprove *)\noops\n'
            f'(* VERO END {uid} disprove *)\n')
    return (f'theory {THEORY}\nimports Vero_Support\n'
        f'(* VERO START {THEORY} imports *)\n\n'
        f'(* VERO END {THEORY} imports *)\nbegin\n'
        + contract(EARLIER, earlier_proof) + helper
        + contract(LATER, 'by simp') + 'end\n')


def run(dataset, vero_source, output, *, timeout=900, native_slots=1,
        shared_host_lock_dir=None, shared_host_slots=1):
    dataset=Path(dataset).resolve()
    package=configure_vero_source(vero_source)
    output=Path(output).resolve()
    context=load_context(dataset)
    if shutil.disk_usage(output.parent).free < 3 * 1024**3:
        raise RuntimeError('less than 3 GiB free for isolated native smoke')
    output.mkdir(parents=True,exist_ok=False)
    rendered=output/'rendered';rendered.mkdir()
    support=(package/'vero/isabelle/Vero_Support.thy').read_text()
    if support.count('IMPORT_BASE')!=1 or 'VERO_RESULT' not in support:
        raise ValueError('unexpected VERO support template')
    (rendered/'Vero_Support.thy').write_text(
        support.replace('IMPORT_BASE',context['base_import']))
    target=rendered/(THEORY+'.thy')
    target.write_text(source('oops'))
    ledger=Ledger(output/'ledger','vero-accepted-prefix-native-smoke')
    ledger.append('experiment_started',model_calls=0)
    bench={'schema_version':2,'benchmark_id':'b'*64,
        'contracts':[{'id':uid,'module':THEORY} for uid in (EARLIER,LATER)]}
    observer=VeroObserver(ledger,Stop(ledger),rendered,bench)
    report={'schema_version':1,'formal_experiment':False,'model_calls':0,
        'dataset':str(dataset),'checker_context_id':context['checker_context_id'],
        'output':str(output),'stages':[],'passed':False}
    try:
        with VeroCheckerAdapter(dataset,output/'native',timeout=timeout,
                observer=observer,native_slots=native_slots,
                shared_host_lock_dir=shared_host_lock_dir,
                shared_host_slots=shared_host_slots) as checker:
            checker.set_frozen_plan({'imports':{THEORY:[]},
                'plan_sha256':hashlib.sha256(b'accepted-prefix-smoke-plan').hexdigest(),
                'allocation_sha256':hashlib.sha256(b'accepted-prefix-smoke-allocation').hexdigest(),
                'imports_sha256':hashlib.sha256(b'accepted-prefix-smoke-imports').hexdigest()})
            print('initial_native: start',flush=True)
            initial=checker.check(rendered,THEORY)
            first=observer.accepted_receipts.get(LATER)
            first_origin=observer.accepted_origins.get(LATER)
            if (initial['native_evidence']['returncode']!=0
                    or initial['audited']!=[LATER+':prove']
                    or not isinstance(first,dict)
                    or EARLIER+':prove' not in first['open_prefix_slots']):
                raise AssertionError('initial later-contract native GREEN missing')
            report['stages'].append({'stage':'initial_native','status':initial['status'],
                'audited':initial['audited'],'source_identity':initial['source_identity'],
                'native_returncode':initial['native_evidence']['returncode']})

            target.write_text(source('apply (simp add: nonexistent_fact)'))
            print('failed_draft_native: start',flush=True)
            failed=checker.check(rendered,THEORY)
            candidate=failed['contract_receipts'][LATER]
            if (failed['native_evidence']['returncode']==0 or failed['audited']
                    or candidate['pre_proof_sha256']==first['pre_proof_sha256']
                    or candidate['prior_accepted_prefix_sha256']
                        !=first['accepted_prefix_sha256']
                    or observer.accepted_receipts.get(LATER)!=first
                    or observer.accepted_origins.get(LATER)!=first_origin
                    or experiment_summary(ledger)['current_valid_contract_ids']!=[LATER]):
                raise AssertionError('failed OPEN draft revoked or falsely renewed GREEN')
            report['stages'].append({'stage':'failed_draft_native',
                'status':failed['status'],'audited':failed['audited'],
                'source_identity':failed['source_identity'],
                'native_returncode':failed['native_evidence']['returncode'],
                'retained_green':LATER})

            target.write_text(source('apply (simp add: nonexistent_fact)',
                helper='lemma vero_aux_changed: "True" by simp\n'))
            candidates=checker._contract_receipts(THEORY,
                {THEORY+'.thy':target.read_bytes(),
                    'Vero_Support.thy':(rendered/'Vero_Support.thy').read_bytes()},
                {THEORY:()})
            observer.reconcile_contracts(THEORY,{THEORY:candidates})
            if (LATER in observer.accepted_receipts
                    or experiment_summary(ledger)['current_valid_contract_ids']):
                raise AssertionError('changed helper retained stale GREEN')
            report['stages'].append({'stage':'helper_changed_reconciliation',
                'retained_green':False,'native_calls':0})

            target.write_text(source('by simp'))
            print('completed_prefix_and_seal: start',flush=True)
            completed=checker.check(rendered,THEORY)
            renewed=observer.accepted_receipts.get(LATER)
            if (completed['native_evidence']['returncode']!=0
                    or completed['audited']!=[EARLIER+':prove',LATER+':prove']
                    or completed['seal_status']!='sealed'
                    or not isinstance(renewed,dict)
                    or renewed['pre_proof_sha256']==first['pre_proof_sha256']
                    or EARLIER+':prove' in renewed['open_prefix_slots']
                    or experiment_summary(ledger)['current_valid_contract_ids']
                        !=[EARLIER,LATER]):
                raise AssertionError('completed predecessor did not rebind and seal')
            report['stages'].append({'stage':'completed_prefix_and_seal',
                'status':completed['status'],'audited':completed['audited'],
                'seal_status':completed['seal_status'],
                'source_identity':completed['source_identity'],
                'native_returncode':completed['native_evidence']['returncode']})
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
    parser.add_argument('--vero-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--native-slots',type=int,default=1,choices=[1,2])
    parser.add_argument('--shared-host-lock-dir',type=Path)
    parser.add_argument('--shared-host-slots',type=int,default=1)
    args=parser.parse_args()
    print(json.dumps(run(args.dataset,args.vero_source,args.output,
        timeout=args.timeout,native_slots=args.native_slots,
        shared_host_lock_dir=args.shared_host_lock_dir,
        shared_host_slots=args.shared_host_slots),indent=2,sort_keys=True))


if __name__=='__main__':main()
