import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from regen.infrastructure.llm_transcript import artifact_paths
from regen.tools.repository_planning_retry import call_with_retry


class PlanningRetryTest(unittest.TestCase):
    def test_timeout_retries_identical_request_and_discards_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / 'request'
            calls = []
            def call(prompt, actual, **options):
                calls.append((prompt, dict(options)))
                paths = artifact_paths(actual)
                for path in paths.values():
                    path.write_text('')
                paths['prompt'].write_text(prompt)
                if len(calls) == 1:
                    paths['raw'].write_text('{"usage":{"input_tokens":12}}\n')
                    paths['final'].write_text('incomplete response')
                    paths['meta'].write_text(json.dumps({
                        'model': options['model'], 'stage': options['stage'],
                        'timed_out': True, 'state': 'failed', 'returncode': 124,
                        'wall_ms': 1000,
                        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                    }))
                    raise RuntimeError('timeout')
                return 'complete'
            final, actual = call_with_retry(call, 'exact request', prefix,
                retry_gap=0, model='test', stage='Repository-Planning')
            self.assertEqual(final, 'complete')
            self.assertEqual(calls[0], calls[1])
            self.assertNotEqual(actual, prefix)
            self.assertEqual(artifact_paths(prefix)['final'].read_text(), '')
            summary = json.loads(Path(str(artifact_paths(prefix)['meta']) + '.timeout-summary.json').read_text())
            self.assertEqual(summary['wall_ms'], 1000)
            self.assertEqual(summary['usage'], [{'input_tokens': 12}])

    def test_two_timeouts_pause_across_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / 'request'
            calls = []
            def call(prompt, actual, **options):
                calls.append(actual)
                for path in artifact_paths(actual).values():
                    path.write_text('')
                artifact_paths(actual)['meta'].write_text(json.dumps({
                    'model': options['model'], 'stage': options['stage'],
                    'timed_out': True, 'state': 'failed', 'returncode': 124,
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                }))
                raise RuntimeError('timeout')
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'two consecutive timeouts'):
                    call_with_retry(call, 'same', prefix, retry_gap=0,
                                    model='test', stage='Repository-Planning')
            self.assertEqual(len(calls), 2)

    def test_non_timeout_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def call(*args, **kwargs):
                calls.append(1)
                raise RuntimeError('transport unavailable')
            with self.assertRaisesRegex(RuntimeError, 'transport unavailable'):
                call_with_retry(call, 'same', Path(tmp) / 'request', retry_gap=0,
                                model='test', stage='Repository-Planning')
            self.assertEqual(len(calls), 1)
