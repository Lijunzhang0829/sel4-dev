"""Admission regressions; native extraction is exercised by guided HOL smoke."""
from copy import deepcopy
import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET

from regen.planning.sketch_assessment import assess_sketch, read_failure
from regen.planning.sketch_check import authorize, check_groups, strategy_options
from regen.planning.sketch_workflow import diagnose_failure, workflow_view
from regen.tests.current import test_sketch_workflow as workflow_tests


class StructuralChecker(workflow_tests.DiagnosticChecker):
    retain = False
    same = False
    iteration = False
    unknown = False

    def check_sketch_source(self, source, prefix, admitted, *, timeout=None):
        marker = re.search(r'REGEN_STRUCTURE_BEGIN ([0-9a-f]{64})', source)
        if not marker:
            return super().check_sketch_source(source, prefix, admitted, timeout=timeout)
        self.last_admitted = admitted
        result = self._check(source, 'structure', timeout=timeout)
        target = bool(re.search(r'lemma (?:target|target_contract):', source[len(prefix)-len('end\n'):]))
        constants = ['Demo.read', 'Demo.update', 'Demo.branch'] if target or self.retain else ['Demo.read']
        if self.iteration and not target:
            constants.append('List.fold')
        root = ET.Element('profile')
        for key, value in [('canonical','parent' if target or self.same else 'leaf'),
                           ('term_size', '25' if target else '10'),
                           ('definition_size','0'), ('definition_count','0')]:
            ET.SubElement(root,key).text = value
        for field in ('constants','expanded_constants'):
            group = ET.SubElement(root,field)
            for name in constants:
                ET.SubElement(group,'constant').text = name
        signature = marker[1]
        log = ('REGEN_STRUCTURE_BEGIN '+signature+'\n'+ET.tostring(root,encoding='unicode')
               +'\nREGEN_STRUCTURE_END '+signature+'\n')
        result.update(log_content=log,log_sha256=hashlib.sha256(log.encode()).hexdigest())
        if self.unknown:
            result.update(verdict='red', returncode=124)
        return result


