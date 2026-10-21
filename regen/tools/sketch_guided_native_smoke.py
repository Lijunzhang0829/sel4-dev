"""Small synthetic HOL admission/repair test; no model or benchmark proof access."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from regen.execution.proof_commits import durable
from regen.planning.declaration_index import initial_index
from regen.planning.sketch_assessment import assess_sketch, read_failure
from regen.planning.sketch_check import authorize, check_groups, strategy_options
from regen.planning.sketch_workflow import probe_helpers, review_sketch, diagnose_failure, record_formal_result
from regen.planning.theory_document import (DocumentStore, block, candidate, check_document,
                                           selected_source, validate_document_commit)
from regen.tools.sketch_native_smoke import LocalHOLChecker, LocalHOLWorker


def run(output, executable, *, phased=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    checker=LocalHOLChecker(output,Path(executable).resolve())
    package={'theory':'Guided_Demo','file_slot':'Guided_Demo.thy','contracts':[
        {'contract_id':k,'name':k,'exact_statement':s,'contract_owned_context':[]}
        for k,s in [('parent','lemma parent: "(n::nat) + 0 = n & (m::nat) = m"'),
                    ('clone_parent','lemma clone_parent: "P --> P"'),
                    ('fold_parent','lemma fold_parent: "fold (\\<lambda>x a. a + (x::nat)) xs 0 = sum_list xs"')]]}
    state={'graph':initial_index(package,run_id='synthetic-guided-native'),
           'proofs':{},'accepted_order':[],'status':'generating'}
    store=DocumentStore(output/'document',package,state,checker.imports(),**strategy_options('sketch-phased-v1' if phased else 'sketch-guided-v1'))
    nodes={n['id']:n for n in state['graph']['nodes']}
    helper={'id':'zero','role':'helper','context':[], 'statement':'lemma zero: "(n::nat) + 0 = n"'}
    clone={'id':'clone','role':'helper','context':[], 'statement':'lemma clone: "Q --> Q"'}
    fold={'id':'fold_acc','role':'helper','context':[],
          'statement':'lemma fold_acc: "fold (\\<lambda>x a. a + (x::nat)) xs a = a + sum_list xs"'}
    source=store.read()['source'].replace(block(nodes['parent']),
        block(helper,'by simp')+block(nodes['parent'],'using zero[where n=n] by simp')).replace(
        block(nodes['clone_parent']),block(clone,'by assumption')+
        block(nodes['clone_parent'],'using clone[where Q=P] by assumption')).replace(
        block(nodes['fold_parent']),block(fold,'by simp')+
        block(nodes['fold_parent'],'using fold_acc[where xs=xs and a=0] by simp'))
    groups=[{'target_entry_id':target,'helper_ids':[key],
             'use_sites':[{'helper_id':key,'consumer_line':1,'fact':fact}]}
            for target,key,fact in [('parent','zero','zero[where n=n]'),
                                    ('clone_parent','clone','clone[where Q=P]'),
                                    ('fold_parent','fold_acc','fold_acc[where xs=xs and a=0]')]]
    saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                      'source':source,'sketches':groups},{})
    report={'pass':False,'model_calls':0,'benchmark_contracts_proved':0,
            'scope':'synthetic HOL authority/structure/repair validation',
            'difficulty_certified':False}
    def row(key,probe=None):
        result={'helper_id':key,'use_indices':[0],'domain_scope':'local','premises':'local',
                'conclusion':'local','generalization':'none','burden':'isolated',
                'reduction':'Prove the exact required local proposition.'}
        if probe:result['probe_id']=probe
        return result
    try:
        with patch('regen.execution.batch_checker.BatchWorker',LocalHOLWorker):
            before=deepcopy(state)
            closed=check_groups(checker,store,saved['source_sha256'])
            assert closed['status']=='conditional_closed',closed
            receipts={r['target_entry_id']:r['receipt_id'] for r in closed['groups']}
            assessed=assess_sketch(checker,store,saved['source_sha256'])
            durable(output/'assessment.json',assessed,immutable=True)
            records={r['target_entry_id']:r for r in assessed['results']}
            assert 'same_obligation' in records['clone_parent']['helpers'][0]['risks'],assessed
            assert state==before
            report['native_alpha_normalized_clone_detected']=True
            report['assessment_did_not_publish_or_change_state']=True
            zero_signal=records['parent']['helpers'][0]
            assert not zero_signal['risks'],zero_signal
            review_sketch(store,saved['source_sha256'],'parent',receipts['parent'],[row('zero')])
            authorize(store,source,['zero'])
            report['local_leaf_admitted_without_mandatory_probe']=True
            failed=probe_helpers(checker,store,saved['source_sha256'],['clone'])['results'][0]
            assert failed['status']=='proof_failed',failed
            try:
                review_sketch(store,saved['source_sha256'],'clone_parent',receipts['clone_parent'],
                              [row('clone',failed['probe_id'])])
            except ValueError as error:
                assert 'risky leaf' in str(error),error
            else:
                raise AssertionError('isolated label plus failed probe bypassed the gate')
            report['repeated_parent_with_failed_probe_rejected']=True
            details=read_failure(store,failed['failure_id'])
            durable(output/'failure-details.json',details,immutable=True)
            assert details['current'] and details['check']['verdict']=='red'
            diagnose_failure(store,failed['failure_id'],'script','Use implication introduction for the exact failed tautology.')
            try:
                store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                    'source':source.replace('lemma clone: "Q --> Q"','lemma clone: "True"')},{})
            except ValueError as error:
                assert 'local proof repair' in str(error),error
            else:
                raise AssertionError('interface changed before local repair')
            fixed=source.replace(block(clone,'by assumption'),block(clone,'by blast'))
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],'source':fixed},{})
            probe=probe_helpers(checker,store,saved['source_sha256'],['clone'])['results'][0]
            assert probe['status']=='proved_in_probe',probe
            review_sketch(store,saved['source_sha256'],'clone_parent',receipts['clone_parent'],[row('clone',probe['probe_id'])])
            assert 'iteration_requires_invariant' in records['fold_parent']['helpers'][0]['risks']
            fold_row={**row('fold_acc'),'domain_scope':'generalized','generalization':'induction'}
            try:
                review_sketch(store,saved['source_sha256'],'fold_parent',receipts['fold_parent'],[fold_row])
            except ValueError as error:
                assert 'concrete induction/reuse' in str(error),error
            else:
                raise AssertionError('induction label bypassed the mechanism requirement')
            failed_fold=probe_helpers(checker,store,saved['source_sha256'],['fold_acc'])['results'][0]
            assert failed_fold['status']=='proof_failed',failed_fold
            diagnose_failure(store,failed_fold['failure_id'],'script',
                             'Induct over the list while keeping the accumulator arbitrary.')
            fixed=fixed.replace(block(fold,'by simp'),
                block(fold,'by (induction xs arbitrary: a) (simp_all add: add.assoc)'))
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],'source':fixed},{})
            if phased:
                from regen.planning.sketch_phases import transition_proof
                transition_proof(store,saved['source_sha256'],failed_fold['failure_id'],
                                 'formal_leaf','Continue with the saved induction proof under the same interface.')
                fold_row['probe_id']=failed_fold['probe_id']
                report['failed_risky_probe_promoted_without_green']=not bool(state['proofs'])
            else:
                fold_probe=probe_helpers(checker,store,saved['source_sha256'],['fold_acc'])['results'][0]
                assert fold_probe['status']=='proved_in_probe',fold_probe
                fold_row['probe_id']=fold_probe['probe_id']
            fold_row['generalization_evidence']={
                'variable':'xs; accumulator a remains arbitrary',
                'invariant':'fold of the remaining suffix equals accumulator plus suffix sum',
                'base_case':'empty suffix returns the accumulator',
                'step_case':'apply induction hypothesis to the updated accumulator; reassociate addition'}
            review_sketch(store,saved['source_sha256'],'fold_parent',receipts['fold_parent'],[fold_row])
            report['iteration_screened_and_useful_induction_generalization_verified']=True
            authorize(store,fixed,['zero','clone','fold_acc'])
            assert not state['proofs']
            projected=selected_source(package,state,fixed,checker.imports(),['zero','clone','fold_acc'])
            new=candidate(package,state,projected,checker.imports())
            evidence=check_document(checker,state,new,projected)
            validate_document_commit(package,state,new,evidence)
            record_formal_result(store,projected,evidence)
            durable(output/'helpers-check.json',evidence,immutable=True)
            state=new;store.state=state
            report['local_repair_then_three_helpers_formally_checked']=True
            projected=selected_source(package,state,fixed,checker.imports(),['parent','clone_parent','fold_parent'])
            new=candidate(package,state,projected,checker.imports())
            evidence=check_document(checker,state,new,projected)
            validate_document_commit(package,state,new,evidence)
            durable(output/'parents-check.json',evidence,immutable=True)
            assert set(new['proofs'])=={'zero','clone','fold_acc','parent','clone_parent','fold_parent'}
            report['three_synthetic_parents_reassembled_without_admissions']=True
            report['pass']=True
    finally:
        checker.close_prefix()
        durable(output/'result.json',report,immutable=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--isabelle',type=Path,default=Path(__file__).resolve().parents[2]/'verification/isabelle/bin/isabelle')
    parser.add_argument('--phased',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(args.output,args.isabelle,phased=args.phased),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
