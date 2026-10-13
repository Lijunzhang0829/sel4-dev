import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.planning.planner_owner_guard import owner_identity,validate_private,watch


class PlannerOwnerGuardTests(unittest.TestCase):
    def test_identity_and_broad_path_rejection(self):
        self.assertTrue(owner_identity(os.getpid()).isdigit())
        for path in ('/', '/tmp', str(Path.home())):
            with self.assertRaises(ValueError):validate_private(path)

    def test_owner_death_removes_only_owned_directory_and_container(self):
        with tempfile.TemporaryDirectory(prefix='regen-planner-private-') as temp:
            directory=Path(temp)
            (directory/'home').mkdir();(directory/'home/auth.json').write_text('fixture')
            name='regen-model-'+'a'*32
            with patch('regen.planning.planner_owner_guard.owner_identity',return_value=None), \
                 patch('regen.execution.native_container.remove_owned') as remove:
                watch(directory,123,'bound-start-time',name)
            remove.assert_called_once_with(name)
            self.assertFalse(directory.exists())

    def test_container_failure_keeps_private_data_for_explicit_recovery(self):
        with tempfile.TemporaryDirectory(prefix='regen-planner-private-') as temp:
            with patch('regen.planning.planner_owner_guard.owner_identity',return_value=None), \
                 patch('regen.execution.native_container.remove_owned',side_effect=RuntimeError('daemon unavailable')):
                with self.assertRaises(RuntimeError):watch(temp,123,'old','regen-model-'+'a'*32)
            self.assertTrue(Path(temp).exists())
