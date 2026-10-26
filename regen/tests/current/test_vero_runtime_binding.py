"""A resumed agent must execute the audited release's transport and client."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from regen.tools.vero_serial_application import runtime_agent_environment


class VeroRuntimeBindingTests(unittest.TestCase):
    def test_frozen_environment_cannot_select_old_transport_from_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'old-release'
            package = old / 'regen' / 'tools'
            package.mkdir(parents=True)
            (old / 'regen' / '__init__.py').write_text('')
            (package / '__init__.py').write_text('')
            (package / 'vero_isolated_exec.py').write_text('OLD_TRANSPORT = True\n')
            workspace = root / 'workspace'
            workspace.mkdir()
            config = {'env': {'PYTHONPATH': str(old), 'SENTINEL': 'preserved'}}
            before = deepcopy(config)
            probe = ('import json, regen.tools.vero_isolated_exec as transport; '
                     'from pathlib import Path; '
                     'print(json.dumps({"transport": transport.__file__, '
                     '"client": str(Path(transport.__file__).with_name('
                     '"vero_application_tool.py").resolve())}))')
            inherited = {**os.environ, **config['env']}
            baseline = json.loads(subprocess.check_output(
                [sys.executable, '-c', probe], cwd=workspace, env=inherited))
            self.assertEqual(Path(baseline['transport']).parent, package)
            runtime = runtime_agent_environment(config)
            result = json.loads(subprocess.check_output(
                [sys.executable, '-c', probe], cwd=workspace,
                env={**os.environ, **runtime}))
            actual_tools = Path(__file__).resolve().parents[2] / 'tools'
            self.assertEqual(Path(result['transport']), actual_tools / 'vero_isolated_exec.py')
            self.assertEqual(Path(result['client']), actual_tools / 'vero_application_tool.py')
            self.assertEqual(runtime['SENTINEL'], 'preserved')
            self.assertEqual(config, before)


if __name__ == '__main__':
    unittest.main()