class GuidedTests(workflow_tests.WorkflowTests):
    # Run the old authority regressions too, with the original strategy untouched.
    def guided(self):
        self.store.require_assessment = True
        self.checker = StructuralChecker()

    def assess(self):
        return assess_sketch(self.checker,self.store,self.store.read()['source_sha256'],['contract'])

    def test_guided_missing_assessment_cannot_be_overridden_by_isolated(self):
        self.guided()
        with self.assertRaisesRegex(ValueError,'structural assessment'):
            self.review([self.row(self.probe())])

    def test_local_leaf_does_not_require_a_probe_and_no_proof_is_published(self):
        self.guided(); before=deepcopy(self.state)
        result=self.assess()
        row=self.row('unused');row.pop('probe_id')
        self.review([row]);authorize(self.store,self.source,['helper'])
        self.assertEqual(self.state,before)
        self.assertEqual(result['new_green'],[])
        self.assertFalse(result['difficulty_certified'])
        self.assertEqual(self.checker.last_admitted,[])

    def test_whole_burden_cannot_pass_with_failed_probe_or_label(self):
        self.guided();self.checker.retain=True
        result=self.assess()
        self.assertIn('main_operations_retained',result['results'][0]['helpers'][0]['risks'])
        self.checker.verdict='red';probe=self.probe()
        with self.assertRaisesRegex(ValueError,'risky leaf'):
            self.review([self.row(probe)])
        failure=workflow_view(self.store)['pending_failures'][0]['failure_id']
        diagnose_failure(self.store,failure,'script','Use a changed local proof for the exact obligation.')
        self.save(self.source.replace('oops','by blast'))
        self.checker.verdict='green'
        self.review([self.row(self.probe())])
        authorize(self.store,self.store.read()['source'],['helper'])

    def test_repeated_obligation_and_iteration_need_real_progress(self):
        for flag in ('same','iteration'):
            with self.subTest(flag=flag):
                self.guided();setattr(self.checker,flag,True);self.assess()
                row=self.row('unused');row.pop('probe_id')
                with self.assertRaisesRegex(ValueError,'risky leaf'):
                    self.review([row])

    def test_unknown_native_profile_is_not_treated_as_easy(self):
        self.guided();self.checker.unknown=True
        result=self.assess()
        self.assertIsNone(result['results'][0]['rank_key'])
        row=self.row('unused');row.pop('probe_id')
        with self.assertRaisesRegex(ValueError,'structure_unknown'):
            self.review([row])

    def test_generalization_enum_is_insufficient(self):
        self.guided();self.assess()
        row={**self.row(self.probe()),'domain_scope':'generalized','generalization':'induction'}
        with self.assertRaisesRegex(ValueError,'concrete induction/reuse'):
            self.review([row])
        row['generalization_evidence']={'variable':'remaining', 'invariant':'R after the processed prefix',
                                        'base_case':'empty suffix preserves R',
                                        'step_case':'one iteration preserves R for the remaining tail'}
        self.review([row])
        authorize(self.store,self.source,['helper'])

    def test_assessment_and_probe_expire_when_interface_changes(self):
        self.guided();self.assess();self.review([self.row(self.probe())])
        self.save(self.source.replace('"True & True"','"True"'))
        self.closed=check_groups(self.checker,self.store,self.store.read()['source_sha256'])
        with self.assertRaisesRegex(ValueError,'structural assessment'):
            self.review([self.row(self.probe())])

    def test_archived_candidates_rank_by_worst_remaining_burden(self):
        self.guided();self.checker.retain=True
        first=self.assess()['results'][0]
        self.save(self.source.replace('"True & True"','"True"'))
        self.closed=check_groups(self.checker,self.store,self.store.read()['source_sha256'])
        self.checker.retain=False
        second=self.assess()['results'][0]
        alternatives=second['ranked_alternatives']
        self.assertEqual(len(alternatives),2)
        self.assertEqual(alternatives[0]['assessment_id'],second['assessment_id'])
        self.assertEqual(alternatives[1]['assessment_id'],first['assessment_id'])
        self.assertTrue(all(a['requires_current_sketch_check_and_review'] for a in alternatives))

    def test_delegated_burden_uses_child_leaves_and_expires_on_child_reassessment(self):
        from regen.planning.theory_document import block
        from regen.planning.sketch_workflow import review_sketch, probe_helpers
        self.guided()
        leaf=block({'id':'leaf','role':'helper','context':[],'statement':'lemma leaf: "True"'})
        source=self.source.replace('(* REGEN-BEGIN ',leaf+'(* REGEN-BEGIN ',1).replace(
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\nusing leaf by simp')
        child={'target_entry_id':'helper','helper_ids':['leaf'],
               'use_sites':[{'helper_id':'leaf','consumer_line':1,'fact':'leaf'}]}
        saved=self.save(source,[self.group,child])
        checked=check_groups(self.checker,self.store,saved['source_sha256'])
        assessed=assess_sketch(self.checker,self.store,saved['source_sha256'])
        rows={r['target_entry_id']:r for r in assessed['results']}
        self.assertEqual(rows['contract']['rank_key'],rows['helper']['rank_key'])
        self.assertEqual(rows['contract']['helpers'][0]['status'],'delegated')
        receipts={r['target_entry_id']:r['receipt_id'] for r in checked['groups']}
        probe=probe_helpers(self.checker,self.store,saved['source_sha256'],['leaf'])['results'][0]['probe_id']
        child_review=review_sketch(self.store,saved['source_sha256'],'helper',receipts['helper'],
                                  [{**self.row(probe),'helper_id':'leaf'}])
        parent_row=self.row('unused');parent_row.pop('probe_id')
        parent_row.update(burden='delegated',child_review_id=child_review['review_id'])
        review_sketch(self.store,saved['source_sha256'],'contract',receipts['contract'],[parent_row])
        authorize(self.store,source,['helper','leaf'])
        self.checker.unknown=True
        assess_sketch(self.checker,self.store,saved['source_sha256'],['helper'])
        with self.assertRaisesRegex(ValueError,'child structural assessment changed'):
            authorize(self.store,source,['helper'])

    def test_native_failure_details_are_read_only_and_bound(self):
        failure=self.fail()
        before=deepcopy(self.state)
        details=read_failure(self.store,failure)
        self.assertTrue(details['current'])
        self.assertEqual(details['repair_scope'],'helper')
        self.assertEqual(details['proof'],'by fail')
        self.assertEqual(details['check']['diagnostic'],'local show mismatch')
        diagnose_failure(self.store,failure,'script','Repair only the failed local show.')
        self.assertEqual(self.state,before)
        self.assertEqual(len(workflow_view(self.store)['pending_failures']),1)

    def test_only_new_strategy_requires_assessment(self):
        self.assertNotIn('require_assessment',strategy_options('sketch-reviewed-v1'))
        self.assertTrue(strategy_options('sketch-guided-v1')['require_assessment'])


class GuidedControllerTests(unittest.TestCase):
    def test_assessment_and_failure_tools_use_actual_controller_path(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.planning.parallel_file_planner import generation_strategy
        from regen.planning.theory_document import block
        base=DocumentControllerTests();base.setUp();self.addCleanup(base.doCleanups)
        base.controller.generation_strategy=generation_strategy('sketch-guided-v1')
        draft=base.read()['document']
        self.assertTrue(draft['structural_assessment_required'])
        native=StructuralChecker()
        base.checkers['Target'].check_sketch_source=native.check_sketch_source
        helper=block({'id':'h','role':'helper','context':[], 'statement':'lemma h: "True"'},'by simp')
        source=draft['source'].replace('(* REGEN-BEGIN ',helper+'(* REGEN-BEGIN ',1).replace('oops','using h by simp')
        saved=base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source,
            'sketches':[{'target_entry_id':'target_contract','helper_ids':['h'],
                        'use_sites':[{'helper_id':'h','consumer_line':1,'fact':'h'}]}]})
        sha=saved['source_sha256']
        closed=base.call({'action':'check_sketch','source_sha256':sha})
        assessed=base.call({'action':'assess_sketch','source_sha256':sha})
        self.assertEqual(assessed['status'],'sketches_assessed',assessed)
        rejected=base.call({'action':'check_theory','source_sha256':sha,'entry_ids':['h']})
        self.assertEqual(rejected['feedback']['status'],'rejected_request')
        native.verdict='red'
        failed=base.call({'action':'probe_helpers','source_sha256':sha,'entry_ids':['h']})
        failure_id=failed['results'][0]['failure_id']
        details=base.call({'action':'read_failure','failure_id':failure_id})
        self.assertTrue(details['current'])
        self.assertEqual(details['repair_scope'],'h')
        base.call({'action':'diagnose_failure','failure_id':failure_id,'classification':'script',
                   'plan':'Repair this exact local proposition.'})
        saved=base.call({'action':'edit_theory','base_sha256':sha,
                        'source':source.replace(helper,helper.replace('by simp','by blast'))})
        sha=saved['source_sha256'];native.verdict='green'
        probe=base.call({'action':'probe_helpers','source_sha256':sha,'entry_ids':['h']})
        reviewed=base.call({'action':'review_sketch','source_sha256':sha,'target_entry_id':'target_contract',
            'receipt_id':closed['groups'][0]['receipt_id'],'reviews':[
                {'helper_id':'h','use_indices':[0],'domain_scope':'local','premises':'local',
                 'conclusion':'local','generalization':'none','burden':'isolated',
                 'reduction':'Required local truth.', 'probe_id':probe['results'][0]['probe_id']}]})
        self.assertEqual(reviewed['status'],'decomposition_reviewed')
        accepted=base.call({'action':'check_theory','source_sha256':sha,'entry_ids':['h']})
        self.assertEqual(accepted['feedback']['status'],'accepted',accepted)
        self.assertEqual(base.controller._state('Target')[1]['accepted_order'],['h'])


if __name__ == '__main__':
    unittest.main()
