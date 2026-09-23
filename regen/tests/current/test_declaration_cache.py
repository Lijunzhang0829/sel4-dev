"""Declaration certificates survive proof failure, never context changes or restart."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from regen.execution.declaration_cache import checked_declarations
from regen.execution.proof_commits import freeze_evidence


class NativeStub:
    def __init__(self, root):
        self.declaration_environment = 'frozen-run-implementation'
        self.image='sha256:image'; self.architecture='ARM'
        self.base_session='Base'; self.base_import='Base.Root'; self.base_catalog='/base'
        for name in ('heaps_dir','l4v_dir','semantic_workspace','isabelle_dir','generated_dir'):
            path = root/name; path.mkdir(exist_ok=True); setattr(self,name,path)
        self.work = root/'work'; self.work.mkdir(exist_ok=True)
        self.calls=0; self.verdict='green'
    def imports(self):
        return [self.base_import]
    def _check(self, source, name):
        self.calls += 1
        path=self.work/(name+'.thy'); path.write_text(source)
        log=self.work/(name+'.log'); log.write_text(self.verdict)
        return {'verdict':self.verdict,'returncode':0 if self.verdict=='green' else 1,
                'source':str(path),'log':str(log),'wall_ms':123,'diagnostic':self.verdict}


class DeclarationCacheTests(unittest.TestCase):
    def test_reuse_keeps_original_bytes_after_failed_check_overwrites_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=NativeStub(Path(tmp))
            first=checked_declarations(c,'source A','Graph_Check_v2')
            c.verdict='red'
            checked_declarations(c,'source B','Graph_Check_v2')
            hit=checked_declarations(c,'source A','Graph_Check_v2')
            self.assertEqual(c.calls,2)
            self.assertTrue(hit['declaration_cache']['hit'])
            self.assertEqual(hit['wall_ms'],0)
            frozen=freeze_evidence(hit)
            self.assertEqual(frozen['source_content'],'source A')
            self.assertEqual(frozen['log_content'],'green')
            hit['diagnostic']='mutated caller'
            self.assertEqual(checked_declarations(c,'source A','Graph_Check_v2')['diagnostic'],'green')
            # Failed declarations are never certificates.
            checked_declarations(c,'source B','Graph_Check_v2')
            self.assertEqual(c.calls,3)

    def test_each_environment_input_invalidates_and_restart_does_not_trust_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=NativeStub(root)
            checked_declarations(c,'source','Graph_Check_v2')
            for name in ('heaps_dir','l4v_dir','semantic_workspace','isabelle_dir','generated_dir'):
                (getattr(c,name)/'input').write_text('changed')
                result=checked_declarations(c,'source','Graph_Check_v2')
                self.assertFalse(result['declaration_cache']['hit'],name)
            c.base_import='Other.Root'
            self.assertFalse(checked_declarations(c,'source','Graph_Check_v2')['declaration_cache']['hit'])
            c.declaration_environment='other implementation'
            self.assertFalse(checked_declarations(c,'source','Graph_Check_v2')['declaration_cache']['hit'])
            self.assertEqual(c.calls,8)
            restarted=NativeStub(root)
            self.assertFalse(checked_declarations(restarted,'source','Graph_Check_v2')['declaration_cache']['hit'])
            self.assertEqual(restarted.calls,1)

    def test_unbound_checker_and_drift_during_check_do_not_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=NativeStub(Path(tmp)); c.declaration_environment=None
            for _ in range(2): checked_declarations(c,'source','Graph_Check_v2')
            self.assertEqual(c.calls,2)
            c.declaration_environment='bound'
            original=c._check
            def changing(source,name):
                result=original(source,name)
                (c.l4v_dir/'changed').write_text(str(c.calls))
                return result
            c._check=changing
            for _ in range(2): checked_declarations(c,'source','Graph_Check_v2')
            self.assertEqual(c.calls,4)
