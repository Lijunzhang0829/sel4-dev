"""VERO keeps scoped native proof receipts across unrelated RED drafts."""
import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver


def receipt(source, uid, *, context='c'*64):
    start=f'(* VERO START {uid} prove *)'
    end=f'(* VERO END {uid} prove *)'
    begin=source.index(start)
    body_start=begin+len(start)
    body_end=source.index(end,body_start)
    body=source[body_start:body_end].strip()
    sha=lambda value:hashlib.sha256(value.encode()).hexdigest()
    return {'polarity':'prove','filled':body not in ('','oops'),
        'pre_proof_sha256':sha(source[:begin]),
        'proof_body_sha256':sha(body),
        'prefix_sha256':sha(source[:body_end+len(end)]),
        'context_identity':context}


def source(bodies):
    return ('theory A imports Main begin\n'+''.join(
        f'(* VERO START {uid} prove *)\n{body}\n(* VERO END {uid} prove *)\n'
        for uid,body in bodies.items())+'end\n')


class Stop:
    def __init__(self):self.event=threading.Event()
    def select(self,theory):pass
    def observe(self,*args,**kwargs):pass
    def mark_sealed(self,theory):pass


class VeroContractReceiptRetentionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.ledger=Ledger(self.root/'ledger','scoped-receipts')
        self.ids=('c0001','c0002','c0003')
        self.bench={'schema_version':2,'benchmark_id':'b'*64,
            'contracts':[{'id':uid,'module':'A'} for uid in self.ids]}
        self.stop=Stop()
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        self.observer=VeroObserver(self.ledger,self.stop,self.workspace,self.bench)
        checker=ModuleType('vero.isabelle.checker')
        checker.strip_comments=lambda text:text
        modules=patch.dict(sys.modules,{'vero.isabelle.checker':checker})
        modules.start();self.addCleanup(modules.stop)

    def check(self, source_text, *, audited=(), status='ok'):
        (self.workspace/'A.thy').write_text(source_text)
        receipts={uid:receipt(source_text,uid) for uid in self.ids}
        result={'status':status,'contract_status':status,
            'audited':[uid+':prove' for uid in audited],
            'contract_receipts':receipts,'native_evidence':{'returncode':0 if status=='ok' else 1},
            'elapsed_seconds':.1,'source_identity':'a'*64}
        self.observer.checked('A',result,source_text)
        return receipts

    def test_later_red_draft_retains_prior_green_and_cannot_create_new_green(self):
        initial=source({'c0001':'by simp','c0002':'by auto','c0003':'oops'})
        original=self.check(initial,audited=self.ids[:2])
        first_ids={uid:self.observer.accepted_origins[uid] for uid in self.ids[:2]}
        changed=source({'c0001':'by simp','c0002':'by auto',
            'c0003':'apply simp'})
        candidates={uid:receipt(changed,uid) for uid in self.ids}
        self.assertEqual(candidates['c0001'],original['c0001'])
        self.assertEqual(candidates['c0002'],original['c0002'])
        self.observer.reconcile_contracts('A',{'A':candidates})
        self.check(changed,status='build_error')
        current=experiment_summary(self.ledger)
        self.assertEqual(current['current_valid_contract_ids'],list(self.ids[:2]))
        self.assertEqual(current['historical_first_green_contract_ids'],list(self.ids[:2]))
        self.assertEqual(self.observer.accepted_origins,first_ids)
        retained=[r for r in self.ledger.rows if r['kind']=='green_retained']
        self.assertEqual(retained[-1]['data']['target_ids'],list(self.ids[:2]))
        # The evaluator can report a new proved slot, but cannot itself
        # mint a GREEN without the earlier native receipt.
        self.observer.evaluated({'contracts':{uid:{'status':'proved'} for uid in self.ids},
            'modules':{'A':{'status':'build_error'}}})
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'],
            list(self.ids[:2]))
        restarted=VeroObserver(Ledger(self.root/'ledger','scoped-receipts'),
            Stop(),self.root/'workspace',self.bench)
        self.assertEqual(restarted.accepted_origins,first_ids)
        self.assertEqual(set(restarted.accepted_receipts),set(self.ids[:2]))

    def test_changed_prior_prefix_invalidates_only_affected_contract(self):
        original=source({'c0001':'by simp','c0002':'by auto','c0003':'oops'})
        self.check(original,audited=self.ids[:2])
        changed=original.replace('(* VERO START c0002 prove *)',
            'lemma new_helper: True by simp\n(* VERO START c0002 prove *)')
        receipts={uid:receipt(changed,uid) for uid in self.ids}
        self.observer.reconcile_contracts('A',{'A':receipts})
        self.assertEqual(set(self.observer.accepted_receipts),{'c0001'})
        self.check(changed,status='build_error')
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'],
            ['c0001'])
        invalidations=[r for r in self.ledger.rows if r['kind']=='contract_green_invalidated']
        self.assertEqual([r['data']['target_id'] for r in invalidations],['c0002'])

    def test_later_sibling_edit_revokes_theory_seal_but_not_prefix_receipts(self):
        original=source({'c0001':'by simp','c0002':'by auto','c0003':'oops'})
        self.check(original,audited=self.ids[:2])
        self.ledger.append('seal_result',theory='A',status='sealed',
            source_identity='a'*64,evidence={'returncode':0})
        self.ledger.append('seal_invalidated',theory='A',
            old_source_identity='a'*64,current_source_identity='b'*64,
            reason='own_source_changed',changed_sources=['A.thy'])
        summary=experiment_summary(self.ledger)
        self.assertEqual(summary['current_valid_contract_ids'],list(self.ids[:2]))
        self.assertEqual(summary['current_valid_sealed_theories'],[])

    def test_seal_failure_and_edit_transaction_keep_old_native_version(self):
        all_green=source({uid:'by simp' for uid in self.ids})
        self.check(all_green,audited=self.ids)
        artifact={'slots':{uid+':prove':{'found':True,'body':'by simp'}
            for uid in self.ids}}
        self.observer.guard_green_proofs(artifact)
        changed_artifact={'slots':{**artifact['slots'],
            'c0001:prove':{'found':True,'body':'by blast'}}}
        with self.assertRaisesRegex(ValueError,'outside seal'):
            self.observer.guard_green_proofs(changed_artifact)
        self.assertEqual(set(self.observer.accepted_receipts),set(self.ids))
        candidate=source({'c0001':'by blast','c0002':'by simp','c0003':'by simp'})
        receipts={uid:receipt(candidate,uid) for uid in self.ids}
        with self.assertRaisesRegex(ValueError,'outside seal'):
            self.observer.reconcile_contracts('A',{'A':receipts})
        self.assertEqual(set(self.observer.accepted_receipts),set(self.ids))
        transaction=self.observer.begin_seal_transaction('A',artifact)
        self.observer.guard_green_proofs(changed_artifact)
        self.observer.reconcile_contracts('A',{'A':receipts})
        self.check(candidate,audited=self.ids)
        self.assertEqual(self.observer.accepted_receipts['c0001']['proof_body_sha256'],
            receipt(all_green,'c0001')['proof_body_sha256'])
        self.check(candidate,status='build_error')
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'],
            list(self.ids))
        restarted=VeroObserver(Ledger(self.root/'ledger','scoped-receipts'),
            Stop(),self.root/'workspace',self.bench)
        self.assertEqual(restarted.seal_transactions,{'A'})
        self.assertEqual(set(restarted.accepted_receipts),set(self.ids))
        with self.assertRaisesRegex(ValueError,'transaction ID'):
            restarted.abort_seal_transaction('A','wrong',artifact)
        with self.assertRaisesRegex(ValueError,'outside seal'):
            restarted.abort_seal_transaction('A',transaction,changed_artifact,
                snapshot_dir=self.workspace)
        self.assertEqual(restarted.seal_transactions,{'A'})
        (self.workspace/'A.thy').write_text(all_green)
        restarted.abort_seal_transaction('A',transaction,artifact,
            snapshot_dir=self.workspace)
        self.assertEqual(restarted.seal_transactions,set())
        self.assertEqual(set(restarted.accepted_receipts),set(self.ids))

    def test_successful_native_seal_closes_transaction_and_replay_heals_tail(self):
        all_green=source({uid:'by simp' for uid in self.ids})
        self.check(all_green,audited=self.ids)
        artifact={'slots':{uid+':prove':{'found':True,'body':'by simp'}
            for uid in self.ids}}
        self.observer.begin_seal_transaction('A',artifact)
        candidate=source({'c0001':'by blast','c0002':'by simp','c0003':'by simp'})
        old=self.observer.accepted_receipts['c0001']
        self.check(candidate,audited=self.ids)
        self.assertEqual(self.observer.accepted_receipts['c0001'],old)
        identity='a'*64;evidence={'returncode':0}
        self.ledger.append('seal_result',theory='A',status='sealed',
            source_identity=identity,evidence=evidence)
        self.observer.seal_completed('A',{'seal_status':'sealed',
            'build_evidence':evidence,'source_identity':identity})
        self.assertEqual(self.observer.accepted_receipts['c0001'],
            receipt(candidate,'c0001'))
        self.assertEqual(experiment_summary(self.ledger)['historical_first_green_contract_ids'],
            list(self.ids))
        self.assertEqual(self.observer.seal_transactions,set())
        self.assertEqual([r['data']['status'] for r in self.ledger.rows
            if r['kind']=='seal_edit_end'],['sealed'])
        # Model a crash after seal_result but before seal_edit_end in another
        # valid ledger: replay must close exactly its still-active transaction.
        recovery=Ledger(self.root/'recovery','recovery')
        recovery.append('proof_commit',theory='A',nodes=[{'uid':uid,
            'kind':'contract','theory':'A','status':'green',
            'accepted_receipt':receipt(all_green,uid)} for uid in self.ids])
        recovery.append('seal_edit_begin',theory='A',transaction_id='txn',
            contract_ids=list(self.ids),base_receipts_sha256='b'*64)
        recovery.append('proof_commit',theory='A',source_identity=identity,
            nodes=[{'uid':uid,'kind':'contract','theory':'A','status':'green',
                'accepted_receipt':receipt(all_green,uid),
                'accepted_evidence_event_id':'old-native',
                'seal_candidate_receipt':receipt(candidate,uid),
                'disposition':'native_green_pending_theory_seal'} for uid in self.ids],
            evidence={'returncode':0})
        recovery.append('seal_result',theory='A',status='sealed',
            source_identity=identity,evidence=evidence)
        restarted=VeroObserver(Ledger(self.root/'recovery','recovery'),
            Stop(),self.root/'workspace',self.bench)
        self.assertEqual(restarted.seal_transactions,set())
        self.assertEqual(restarted.accepted_receipts['c0001'],receipt(candidate,'c0001'))
        self.assertEqual(len([r for r in restarted.ledger.rows if r['kind']=='proof_commit'
            and r['data'].get('authority')=='native-seal-candidate-promotion']),1)
        self.assertEqual([r['data']['status'] for r in restarted.ledger.rows
            if r['kind']=='seal_edit_end'],['sealed'])

    def test_deleted_local_seal_candidate_keeps_old_receipts_until_rollback(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        artifact={'slots':{uid+':prove':{'found':True,'body':'by simp'}
            for uid in self.ids}}
        transaction=self.observer.begin_seal_transaction('A',artifact,
            snapshot_dir=self.workspace)
        (self.workspace/'A.thy').unlink()
        self.assertEqual(self.observer.candidate_theories_for_snapshot(self.workspace),['A'])
        self.observer.source_missing({}, {'A':[]},'A',support_required=False)
        summary=experiment_summary(self.ledger)
        self.assertEqual(summary['current_valid_contract_ids'],list(self.ids))
        event=[r for r in self.ledger.rows if r['kind']=='source_missing'][-1]
        self.assertEqual(event['data']['preserved_contract_theories'],['A'])
        restarted=VeroObserver(Ledger(self.root/'ledger','scoped-receipts'),
            Stop(),self.workspace,self.bench)
        self.assertEqual(set(restarted.accepted_receipts),set(self.ids))
        (self.workspace/'A.thy').write_text(baseline)
        restarted.abort_seal_transaction('A',transaction,artifact,
            snapshot_dir=self.workspace)

    def test_invalid_local_candidate_preserves_old_but_external_change_revokes(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        (self.workspace/'Parent.thy').write_text('theory Parent imports Main begin end\n')
        artifact={'slots':{uid+':prove':{'found':True,'body':'by simp'}
            for uid in self.ids}}
        self.observer.begin_seal_transaction('A',artifact,snapshot_dir=self.workspace)
        (self.workspace/'A.thy').write_text('malformed own candidate\n')
        local=self.observer.candidate_theories_for_snapshot(self.workspace)
        self.assertEqual(local,['A'])
        self.observer.source_invalid(reason='malformed_local_candidate',
            requested_theory='A',candidate_theories=local)
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'],
            list(self.ids))
        (self.workspace/'Parent.thy').write_text('changed external context\n')
        self.assertEqual(self.observer.candidate_theories_for_snapshot(self.workspace),[])
        self.observer.source_invalid(reason='external_context_changed',
            requested_theory='A',candidate_theories=[])
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'],[])
        self.assertEqual(self.observer.accepted_receipts,{})

    def test_extraction_error_revokes_unchecked_prefix_and_import_consumers(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        # The proof bodies still match, so the raw edit guard alone cannot
        # detect a damaged earlier auxiliary marker that blocks extraction.
        damaged=baseline.replace('theory A imports Main begin',
            'theory A imports Main begin\n(* VERO START broken_aux *)')
        (self.workspace/'A.thy').write_text(damaged)
        artifact={'slots':{uid+':prove':{'found':True,'body':'by simp'}
            for uid in self.ids}}
        self.observer.guard_green_proofs(artifact,snapshot_dir=self.workspace)
        self.observer.source_extraction_invalid({'A':'unpaired marker'},
            {'A':[]},snapshot_dir=self.workspace)
        summary=experiment_summary(self.ledger)
        self.assertEqual(summary['current_valid_contract_ids'],[])
        self.assertEqual(summary['historical_first_green_contract_ids'],list(self.ids))
        restarted=VeroObserver(Ledger(self.root/'ledger','scoped-receipts'),
            Stop(),self.workspace,self.bench)
        self.assertEqual(restarted.accepted_receipts,{})

    def test_extraction_error_in_local_seal_candidate_retains_old_but_revokes_child(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        self.bench['contracts'].append({'id':'c0004','module':'B'})
        child=source({'c0004':'by simp'}).replace('theory A imports Main begin',
            'theory B imports A begin')
        (self.workspace/'B.thy').write_text(child)
        child_receipt=receipt(child,'c0004')
        row={'uid':'c0004','kind':'contract','theory':'B','status':'green',
            'accepted_receipt':child_receipt}
        accepted=self.ledger.append('proof_commit',theory='B',nodes=[row],
            evidence={'returncode':0})
        self.observer.nodes['B']=[row]
        self.observer.accepted_receipts['c0004']=child_receipt
        self.observer.accepted_origins['c0004']=accepted['event_id']
        artifact={'slots':{uid+':prove':{'found':True,'body':'by simp'}
            for uid in (*self.ids,'c0004')}}
        self.observer.begin_seal_transaction('A',artifact,snapshot_dir=self.workspace)
        (self.workspace/'A.thy').write_text(baseline.replace(
            'theory A imports Main begin','theory A imports Main begin\n(* VERO START broken_aux *)'))
        self.assertEqual(self.observer.candidate_theories_for_snapshot(self.workspace),['A'])
        affected=self.observer.source_extraction_invalid({'A':'unpaired marker'},
            {'A':[],'B':['A']},snapshot_dir=self.workspace)
        self.assertEqual(affected,['A','B'])
        summary=experiment_summary(self.ledger)
        self.assertEqual(summary['current_valid_contract_ids'],list(self.ids))
        self.assertEqual(summary['historical_first_green_contract_ids'],
            list((*self.ids,'c0004')))
        event=[r for r in self.ledger.rows if r['kind']=='source_extraction_invalid'][-1]
        self.assertEqual(event['data']['preserved_contract_theories'],['A'])
        restarted=VeroObserver(Ledger(self.root/'ledger','scoped-receipts'),
            Stop(),self.workspace,self.bench)
        self.assertEqual(set(restarted.accepted_receipts),set(self.ids))

    def test_known_invalid_import_slot_revokes_only_its_consumers(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        self.bench['contracts'].append({'id':'c0004','module':'B'})
        child=source({'c0004':'by simp'}).replace('theory A imports Main begin',
            'theory B imports A begin')
        child_receipt=receipt(child,'c0004')
        row={'uid':'c0004','kind':'contract','theory':'B','status':'green',
            'accepted_receipt':child_receipt}
        self.ledger.append('proof_commit',theory='B',nodes=[row],
            evidence={'returncode':0})
        self.observer.nodes['B']=[row]
        self.observer.accepted_receipts['c0004']=child_receipt
        affected=self.observer.source_invalid(reason='invalid additive VERO imports: B',
            requested_theory='B',invalid_theories=['B'],
            imports={'A':[],'B':['A']})
        self.assertEqual(affected,['B'])
        self.assertEqual(set(self.observer.accepted_receipts),set(self.ids))
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'],
            list(self.ids))
        replay=VeroObserver(Ledger(self.root/'ledger','scoped-receipts'),
            Stop(),self.workspace,self.bench)
        self.assertEqual(set(replay.accepted_receipts),set(self.ids))

    def test_unknown_import_graph_failure_is_conservative(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        self.bench['contracts'].append({'id':'c0004','module':'B'})
        # A missing graph node cannot prove that independent GREEN contexts
        # stayed unchanged, even when the caller names B as malformed.
        affected=self.observer.source_invalid(reason='invalid additive VERO imports: B',
            requested_theory='B',invalid_theories=['B'],imports={'B':[]})
        self.assertEqual(affected,['A','B'])
        self.assertEqual(self.observer.accepted_receipts,{})

    def test_post_native_source_change_revokes_published_green(self):
        baseline=source({uid:'by simp' for uid in self.ids})
        self.check(baseline,audited=self.ids)
        (self.workspace/'A.thy').write_text(baseline.replace('by simp','by auto',1))
        affected=self.observer.source_extraction_invalid(
            {'A.thy':'changed after native publication'}, {'A':[]},
            snapshot_dir=self.workspace,reason='source_changed_after_native_publication')
        self.assertEqual(affected,['A'])
        self.assertEqual(self.observer.accepted_receipts,{})
        event=[r for r in self.ledger.rows if r['kind']=='source_extraction_invalid'][-1]
        self.assertEqual(event['data']['reason'],'source_changed_after_native_publication')
        self.assertEqual(event['data']['failed_theories'],['A'])


if __name__=='__main__':unittest.main()
