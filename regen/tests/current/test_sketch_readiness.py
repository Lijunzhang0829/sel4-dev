"""Workflow navigation preserves authority and explains only actual gates."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from regen.planning.sketch_check import certificate_status,load_groups
from regen.planning.sketch_readiness import entry_authorization,entry_readiness,readiness_view
from regen.planning.theory_document import parse
from regen.tests.current import test_sketch_workflow as fixtures


class SketchReadinessTests(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.WorkflowTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.store=self.base.store

    def context(self):
        source=self.store.path.read_text()
        entries,proofs=parse(source,self.store.package,self.store.imports)
        return source,{n['id']:n for n in entries},proofs

    def ready(self):
        self.base.review([self.base.row(self.base.probe())])
        self.base.save(self.store.path.read_text().replace('oops','by simp'))

    def test_missing_review_has_exact_native_receipt_and_requires_agent_judgment(self):
        row=entry_readiness(self.store,'helper')
        self.assertEqual(row['status'],'blocked_workflow')
        self.assertIn('review_missing',[r['code'] for r in row['missing_prerequisites']])
        action=next(a for a in row['next_actions'] if a['request']['action']=='review_sketch')
        self.assertEqual(action['request']['receipt_id'],self.base.closed['groups'][0]['receipt_id'])
        self.assertEqual(action['requires_agent_fields'],['reviews'])
        self.assertNotIn('reviews',action['request'])

    def test_proof_only_edit_reuses_certificate_and_review(self):
        self.ready();source,nodes,proofs=self.context()
        info=certificate_status(self.store,nodes,proofs,load_groups(self.store)[0])
        self.assertEqual(info['status'],'valid')
        self.assertFalse(info['helper_proof_changes_require_recheck'])
        self.assertEqual(entry_authorization(self.store,source,'helper')['status'],'ready')
        view=readiness_view(self.store)
        self.assertEqual(view['entries']['helper']['status'],'ready')
        self.assertEqual(view['groups'][0]['next_actions'],[])

    def test_consumer_edit_reports_precise_cause_and_executable_selector(self):
        self.base.save(self.store.path.read_text().replace('using helper by simp','using helper by blast'))
        row=entry_readiness(self.store,'helper')
        self.assertIn('consumer_proof_changed',[r['code'] for r in row['missing_prerequisites']])
        action=next(a for a in row['next_actions'] if a['request']['action']=='check_sketch')
        self.assertEqual(action['requires_agent_fields'],[])
        self.assertEqual(action['request']['target_entry_ids'],['contract'])
        self.assertEqual(action['request']['source_sha256'],self.store.read()['source_sha256'])

    def test_changed_helper_interface_is_distinct_from_consumer_edit(self):
        self.base.save(self.store.path.read_text().replace('"True & True"','"True"'))
        row=entry_readiness(self.store,'helper')
        codes={r['code'] for r in row['missing_prerequisites']}
        self.assertIn('helper_interface_changed',codes)
        self.assertNotIn('consumer_proof_changed',codes)

    def test_compatible_green_prefix_extension_does_not_force_recheck(self):
        self.ready()
        self.base.state['proofs']['helper']='by simp'
        self.base.state['accepted_order']=['helper']
        source,nodes,proofs=self.context()
        info=certificate_status(self.store,nodes,proofs,load_groups(self.store)[0])
        self.assertEqual(info['status'],'valid')
        self.assertEqual(info['reuse'],'compatible_prefix_extension')
        self.assertEqual(entry_readiness(self.store,'helper')['status'],'accepted')

    def test_changed_imports_has_context_cause(self):
        _,nodes,proofs=self.context()
        self.store.checked_imports=['Other.Semantic_Root']
        info=certificate_status(self.store,nodes,proofs,load_groups(self.store)[0])
        self.assertIn('imports_changed',[r['code'] for r in info['reasons']])

    def test_new_receipt_reports_review_binding_refresh(self):
        self.ready()
        signature=self.base.closed['groups'][0]['sketch_sha256']
        (self.store.root/'sketch-certificates'/(signature+'.json')).unlink()
        from regen.planning.sketch_check import check_groups
        check_groups(self.base.checker,self.store,self.store.read()['source_sha256'])
        row=entry_readiness(self.store,'helper')
        self.assertIn('review_receipt_changed',[r['code'] for r in row['missing_prerequisites']])

    def test_corrupt_certificate_is_not_local_workflow_block(self):
        self.ready()
        signature=self.base.closed['groups'][0]['sketch_sha256']
        path=self.store.root/'sketch-certificates'/(signature+'.json')
        record=json.loads(path.read_text());record['audit_policy']='tampered'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,'integrity'):
            entry_authorization(self.store,self.store.path.read_text(),'helper')

    def test_unknown_authorization_errors_are_not_swallowed(self):
        for message in ('foreign global source binding','structural assessment integrity mismatch'):
            with self.subTest(message=message),patch('regen.planning.sketch_readiness.authorize',side_effect=ValueError(message)):
                with self.assertRaisesRegex(ValueError,message):
                    entry_authorization(self.store,self.store.path.read_text(),'helper')

    def test_typed_semantic_block_preserves_exact_next_action(self):
        from regen.planning.sketch_semantics import WorkflowBlocked
        next_actions=[{'request':{'action':'check_theory','source_sha256':'frozen','entry_ids':['critical']},
                       'requires_agent_fields':[]}]
        error=WorkflowBlocked('critical_helper_unproved','critical route assumption still pending',
                              details={'critical_entry_ids':['critical']},next_actions=next_actions)
        with patch('regen.planning.sketch_readiness.authorize',side_effect=error):
            result=entry_authorization(self.store,self.store.path.read_text(),'helper')
        self.assertEqual(result['status'],'blocked_workflow')
        self.assertEqual(result['next_actions'],next_actions)
        self.assertEqual(result['gate_details']['critical_entry_ids'],['critical'])

    def test_real_v2_critical_gate_is_actionable_without_native_recheck(self):
        from regen.planning.theory_document import block
        from regen.planning.sketch_check import check_groups
        from regen.planning.sketch_workflow import review_sketch
        from regen.tests.current.test_sketch_semantic_review import SemanticReviewTests
        self.store.require_semantic_review=True;self.store.allow_formal_leaf=True
        critical=block({'id':'critical','role':'helper','context':[],
                        'statement':'lemma critical: "True"'})
        source=self.base.source.replace('(* REGEN-BEGIN ',critical+'(* REGEN-BEGIN ',1).replace(
            'using helper by simp','using critical helper by simp')
        group={'target_entry_id':'contract','helper_ids':['critical','helper'],
               'use_sites':[{'helper_id':'critical','consumer_line':1,'fact':'critical'},
                            {'helper_id':'helper','consumer_line':1,'fact':'helper'}]}
        saved=self.base.save(source,[group])
        checked=check_groups(self.base.checker,self.store,saved['source_sha256'])
        row=SemanticReviewTests.row(self)
        reviewed=review_sketch(self.store,saved['source_sha256'],'contract',
            checked['groups'][0]['receipt_id'],
            [{**deepcopy(row),'helper_id':'critical','helper_role':'classification','use_indices':[0]},
             {**deepcopy(row),'use_indices':[1]}])
        view=readiness_view(self.store)
        blocked=view['entries']['helper']
        self.assertEqual(blocked['status'],'blocked_workflow')
        self.assertEqual(blocked['gate_details']['critical_entry_ids'],['critical'])
        action=blocked['next_actions'][0]
        self.assertEqual(action['request'],{'action':'check_theory',
            'source_sha256':saved['source_sha256'],'entry_ids':['critical']})
        self.assertEqual(action['requires_agent_fields'],[])
        self.assertEqual(view['entries']['critical']['status'],'open_proof_slot')
        review=view['groups'][0]['review']
        self.assertEqual(review['helper_feasibility'],reviewed['helper_feasibility'])
        self.assertEqual(review['helper_reviews'][0]['helper_role'],'classification')
        self.assertEqual(review['helper_reviews'][0]['proof_mode'],'formal')
        self.assertFalse(review['semantic_claims_verified'])
        self.assertFalse(review['reasons'])

    def test_formal_mode_validates_supplied_probe_after_policy_change(self):
        from regen.tests.current.test_sketch_semantic_review import SemanticReviewTests
        self.store.require_semantic_review=True;self.store.allow_formal_leaf=True
        row=SemanticReviewTests.row(self)
        row['probe_id']=self.base.probe()
        self.base.review([row])
        with patch('regen.planning.sketch_workflow.PROBE_AUDIT_POLICY','next-probe-policy'):
            result=entry_readiness(self.store,'helper')
            review=readiness_view(self.store)['groups'][0]['review']
        self.assertEqual(result['status'],'blocked_workflow')
        self.assertEqual(review['helper_feasibility']['helper'],'unestablished')
        self.assertEqual(review['review_helper_feasibility']['helper'],'proved_in_probe')
        reason=next(r for r in result['missing_prerequisites'] if r['code']=='probe_audit_policy_changed')
        self.assertEqual(reason['entry_id'],'helper')
        action=next(a for a in result['next_actions'] if a['request']['action']=='probe_helpers')
        self.assertEqual(action['request']['entry_ids'],['helper'])
        self.assertEqual(action['requires_agent_fields'],[])

    def test_current_feasibility_advances_after_acceptance_without_mutating_review(self):
        from regen.tests.current.test_sketch_semantic_review import SemanticReviewTests
        from regen.planning.sketch_workflow import _read
        self.store.require_semantic_review=True;self.store.allow_formal_leaf=True
        row=SemanticReviewTests.row(self);row['helper_role']='classification'
        reviewed=self.base.review([row])
        original=_read(self.store,'reviews',reviewed['review_id'])
        self.assertEqual(original['helper_feasibility']['helper'],'unestablished')
        self.base.save(self.store.path.read_text().replace('oops','by simp'))
        self.base.state['proofs']['helper']='by simp'
        self.base.state['accepted_order']=['helper']
        review=readiness_view(self.store)['groups'][0]['review']
        self.assertEqual(review['helper_feasibility']['helper'],'proved')
        self.assertEqual(review['review_helper_feasibility']['helper'],'unestablished')
        self.assertEqual(_read(self.store,'reviews',reviewed['review_id']),original)

    def test_failed_helper_requires_target_bound_agent_diagnosis(self):
        from regen.planning.sketch_workflow import record_formal_result
        self.ready()
        source=self.store.path.read_text().replace(
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\nby simp',
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\nby fail')
        self.base.save(source)
        record_formal_result(self.store,source,{'check':{'verdict':'red','diagnostic':'local rule mismatch'},
            'node_results':[{'node_id':'helper','status':'failed'}]})
        result=entry_readiness(self.store,'helper')
        reason=next(r for r in result['missing_prerequisites'] if r['code']=='diagnosis_required')
        self.assertEqual(reason['entry_id'],'helper')
        action=next(a for a in result['next_actions'] if a['request']['action']=='diagnose_failure')
        self.assertEqual(action['request']['failure_id'],reason['failure_id'])
        self.assertEqual(action['requires_agent_fields'],['classification','plan'])
        self.assertNotIn('classification',action['request'])
