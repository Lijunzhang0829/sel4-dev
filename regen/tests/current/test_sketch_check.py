"""Interface/authority regressions; native solvability is covered by the smoke tool."""
from copy import deepcopy
import hashlib
import re
import tempfile
import unittest

from regen.planning.sketch_check import authorize, check_groups, read_receipt, available_prefix, STRATEGY
from regen.planning.theory_document import (DocumentStore, block, candidate, render,
    selected_source, baseline, check_document, validate_document_commit)
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker


class ConditionalChecker(Checker):
    def check_sketch_source(self, source, prefix, admitted):
        result = self._check(source, 'sketch')
        marker = re.search(r'REGEN_SKETCH_CLOSED [0-9a-f]{64}', source)[0]
        log = marker + '\n' if self.verdict == 'green' else 'unclosed consumer\n'
        result.update(log_content=log, log_sha256=hashlib.sha256(log.encode()).hexdigest(),
                      check_scope='test-conditional-diagnostic', publishable=False)
        return result


class SketchGroupTests(unittest.TestCase):
    def setUp(self):
        self.package, self.state = fixture()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.checker = ConditionalChecker()
        self.store = DocumentStore(self.temp.name, self.package, self.state,
                                   self.checker.imports(), require_sketch=True)
        self.source = self.store.read()['source']
        second = block({'id':'second', 'role':'helper', 'context':[],
                        'statement':'lemma second: "True | False"'})
        # Insert a complete marked block, outside the target's identity marker.
        target = block(next(n for n in self.state['graph']['nodes'] if n['id']=='contract'))
        self.group_source = self.source.replace(target, second + target).replace(
            'lemma target: "True"\n(* REGEN-PROOF *)\noops',
            'lemma target: "True"\n(* REGEN-PROOF *)\nusing helper second by simp')

    def save(self, source=None, groups=None):
        return self.store.edit({'action':'edit_theory', 'base_sha256':self.store.read()['source_sha256'],
            'source':source or self.group_source,
            'sketches':groups or [{'target_entry_id':'contract','helper_ids':['helper','second']}]}, {})

    def test_group_requires_consumer_and_orphans_cannot_be_saved(self):
        before = self.store.read()['source_sha256']
        with self.assertRaisesRegex(ValueError, 'parent sketch group'):
            self.store.edit({'action':'edit_theory','base_sha256':before,'source':self.group_source}, {})
        with self.assertRaisesRegex(ValueError, 'executable parent'):
            self.save(self.group_source.replace('using helper second by simp','oops'))
        self.assertEqual(self.store.read()['source_sha256'], before)

    def test_two_helpers_conditionally_close_without_publishing(self):
        saved = self.save()
        with self.assertRaisesRegex(ValueError, 'successful current check_sketch'):
            authorize(self.store, self.group_source.replace('oops','by simp'), ['helper','second'])
        before = deepcopy(self.state)
        result = check_groups(self.checker, self.store, saved['source_sha256'])
        self.assertEqual(result['status'], 'conditional_closed')
        self.assertEqual(result['new_green'], [])
        self.assertFalse(result['publishable'])
        self.assertEqual(self.state, before)
        self.assertNotIn('source_content',result['groups'][0]['check'])
        native = read_receipt(self.store,result['groups'][0]['receipt_id'],'full')['check']['source_content']
        self.assertEqual(native.count('\nsorry'), 2)
        self.assertNotIn('sorry', self.store.read()['source'])
        self.assertEqual(len(authorize(self.store,self.group_source.replace('oops','by simp'),['helper','second'])), 1)
        repeated=check_groups(self.checker,self.store,saved['source_sha256'])
        self.assertTrue(repeated['groups'][0]['reused_sketch_check'])
        with self.assertRaises(ValueError):
            read_receipt(self.store,'../receipt')

    def test_statement_or_consumer_change_expires_but_proof_filling_does_not(self):
        saved = self.save()
        check_groups(self.checker, self.store, saved['source_sha256'])
        filled = self.group_source.replace('oops','by simp')
        self.save(filled)
        self.assertEqual(self.store.read()['sketch_groups'][0]['status'], 'conditional_closed')
        self.save(filled.replace('"True | False"','"True --> True"'))
        with self.assertRaisesRegex(ValueError, 'successful current check_sketch'):
            authorize(self.store, self.store.read()['source'], ['second'])
        self.save(filled.replace('using helper second by simp','using helper second by blast'))
        self.assertEqual(self.store.read()['sketch_groups'][0]['status'], 'needs_sketch_check')

    def test_rejected_composition_and_stale_request_never_authorize(self):
        saved = self.save()
        self.checker.verdict = 'red'
        result = check_groups(self.checker,self.store,saved['source_sha256'])
        self.assertEqual(result['status'],'sketch_rejected')
        with self.assertRaises(ValueError):
            authorize(self.store,self.group_source.replace('oops','by simp'),['helper'])
        with self.assertRaisesRegex(ValueError,'current source_sha256'):
            check_groups(self.checker,self.store,'wrong')

    def test_prefix_mutation_invalidates_certificate(self):
        self.state['accepted_order'] = ['helper']; self.state['proofs']={'helper':'by simp'}
        self.state['graph']['accepted_order']=['helper']
        saved=self.save()
        check_groups(self.checker,self.store,saved['source_sha256'])
        self.state['proofs']['helper']='by blast'
        self.assertEqual(self.store.read()['sketch_groups'][0]['status'],'needs_sketch_check')

    def test_nested_group_must_be_rooted_and_parent_cannot_be_assumed(self):
        leaf=block({'id':'leaf','role':'helper','context':[],'statement':'lemma leaf: "True"'})
        source=self.group_source.replace('(* REGEN-BEGIN ',leaf+'(* REGEN-BEGIN ',1)
        source=source.replace('lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
                              'lemma helper: "True & True"\n(* REGEN-PROOF *)\nusing leaf by simp')
        saved=self.save(source,[{'target_entry_id':'contract','helper_ids':['helper','second']},
                                {'target_entry_id':'helper','helper_ids':['leaf']}])
        self.assertEqual(len(self.store.read()['sketch_groups']),2)
        result=check_groups(self.checker,self.store,saved['source_sha256'])
        self.assertEqual(result['status'],'conditional_closed')
        with self.assertRaisesRegex(ValueError,'before their parent'):
            self.save(source,[{'target_entry_id':'helper','helper_ids':['helper']}])

    def test_helper_order_and_later_green_facts_cannot_widen_context(self):
        with self.assertRaisesRegex(ValueError,'declaration order'):
            self.save(groups=[{'target_entry_id':'contract','helper_ids':['second','helper']}])
        from regen.planning.theory_document import parse
        self.state['accepted_order']=['helper']; self.state['proofs']={'helper':'by simp'}
        self.state['graph']['accepted_order']=['helper']
        entries,_=parse(self.source,self.package,self.checker.imports())
        self.assertEqual(available_prefix(self.state,entries,'contract')['accepted_order'],['helper'])
        self.assertEqual(available_prefix(self.state,list(reversed(entries)),'contract')['accepted_order'],[])

    def test_local_demands_requires_complete_current_use_selectors(self):
        self.store.require_use_sites = True
        with self.assertRaisesRegex(ValueError, 'requires use_sites'):
            self.save()
        group = {'target_entry_id':'contract','helper_ids':['helper','second'],
                 'use_sites':[{'helper_id':'helper','consumer_line':1,'fact':'helper'},
                              {'helper_id':'second','consumer_line':1,'fact':'second'}]}
        self.save(groups=[group])
        self.assertTrue(self.store.read()['helper_use_sites_required'])
        for sites in [group['use_sites'][:1],
                      [{**s,'consumer_line':True} for s in group['use_sites']],
                      [{**s,'consumer_line':2} for s in group['use_sites']],
                      [{**s,'fact':'helper[sorry]'} for s in group['use_sites']]]:
            with self.subTest(sites=sites), self.assertRaises(ValueError):
                self.save(groups=[{**group,'use_sites':sites}])
        with self.assertRaisesRegex(ValueError, 'selected consumer command'):
            self.save(self.group_source.replace('using helper second by simp','using helper by simp'), [group])

    def test_success_marker_without_native_use_observations_does_not_authorize(self):
        group = {'target_entry_id':'contract','helper_ids':['helper','second'],
                 'use_sites':[{'helper_id':'helper','consumer_line':1,'fact':'helper'},
                              {'helper_id':'second','consumer_line':1,'fact':'second'}]}
        saved = self.save(groups=[group])
        result = check_groups(self.checker,self.store,saved['source_sha256'])
        self.assertEqual(result['status'],'sketch_rejected')
        self.assertEqual(result['groups'][0]['helper_use_coverage']['unobserved_helper_ids'],
                         ['helper','second'])
        with self.assertRaisesRegex(ValueError,'successful current check_sketch'):
            authorize(self.store,self.group_source.replace('oops','by simp'),['helper'])

    def test_unobserved_historical_receipts_remain_explicitly_unobserved(self):
        saved = self.save()
        result = check_groups(self.checker,self.store,saved['source_sha256'])
        self.assertEqual(result['status'],'conditional_closed')
        brief = read_receipt(self.store,result['groups'][0]['receipt_id'])
        self.assertEqual(brief['helper_use_coverage']['observed_use_sites'],0)
        self.assertFalse(brief['helper_use_coverage']['full_observations_available'])


