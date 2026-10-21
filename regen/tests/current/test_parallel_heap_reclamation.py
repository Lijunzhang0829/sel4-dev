"""Coordinator cache reclamation requires a sealed, completed provider session."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.planning.parallel_file_planner import _reclaim_completed_heaps


class ParallelHeapReclamationTests(unittest.TestCase):
    def test_only_sealed_and_completed_sessions_are_eligible(self):
        for status in ('sealed', 'generating', 'retired', 'infrastructure_error'):
            for provider in ({}, {'status':'failed'}, {'status':'completed'}):
                result = {'theory':'Target', 'status':status, 'provider':provider}
                expected = status == 'sealed' and provider.get('status') == 'completed'
                with self.subTest(status=status, provider=provider), patch(
                        'regen.infrastructure.resource_budget.reclaim_theory_heaps',
                        return_value={'status':'reclaimed'}) as reclaim:
                    _reclaim_completed_heaps(Path('/test-run'), result)
                    self.assertEqual(reclaim.call_count, int(expected))
                    if expected:
                        reclaim.assert_called_once_with(Path('/test-run'), 'Target')
                        self.assertEqual(result['heap_reclamation']['status'], 'reclaimed')
                    else:
                        self.assertNotIn('heap_reclamation', result)

    def test_reclamation_warning_does_not_change_seal_or_provider_result(self):
        result = {'theory':'Target', 'status':'sealed', 'provider':{'status':'completed'}}
        original = deepcopy(result)
        with patch('regen.infrastructure.resource_budget.reclaim_theory_heaps',
                   return_value={'status':'warning', 'diagnostic':'injected audit error'}):
            _reclaim_completed_heaps(Path('/test-run'), result)
        self.assertEqual({key:result[key] for key in original}, original)
        self.assertEqual(result['heap_reclamation']['status'], 'warning')


if __name__ == '__main__':
    unittest.main()
