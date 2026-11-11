"""Future Ours runs default to one agent/native resident; explicit parallelism remains."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


class OursSingleWorkerDefaultsTests(unittest.TestCase):
    def _cli(self, *options):
        from regen.execution.proof_session_runner import main
        argv = ['proof_session_runner','parallel-workspace','--run-dir','/unused-new-run',
                '--allow-external-call', *options]
        with patch('sys.argv',argv), redirect_stdout(io.StringIO()), \
             patch('regen.planning.parallel_file_planner.run_parallel_workspace',return_value={
                 'status':'selected_theories_sealed','integration_required':False}) as run:
            self.assertEqual(main(),0)
        return run.call_args.kwargs

    def test_cli_uses_one_worker_slot_and_resident_without_changing_native_limits(self):
        args = self._cli()
        self.assertEqual((args['max_parallel_theories'],args['native_slots'],args['resident_limit']),(1,1,1))
        self.assertEqual((args['native_threads'],args['native_cpus'],args['native_memory_mb']),(4,4,16384))
        self.assertEqual((args['check_timeout'],args['candidate_timeout'],args['effective_seconds']),(900,300,172800))

    def test_cli_preserves_explicit_two_worker_override(self):
        args = self._cli('--max-parallel-theories','2','--resident-limit','2')
        self.assertEqual((args['max_parallel_theories'],args['native_slots'],args['resident_limit']),(2,1,2))
        args = self._cli('--max-parallel-theories','2','--resident-limit','2','--native-slots','2')
        self.assertEqual((args['max_parallel_theories'],args['native_slots'],args['resident_limit']),(2,2,2))

    def test_api_default_serializes_ready_theories_and_freezes_single_worker_policy(self):
        from regen.planning import parallel_file_planner as planner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); binary = root/'codex'; binary.write_text('synthetic-not-executed')
            states = {name:'pending' for name in ('A','B','C')}
            events = []; lock = threading.Lock(); active = [0]; peak = [0]
            class Overview:
                selected = ['A','B','C']
                run = {'run_id':'single-worker-test','execution_input_kind':'refine-dataset',
                       'checker_context_root':str(root),'checker_context_id':'fixture'}
                options = {'model':'fixture','call_timeout':10,'check_timeout':10,'candidate_timeout':10}
                def __init__(self,*args,**kwargs):self.root=root
                def list_workspace(self):
                    with lock:
                        return {'theories':[{'theory':name,'status':state,'selected':True,
                            'contracts':[name],'ready_for_proof_action':state=='pending'}
                            for name,state in states.items()]}
            def session(run_root,theory,**kwargs):
                with lock:
                    active[0] += 1; peak[0] = max(peak[0],active[0])
                    events.append(('start',theory))
                with lock:
                    states[theory]='sealed'; events.append(('finish',theory)); active[0] -= 1
                return {'theory':theory,'status':'sealed'}
            settings = {'codex_bin':str(binary),'codex_runtime_root':str(root/'runtime'),
                        'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch.object(planner,'WorkspaceController',Overview), \
                 patch.object(planner,'run_theory_session',side_effect=session), \
                 patch('regen.infrastructure.audited_llm.model_settings',return_value=settings), \
                 patch('regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}), \
                 redirect_stdout(io.StringIO()):
                result = planner.run_parallel_workspace(root, budget_mode="effective-time",allow_external_call=True,integrate=False)
            self.assertEqual(result['status'],'selected_theories_sealed')
            self.assertEqual(result['workspace']['worker_count'],1)
            self.assertEqual(peak[0],1)
            self.assertEqual(events,[('start','A'),('finish','A'),('start','B'),('finish','B'),('start','C'),('finish','C')])
            policy = json.loads((root/'parallel-workspace-policy.json').read_text())
            self.assertEqual((policy['max_parallel_theories'],policy['native_slots'],policy['resident_limit']),(1,1,1))
            self.assertEqual((policy['threads'],policy['cpus'],policy['memory_mb']),(4,4,16384))


if __name__ == '__main__':
    unittest.main()