class IncrementalSelectionTests(unittest.TestCase):
    def setUp(self):
        self.package,self.old=fixture(); self.checker=Checker()
        self.imports=self.checker.imports(); self.source=render(self.package,self.old,self.imports)

    def test_targeted_check_keeps_unselected_failure_in_original_draft(self):
        source=self.source.replace('oops','by simp',1).replace('oops','by fail',1)
        projected=selected_source(self.package,self.old,source,self.imports,['helper'])
        new=candidate(self.package,self.old,projected,self.imports)
        self.assertEqual(new['accepted_order'],['helper'])
        self.assertIn('by fail',source)
        self.assertNotIn('by fail',projected)
        evidence=check_document(self.checker,self.old,new,projected)
        validate_document_commit(self.package,self.old,new,evidence)

    def test_rewrite_reuses_unchanged_prefix_and_rechecks_later_proofs(self):
        source=self.source.replace('oops','by simp')
        old=candidate(self.package,self.old,source,self.imports)
        changed=source.replace('lemma target: "True"\n(* REGEN-PROOF *)\nby simp',
                               'lemma target: "True"\n(* REGEN-PROOF *)\nby blast')
        new=candidate(self.package,old,changed,self.imports)
        base,mode=baseline(old,new)
        self.assertEqual(mode,'suffix')
        self.assertEqual(base['accepted_order'],['helper'])
        evidence=check_document(self.checker,old,new,changed)
        validate_document_commit(self.package,old,new,evidence)

    def test_multiple_selected_updates_and_invalid_selections(self):
        source=self.source.replace('oops','by simp')
        self.assertEqual(selected_source(self.package,self.old,source,self.imports,['helper','contract']),source)
        for ids in ([],['helper','helper'],['unknown']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                selected_source(self.package,self.old,source,self.imports,ids)

    def test_unselected_ill_typed_new_declaration_stays_draft_only(self):
        broken=block({'id':'broken','role':'helper','context':[],
                      'statement':'lemma broken: "(0::nat) = True"'})
        source=self.source.replace('(* REGEN-BEGIN ',broken+'(* REGEN-BEGIN ',1)
        source=source.replace('lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
                              'lemma helper: "True & True"\n(* REGEN-PROOF *)\nby simp')
        projected=selected_source(self.package,self.old,source,self.imports,['helper'])
        new=candidate(self.package,self.old,projected,self.imports)
        self.assertNotIn('broken',{n['id'] for n in new['graph']['nodes']})
        self.assertIn('lemma broken',source)
        evidence=check_document(self.checker,self.old,new,projected)
        self.assertIsNone(evidence['graph_check'])
        validate_document_commit(self.package,self.old,new,evidence)


class SketchControllerTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.planning.parallel_file_planner import generation_strategy
        self.base=DocumentControllerTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.base.controller.generation_strategy=generation_strategy(STRATEGY)

    def test_native_gate_and_two_targeted_helper_updates(self):
        draft=self.base.read()['document']
        checker=self.base.checkers['Target']
        checker.check_sketch_source=ConditionalChecker().check_sketch_source
        helpers=''.join(block({'id':key,'role':'helper','context':[],
            'statement':f'lemma {key}: "True"'},'by simp') for key in ['h1','h2'])
        source=draft['source'].replace('(* REGEN-BEGIN ',helpers+'(* REGEN-BEGIN ',1).replace(
            'oops','using h1 h2 by simp')
        with self.assertRaisesRegex(ValueError,'parent sketch group'):
            self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source})
        saved=self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source,
            'sketches':[{'target_entry_id':'target_contract','helper_ids':['h1','h2']}]})
        rejected=self.base.call({'action':'check_theory','source_sha256':saved['source_sha256'],
                                 'entry_ids':['h1','h2']})
        self.assertEqual(rejected['feedback']['status'],'rejected_request')
        checked=self.base.call({'action':'check_sketch','source_sha256':saved['source_sha256']})
        self.assertEqual(checked['status'],'conditional_closed',checked)
        accepted=self.base.call({'action':'check_theory','source_sha256':saved['source_sha256'],
                                 'entry_ids':['h1','h2']})
        self.assertEqual(accepted['feedback']['status'],'accepted',accepted)
        self.assertEqual(self.base.controller._state('Target')[1]['accepted_order'],['h1','h2'])
        self.assertEqual(self.base.read()['document']['source'],source)
        parent=self.base.call({'action':'check_theory','source_sha256':saved['source_sha256'],
                               'entry_ids':['target_contract']})
        self.assertEqual(parent['feedback']['status'],'accepted',parent)

    def test_reviewed_strategy_is_enforced_in_actual_check_theory_path(self):
        from regen.planning.parallel_file_planner import generation_strategy
        from regen.tests.current.test_sketch_workflow import DiagnosticChecker
        self.base.controller.generation_strategy=generation_strategy('sketch-reviewed-v1')
        draft=self.base.read()['document']
        self.base.checkers['Target'].check_sketch_source=DiagnosticChecker().check_sketch_source
        helper=block({'id':'h','role':'helper','context':[], 'statement':'lemma h: "True"'},'by simp')
        source=draft['source'].replace('(* REGEN-BEGIN ',helper+'(* REGEN-BEGIN ',1).replace('oops','using h by simp')
        saved=self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source,
            'sketches':[{'target_entry_id':'target_contract','helper_ids':['h'],
                        'use_sites':[{'helper_id':'h','consumer_line':1,'fact':'h'}]}]})
        sha=saved['source_sha256']
        checked=self.base.call({'action':'check_sketch','source_sha256':sha})
        rejected=self.base.call({'action':'check_theory','source_sha256':sha,'entry_ids':['h']})
        self.assertEqual(rejected['feedback']['status'],'rejected_request',rejected)
        probes=self.base.call({'action':'probe_helpers','source_sha256':sha})
        reviewed=self.base.call({'action':'review_sketch','source_sha256':sha,'target_entry_id':'target_contract',
            'receipt_id':checked['groups'][0]['receipt_id'],'reviews':[
                {'helper_id':'h','use_indices':[0],'domain_scope':'local','premises':'local',
                 'conclusion':'local','generalization':'none','burden':'isolated',
                 'reduction':'Isolate the required True proposition.', 'probe_id':probes['results'][0]['probe_id']}]})
        self.assertEqual(reviewed['status'],'decomposition_reviewed')
        accepted=self.base.call({'action':'check_theory','source_sha256':sha,'entry_ids':['h']})
        self.assertEqual(accepted['feedback']['status'],'accepted',accepted)


if __name__=='__main__':
    unittest.main()
