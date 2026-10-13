import subprocess
import unittest
from unittest.mock import patch

from regen.execution import native_container
from regen.execution.persistent_checker import PrefixWorker


class RemovalRaceTests(unittest.TestCase):
    def response(self, code, out='', err=''):
        return subprocess.CompletedProcess([],code,out,err)

    def test_hot_and_cold_races_wait_for_confirmed_absence(self):
        name='regen-owned-test'
        worker=object.__new__(PrefixWorker);worker.name=name
        for remove in (lambda:native_container.remove_owned(name),worker._remove_container):
            with self.subTest(remove=remove), \
                    patch('subprocess.run', side_effect=[
                        self.response(1,err=f'removal of container {name} is already in progress'),
                        self.response(0,'removing'),
                        self.response(1,err=f'Error: No such object: {name}')]) as run, \
                    patch.object(native_container.time,'sleep'):
                remove()
                self.assertEqual(run.call_count,3)
                self.assertEqual(run.call_args.args[0][-1],name)

    def test_existing_container_is_not_success_and_wait_is_bounded(self):
        with patch('subprocess.run',return_value=self.response(0,'removing')) as run, \
                patch.object(native_container.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'still exists'):
                native_container.wait_for_owned_removal('owned')
            self.assertEqual(run.call_count,10)

    def test_daemon_failure_or_another_missing_container_is_not_absence(self):
        for message in ('Cannot connect to daemon','No such object: different'):
            with self.subTest(message=message),patch('subprocess.run',return_value=self.response(1,err=message)):
                with self.assertRaisesRegex(RuntimeError,'cannot confirm'):
                    native_container.wait_for_owned_removal('owned')


if __name__=='__main__':unittest.main()
