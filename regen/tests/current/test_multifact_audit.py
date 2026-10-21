"""Both seal audit emitters must inspect every theorem in a named fact."""
from pathlib import Path
import tempfile
import unittest
from regen.execution.isabelle_node_checker import (automation_audit_ml,
    proof_dependency_audit_ml, parse_proof_dependencies)
from regen.analysis.retained_semantic_impact import summarize_node

class MultiFactAuditTests(unittest.TestCase):
    def test_both_emitters_audit_complete_list(self):
        for emit in (automation_audit_ml, proof_dependency_audit_ml):
            with self.subTest(emitter=emit.__name__):
                source=emit('pair',dependency_filename='pair.tsv')
                self.assertIn('Proof_Context.get_thms ctxt "pair"',source)
                self.assertIn('Thm.consolidate targets',source)
                self.assertIn('Thm_Deps.thm_deps thy targets',source)
                self.assertIn('Thm_Deps.has_skip_proof targets',source)
                self.assertNotIn('[target]',source)
                self.assertNotIn('hd targets',source)

    def test_parser_preserves_dependency_fact_indices(self):
        def row(tag,*fields):return '\t'.join([tag,*[s.encode().hex() for s in fields]])
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'deps.tsv';p.write_text('\n'.join([
                row('THM_DEP','Example.left','0'),row('THM_DEP','Example.right','1'),
                row('HAS_SKIP_PROOF','false')])+'\n')
            d=parse_proof_dependencies(p,node_id='pair')
            self.assertEqual(d['dependencies'],[{'canonical_name':'Example.left','fact_index':0},
                {'canonical_name':'Example.right','fact_index':1}])
            self.assertFalse(d['has_skip_proof'])

    def test_skip_in_list_rejected_by_impact_consumer(self):
        with self.assertRaisesRegex(ValueError,'skip proof'):
            summarize_node({'id':'pair'},'proof body',{'automation_context':{'records':[]},'proof_dependencies':{
                'has_skip_proof':True,'dependencies':[]}},[])
