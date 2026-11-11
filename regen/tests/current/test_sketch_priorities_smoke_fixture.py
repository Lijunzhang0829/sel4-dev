"""The native smoke fixture must be a valid multi-theory frozen input."""
from pathlib import Path
import tempfile
import unittest

from regen.execution.repository_execution import _progress_unlocked
from regen.planning.declaration_index import initial_index
from regen.planning.theory_document import candidate, header, block
from regen.tools.sketch_priorities_short_smoke import (
    fixture, specifications, helper_entries, consumer, group, committed_proofs,
    replay_uncached, THEORY, HELPER, LOCAL)


class SketchPrioritiesFixtureTests(unittest.TestCase):
    def test_before_first_commit_replay_is_uninitialized_not_an_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            run,packages=fixture(root,{'checker_context_id':'synthetic-context','base_import':'HOL.Main'},specifications())
            self.assertIsNone(replay_uncached(root,run,packages[THEORY]))
            self.assertEqual(committed_proofs(root,run,packages[THEORY],allow_uninitialized=True),{})
            with self.assertRaisesRegex(AssertionError,'expected committed journal'):
                committed_proofs(root,run,packages[THEORY])

    def test_unstarted_multi_theory_progress_has_complete_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            run,packages=fixture(root,{'checker_context_id':'synthetic-context','base_import':'HOL.Main'},specifications())
            result=_progress_unlocked(root)
            self.assertEqual(result['contracts'],3)
            self.assertEqual(result['checked_contracts'],0)
            self.assertEqual(result['session_status'],'partial')
            self.assertEqual(run['model_calls'],0)
            self.assertTrue(run['validation_only'])
            self.assertEqual(set(packages),{'Switch_A','Switch_B'})

    def test_incorrect_and_corrected_helpers_preserve_fixed_statements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            _,packages=fixture(root,{'checker_context_id':'synthetic-context','base_import':'HOL.Main'},specifications())
            package=packages[THEORY]
            state={'graph':initial_index(package,run_id='smoke'),'proofs':{},'accepted_order':[],
                   'status':'generating','rounds':{},'revisions':{},'seal_id':None}
            fixed={n['id']:n for n in state['graph']['nodes']}
            for corrected in [False,True]:
                h,local=helper_entries(corrected)
                source=header(THEORY,['HOL.Main'])+block(h,'by (cases b) auto')+block(local,'by simp')
                source+=block(fixed['parent'],consumer(corrected))+block(fixed['independent'],'by simp')+'end\n'
                proposed=candidate(package,state,source,['HOL.Main'])
                from regen.planning.helper_use_sites import validate
                validate(group(),{n['id']:n for n in proposed['graph']['nodes']},consumer(corrected))
                self.assertEqual({n['id']:n['statement'] for n in proposed['graph']['nodes'] if n['role']=='contract'},
                                 {k:n['statement'] for k,n in fixed.items()})
                self.assertEqual(group()['helper_ids'],[HELPER,LOCAL])
                self.assertEqual(proposed['proofs'][HELPER],'by (cases b) auto')
