"""Planning failure cancels native preflight instead of resuming a partial plan."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from regen.planning.workspace_preflight import native_checker
from regen.execution.check_tasks import check_cancelled


class PlanningAbortTests(unittest.TestCase):
    def test_abort_cancels_running_preflight_and_rejects_later_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            context=dict(base_import='Main',base_session='HOL',base_catalog='/base',
                image='fixture',architecture='ARM',checker_context_id='fixture',
                heaps=root,l4v=root,semantic_workspace=root,isabelle=root)
            entered=threading.Event()
            def native(*args,**kwargs):
                entered.set()
                until=time.monotonic()+5
                while time.monotonic()<until:
                    check_cancelled()
                    time.sleep(.01)
                raise AssertionError('native preflight was not cancelled')
            with patch('regen.planning.workspace_preflight.load_context',return_value=context), \
                 patch('regen.planning.workspace_preflight.render',return_value='fixture'), \
                 patch('regen.planning.workspace_preflight.IsabelleChecker._check',side_effect=native):
                checker=native_checker(root)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    task=executor.submit(checker,{}, {},root/'first')
                    self.assertTrue(entered.wait(2))
                    checker.abort()
                    with self.assertRaisesRegex(RuntimeError,'cancelled'):
                        task.result(timeout=2)
                with self.assertRaisesRegex(RuntimeError,'owner aborted'):
                    checker({}, {},root/'second')
