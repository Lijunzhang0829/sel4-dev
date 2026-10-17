"""Workflow authority regressions. Native proof behavior is tested by HOL smoke."""
from copy import deepcopy
import hashlib
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from regen.planning.sketch_check import authorize, check_groups, strategy_options
from regen.planning.sketch_workflow import (probe_helpers, review_sketch, diagnose_failure,
                                          record_formal_result, workflow_view)
from regen.planning.theory_document import DocumentStore, block
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker


class DiagnosticChecker(Checker):
    def check_sketch_source(self, source, prefix, admitted, *, timeout=None):
        self.last_timeout = timeout
        self.last_admitted = admitted
        result = self._check(source, 'diagnostic', timeout=timeout)
        marker = re.search(r'REGEN_(?:SKETCH_CLOSED|HELPER_PROBE_CLEAN) [0-9a-f]{64}', source)[0]
        log = marker + '\n' if self.verdict == 'green' else 'failed proof\n'
        if marker.startswith('REGEN_SKETCH'):
            signature = marker.split()[-1]
            for line in source.splitlines():
                if 'XML.Elem (("helper_use", [' not in line:
                    continue
                attrs = {key:json.loads(value) for key,value in re.findall(
                    r'\("(helper_id|consumer_line|fact|index)", ("(?:\\.|[^"])*")\)',line)}
                element = ET.Element('helper_use', attrs)
                facts = ET.SubElement(element,'facts'); fact = ET.SubElement(facts,'fact')
                for key, value in [('proposition','True'),('schematic_variable_count','0'),('schematic_type_variable_count','0')]:
                    ET.SubElement(fact,key).text = value
                ET.SubElement(ET.SubElement(element,'goals'),'goal').text='True'
                log += ('REGEN_HELPER_USE_BEGIN '+signature+'\n'+ET.tostring(element,encoding='unicode')
                        +'\nREGEN_HELPER_USE_END '+signature+'\n')
        result.update(log_content=log,log_sha256=hashlib.sha256(log.encode()).hexdigest())
        return result


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.package,self.state=fixture()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.checker=DiagnosticChecker()
        self.store=DocumentStore(self.temp.name,self.package,self.state,self.checker.imports(),
                                 **strategy_options('sketch-reviewed-v1'))
        self.group={'target_entry_id':'contract','helper_ids':['helper'],
                    'use_sites':[{'helper_id':'helper','consumer_line':1,'fact':'helper'}]}
        self.source=self.store.read()['source'].replace('lemma target: "True"\n(* REGEN-PROOF *)\noops',
                                                       'lemma target: "True"\n(* REGEN-PROOF *)\nusing helper by simp')
        self.save()
        self.closed=check_groups(self.checker,self.store,self.store.read()['source_sha256'])

    def save(self,source=None,groups=None):
        return self.store.edit({'action':'edit_theory','base_sha256':self.store.read()['source_sha256'],
                                'source':source or self.source,'sketches':groups or [self.group]}, {})

    def probe(self):
        return probe_helpers(self.checker,self.store,self.store.read()['source_sha256'])['results'][0]['probe_id']

    def row(self,probe):
        return {'helper_id':'helper','use_indices':[0],'domain_scope':'local','premises':'local',
                'conclusion':'local','generalization':'none','burden':'isolated',
                'reduction':'The helper isolates a propositional conjunction.', 'probe_id':probe}

    def review(self,rows):
        return review_sketch(self.store,self.store.read()['source_sha256'],'contract',
                             self.closed['groups'][0]['receipt_id'],rows)

    def fail(self,proof='by fail'):
        source=self.store.read()['source'].replace('lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\n'+proof)
        self.save(source)
        record_formal_result(self.store,source,{'check':{'verdict':'red','diagnostic':'local show mismatch'},
                              'node_results':[{'node_id':'helper','status':'failed'},
                                              {'node_id':'contract','status':'not_reached'}]})
        return workflow_view(self.store)['pending_failures'][0]['failure_id']

    def test_probes_and_review_are_required_and_never_publish(self):
        before=deepcopy(self.state)
        with self.assertRaisesRegex(ValueError,'decomposition review'):
            authorize(self.store,self.source,['helper'])
        probe=self.probe()
        self.assertEqual(self.checker.last_timeout,10)
        self.assertEqual(self.checker.last_admitted,[])
        self.review([self.row(probe)])
        authorize(self.store,self.source,['helper'])
        self.assertEqual(self.state,before)
        self.save(self.source.replace('oops','by simp'))
        authorize(self.store,self.store.read()['source'],['helper'])

    def test_unchanged_burden_and_unjustified_generalization_rejected(self):
        row=self.row(self.probe())
        for changes in [{'burden':'unchanged'}, {'domain_scope':'generalized'},
                        {'use_indices':[]}, {'probe_id':'0'*64}, {'reduction':''}]:
            with self.subTest(changes=changes), self.assertRaises((ValueError,FileNotFoundError)):
                self.review([{**row,**changes}])
        self.review([{**row,'domain_scope':'generalized','generalization':'induction',
                      'reduction':'Quantify the changing index to support induction.'}])

    def test_interfaces_invalidate_probe_and_review(self):
        probe=self.probe();self.review([self.row(probe)])
        changed=self.source.replace('"True & True"','"True"')
        self.save(changed)
        self.closed=check_groups(self.checker,self.store,self.store.read()['source_sha256'])
        with self.assertRaisesRegex(ValueError,'probe does not match'):
            self.review([self.row(probe)])

    def test_new_native_receipt_expires_the_old_review(self):
        self.review([self.row(self.probe())])
        signature=self.closed['groups'][0]['sketch_sha256']
        (self.store.root/'sketch-certificates'/(signature+'.json')).unlink()
        check_groups(self.checker,self.store,self.store.read()['source_sha256'])
        with self.assertRaisesRegex(ValueError,'stale decomposition review'):
            authorize(self.store,self.source,['helper'])

    def test_failure_requires_diagnosis_and_script_retry(self):
        failure=self.fail()
        changed=self.store.read()['source'].replace('"True & True"','"True"')
        with self.assertRaisesRegex(ValueError,'diagnose failure'):
            self.save(changed)
        diagnose_failure(self.store,failure,'script','Bind the actual native goal assumptions.')
        with self.assertRaisesRegex(ValueError,'local proof repair'):
            self.save(changed)
        with self.assertRaisesRegex(ValueError,'local retry'):
            diagnose_failure(self.store,failure,'mathematical','Skip directly to another decomposition.')
        fixed=self.store.read()['source'].replace('by fail','by simp')
        self.save(fixed)
        record_formal_result(self.store,fixed,{'check':{'verdict':'green'},'node_results':[]})
        self.save(fixed.replace('"True & True"','"True"'))
        self.assertFalse(workflow_view(self.store)['pending_failures'])

    def test_failed_consumer_is_repaired_without_establishing_parent_green(self):
        self.save(self.source.replace('using helper by simp','using helper by fastforce'))
        self.checker.verdict='red'
        check_groups(self.checker,self.store,self.store.read()['source_sha256'])
        failure=workflow_view(self.store)['pending_failures'][0]['failure_id']
        diagnose_failure(self.store,failure,'script','Correct the local consumer rule application.')
        with self.assertRaisesRegex(ValueError,'local proof repair'):
            self.save(self.source.replace('"True & True"','"True"'))
        fixed=self.source.replace('using helper by simp','using helper by blast')
        self.save(fixed)
        self.checker.verdict='green'
        check_groups(self.checker,self.store,self.store.read()['source_sha256'])
        self.assertFalse(workflow_view(self.store)['pending_failures'])
        self.assertFalse(self.state['proofs'])

    def test_changed_failed_retry_can_be_reclassified_but_same_proof_cannot(self):
        failure=self.fail()
        diagnose_failure(self.store,failure,'known_fact','Apply a legal existing fact.')
        same=self.store.read()['source']
        evidence={'check':{'verdict':'red','diagnostic':'another obligation'},
                  'node_results':[{'node_id':'helper','status':'failed'}]}
        record_formal_result(self.store,same,evidence)
        failure=workflow_view(self.store)['pending_failures'][0]['failure_id']
        with self.assertRaisesRegex(ValueError,'local retry'):
            diagnose_failure(self.store,failure,'mathematical','The interface needs a different invariant.')
        changed=same.replace('by fail','by blast');self.save(changed)
        record_formal_result(self.store,changed,evidence)
        failure=workflow_view(self.store)['pending_failures'][0]['failure_id']
        diagnose_failure(self.store,failure,'mathematical','The changed proof exposes an invariant gap.')
        self.save(changed.replace('"True & True"','"True"'))

    def test_partial_batch_success_does_not_clear_failure(self):
        self.fail()
        record_formal_result(self.store,self.store.read()['source'],{'check':{'verdict':'red'},
            'node_results':[{'node_id':'helper','status':'completed_uncommitted'}]})
        self.assertEqual(len(workflow_view(self.store)['pending_failures']),1)

    def test_nested_delegation_requires_a_current_child_review(self):
        original_probe=self.probe();self.review([self.row(original_probe)])
        leaf=block({'id':'leaf','role':'helper','context':[],'statement':'lemma leaf: "True"'})
        source=self.source.replace('(* REGEN-BEGIN ',leaf+'(* REGEN-BEGIN ',1).replace(
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\nusing leaf by simp')
        child={'target_entry_id':'helper','helper_ids':['leaf'],
               'use_sites':[{'helper_id':'leaf','consumer_line':1,'fact':'leaf'}]}
        saved=self.save(source,[self.group,child])
        checked=check_groups(self.checker,self.store,saved['source_sha256'])
        with self.assertRaisesRegex(ValueError,'child decomposition'):
            authorize(self.store,source,['helper'])
        probe=probe_helpers(self.checker,self.store,saved['source_sha256'])['results'][0]['probe_id']
        row={**self.row(probe),'helper_id':'leaf'}
        child_receipt=next(g['receipt_id'] for g in checked['groups'] if g['target_entry_id']=='helper')
        reviewed=review_sketch(self.store,saved['source_sha256'],'helper',child_receipt,[row])
        parent_row=self.row(original_probe);parent_row.pop('probe_id')
        parent_row.update(burden='delegated',child_review_id=reviewed['review_id'])
        self.review([parent_row])
        authorize(self.store,source,['helper','leaf'])

    def test_infrastructure_failure_is_not_a_mathematical_diagnosis(self):
        source=self.source.replace('oops','by simp');self.save(source)
        record_formal_result(self.store,source,{'check':{'verdict':'red','failure_scope':'native_preparation'},
            'node_results':[{'node_id':'helper','status':'failed'}]})
        self.assertFalse(workflow_view(self.store)['pending_failures'])

    def test_failed_parent_does_not_block_an_unrelated_parent(self):
        from pathlib import Path
        from regen.planning.declaration_index import build_index
        package=deepcopy(self.package)
        package['contracts'].append({'contract_id':'other','name':'other',
            'exact_statement':'lemma other: "True"','contract_owned_context':[]})
        other={'id':'other','name':'other','role':'contract','context':[], 'statement':'lemma other: "True"'}
        state=deepcopy(self.state)
        original={n['id']:n for n in state['graph']['nodes']}
        state['graph']=build_index(package,[*(original[k] for k in state['graph']['execution_order']),other],
                                   run_id=state['graph']['run_id'],revision=1)
        store=DocumentStore(Path(self.temp.name)/'two-parents',package,state,self.checker.imports(),
                            **strategy_options('sketch-reviewed-v1'))
        source=store.read()['source'].replace('lemma target: "True"\n(* REGEN-PROOF *)\noops',
            'lemma target: "True"\n(* REGEN-PROOF *)\nusing helper by simp').replace(
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
            'lemma helper: "True & True"\n(* REGEN-PROOF *)\nby fail')
        store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                    'source':source,'sketches':[self.group]}, {})
        record_formal_result(store,source,{'check':{'verdict':'red'},
            'node_results':[{'node_id':'helper','status':'failed'}]})
        extra=block({'id':'extra','role':'helper','context':[],'statement':'lemma extra: "True"'})
        source=source.replace(block(other),extra+block(other,'using extra by simp'))
        saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
            'source':source,'sketches':[{'target_entry_id':'other','helper_ids':['extra'],
                'use_sites':[{'helper_id':'extra','consumer_line':1,'fact':'extra'}]}]}, {})
        self.assertEqual(saved['status'],'draft_saved')


if __name__=='__main__':
    unittest.main()
