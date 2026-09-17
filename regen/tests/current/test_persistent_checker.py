import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.tests.current.test_proof_commits import fixture
from regen.execution.repository_execution import context_graph


class PersistentCheckerTests(unittest.TestCase):
    def test_red_reuse_green_cold_certification_and_fallback(self):
        _, state = fixture(); graph = context_graph(state['graph'], [])
        class Cold(IsabelleChecker):
            count = 0
            def _check(self, source_text, label, **kwargs):
                self.count += 1
                source = self.work_dir/(label+'.thy');source.write_text(source_text)
                log = self.work_dir/(label+'.log');log.write_text('cold checked')
                return {'verdict':'green','returncode':0,'source':str(source),'log':str(log),'wall_ms':100}
        class Worker:
            starts = 0
            closes = 0
            verdict = 'red'
            def __init__(self,c,*args):
                Worker.starts+=1;self.root=c.work_dir;self.i=0
            def check(self,body):
                self.i+=1
                source=self.root/f'{self.i}.request';source.write_text(body)
                log=self.root/f'{self.i}.response';log.write_text(self.verdict)
                if self.verdict=='error':raise RuntimeError('worker exited')
                return {'verdict':self.verdict,'source':str(source),'log':str(log),'wall_ms':2}
            def close(self):Worker.closes+=1
        with tempfile.TemporaryDirectory() as td, patch('regen.execution.persistent_checker.PrefixWorker',Worker):
            root=Path(td);c=Cold(root,root/'c',heaps_dir=root,l4v_dir=root,semantic_workspace=root);c.persistent=True
            for proof in ('by simp','by blast'):
                self.assertEqual(c.check_node(graph,{'helper':proof},'helper')['verdict'],'red')
            self.assertEqual((Worker.starts,c.count),(1,0))
            Worker.verdict='green'
            result=c.check_node(graph,{'helper':'by auto'},'helper')
            self.assertEqual(c.count,1);self.assertIn('prefix_check',result)
            self.assertTrue(c.check_node(graph,{'helper':'by auto'},'helper')['reused_check'])
            self.assertEqual(c.count,1)
            Worker.verdict='error'
            result=c.check_node(graph,{'helper':'by force'},'helper')
            self.assertIn('fallback',result['prefix_check']);self.assertTrue(c._persistent_disabled)
            self.assertEqual(Worker.closes,1)

    def test_unsafe_ml_bypasses_worker(self):
        _, state=fixture();graph=context_graph(state['graph'],[])
        with tempfile.TemporaryDirectory() as td, patch('regen.execution.persistent_checker.PrefixWorker') as worker:
            root=Path(td);c=IsabelleChecker(root,root/'c',heaps_dir=root,l4v_dir=root,semantic_workspace=root);c.persistent=True
            with patch.object(c,'_check',return_value={'verdict':'red'}) as cold:
                c.check_node(graph,{'helper':'by (tactic \\<open>all_tac\\<close>)'},'helper')
                cold.assert_called_once();worker.assert_not_called()
