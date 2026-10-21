from copy import deepcopy
import unittest
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.seal_fixture import seal,bound
from regen.execution.seal_evidence import validate_seal
from regen.execution.proof_commits import digest

class SealEvidenceTests(unittest.TestCase):
    def state(self):
        _,s=fixture();s['proofs']={'helper':'by simp','contract':'by simp'}
        s['accepted_order']=['helper','contract'];s['graph']['accepted_order']=s['accepted_order']
        return s
    def test_valid_bound_evidence(self):
        s=self.state();e=seal(s);validate_seal(s,e)
    def test_rehashed_malformed_native_evidence_is_rejected(self):
        changes=[('missing_complete',lambda e:e.pop('complete')),
                 ('nonzero_return',lambda e:e['complete'].update(returncode=1)),
                 ('boolean_return',lambda e:e['build'].update(returncode=False)),
                 ('missing_log',lambda e:e['build'].pop('log_content')),
                 ('changed_source',lambda e:e['complete'].update(bound('source','theory Fake imports Main begin end'))),
                 ('changed_imports',lambda e:e.update(imports=['Main'])),
                 ('missing_certificate',lambda e:e['dependencies']['complete_theory_audit']['proof_dependencies'].pop('helper')),
                 ('admitted',lambda e:e['dependency_receipts']['helper'].update(bound('raw','HAS_SKIP_PROOF\t'+b'true'.hex()+'\n'))),
                 ('changed_build_source',lambda e:e['build']['source_files'].update({next(iter(e['build']['source_files'])):'bad'})),
                 ('changed_root',lambda e:e['build'].update(bound('root','session Fake = HOL +'))),
                 ('wrong_impact',lambda e:e['impact'].update(graph_revision=900)),
                 ('old_format',lambda e:e.pop('seal_evidence_version'))]
        for name,change in changes:
            with self.subTest(name=name):
                s=self.state();e=seal(s);change(e);s['seal_id']='theory-seal:'+digest(e)[:20]
                with self.assertRaises(ValueError):validate_seal(s,e)
    def test_changed_accepted_proof_rejected(self):
        s=self.state();e=seal(s);s['proofs']['helper']='by blast'
        with self.assertRaisesRegex(ValueError,'state binding'):validate_seal(s,e)

    def test_frozen_context_binding(self):
        s=self.state();e=seal(s)
        with self.assertRaisesRegex(ValueError,'frozen journal'):
            validate_seal(s,e,expected_imports=['Wrong.Theory'])
        with self.assertRaisesRegex(ValueError,'frozen run'):
            validate_seal(s,e,base_session='Wrong')

    def test_build_input_mutation_cannot_certify(self):
        import tempfile
        from pathlib import Path
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from regen.execution.isabelle_node_checker import IsabelleChecker
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c=IsabelleChecker(root,root/'checks',heaps_dir=root,l4v_dir=root,semantic_workspace=root)
            f=c.generated_dir/'X.thy';f.write_text('theory X imports Main begin end')
            def mutate(command,**kwargs):
                f.write_text('theory X imports Main begin lemma forged: "False" sorry end')
                return CompletedProcess(command,0,'','')
            with patch('regen.execution.isabelle_node_checker.native_run',side_effect=mutate):
                with self.assertRaisesRegex(ValueError,'inputs changed'):
                    c.build_generated_session(['X'])
