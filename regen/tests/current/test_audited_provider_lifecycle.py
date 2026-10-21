"""Local dummy processes only: streaming evidence and owned process-group cleanup."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from regen.infrastructure.audited_llm import _run_provider, call_codex
from regen.infrastructure.llm_transcript import (
    call_codex as transcript_call, transcript_health, validate_call_artifact,
)


class AuditedProviderLifecycleTest(unittest.TestCase):
    def fake(self, root, mode):
        script=root/'dummy-codex'
        script.write_text('#!'+sys.executable+'\n'+f'''
import json,os,signal,subprocess,sys,time
from pathlib import Path
mode={mode!r}
root=Path(__file__).parent
sys.stdin.read()
if mode in ('hang','interrupt'):
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    (root/'child.pid').write_text(str(child.pid))
print(json.dumps({{'type':'turn.completed'}}),flush=True)
print('live stderr',file=sys.stderr,flush=True)
if mode=='interrupt':
    os.kill(os.getppid(),signal.SIGINT)
if mode in ('hang','interrupt'):
    time.sleep(60)
if '-o' in sys.argv:
    Path(sys.argv[sys.argv.index('-o')+1]).write_text('READY')
''')
        script.chmod(0o755)
        return script

    def alive(self,pid):
        try:
            text=Path('/proc')/str(pid)/'stat'
            return text.read_text().rsplit(')',1)[1].split()[0] not in {'Z','X'}
        except FileNotFoundError:
            return False

    def test_output_files_are_visible_before_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'prompt';source.write_text('test')
            script=root/'stream.py';script.write_text("import sys,time;sys.stdin.read();print('live',flush=True);print('error',file=sys.stderr,flush=True);time.sleep(.4)")
            raw=root/'raw';err=root/'err'
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(_run_provider,[sys.executable,str(script)],prompt_path=source,
                    raw_path=raw,stderr_path=err,timeout=5)
                deadline=time.monotonic()+3
                while (not raw.exists() or not raw.read_text()) and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(raw.read_text(),'live\n')
                self.assertEqual(err.read_text(),'error\n')
                self.assertFalse(future.done())
                self.assertEqual(future.result(timeout=3)['returncode'],0)

    def test_success_remains_hash_checked_and_auditable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=self.fake(root,'success');prefix=root/'call'
            with patch.dict(os.environ,{'REGEN_CODEX_BIN':str(fake)}):
                self.assertEqual(transcript_call('test',prefix,timeout=5,clean_runtime=True),'READY')
            meta=validate_call_artifact(prefix)
            self.assertTrue(meta['sealable_generation'])
            self.assertEqual(meta['state'],'complete')
            self.assertGreater(meta['raw_bytes'],0)
            self.assertGreater(meta['stderr_bytes'],0)
            self.assertFalse(meta['interrupted'])

    def test_timeout_preserves_partial_output_and_kills_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=self.fake(root,'hang');prefix=root/'call'
            with self.assertRaisesRegex(RuntimeError,'exit code 124'):
                call_codex('test',prefix,codex_bin=fake,timeout=.4,clean_runtime=True)
            meta=json.loads(prefix.with_suffix('.meta.json').read_text())
            self.assertEqual(meta['state'],'failed');self.assertTrue(meta['timed_out'])
            self.assertFalse(meta['sealable_generation'])
            self.assertIn('turn.completed',prefix.with_suffix('.ndjson').read_text())
            self.assertFalse(self.alive(meta['pid']))
            child=int((root/'child.pid').read_text())
            deadline=time.monotonic()+2
            while self.alive(child) and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(self.alive(child))

    def test_keyboard_interrupt_is_terminal_and_cleans_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=self.fake(root,'interrupt');prefix=root/'call'
            with patch.dict(os.environ,{'REGEN_CODEX_BIN':str(fake)}), self.assertRaises(KeyboardInterrupt):
                transcript_call('test',prefix,timeout=5,clean_runtime=True)
            meta=json.loads(prefix.with_suffix('.meta.json').read_text())
            self.assertEqual(meta['state'],'interrupted');self.assertFalse(meta['sealable_generation'])
            self.assertEqual(transcript_health(prefix)['state'],'interrupted')
            self.assertEqual(meta['signal_number'],signal.SIGINT)
            child=int((root/'child.pid').read_text())
            deadline=time.monotonic()+2
            while self.alive(child) and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(self.alive(child))
            self.assertFalse(self.alive(meta['pid']))

    def test_external_sigterm_records_terminal_state_and_cleans_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=self.fake(root,'hang');prefix=root/'call'
            code=('from pathlib import Path; from regen.infrastructure.llm_transcript import call_codex; '
                  f'call_codex("test",Path({str(prefix)!r}),timeout=30,clean_runtime=True)')
            env={**os.environ,'REGEN_CODEX_BIN':str(fake)}
            caller=subprocess.Popen([sys.executable,'-c',code],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+5
                while not (root/'child.pid').exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue((root/'child.pid').exists())
                caller.send_signal(signal.SIGTERM);caller.wait(timeout=5)
                meta=json.loads(prefix.with_suffix('.meta.json').read_text())
                self.assertEqual(meta['state'],'interrupted');self.assertEqual(meta['signal_number'],signal.SIGTERM)
                self.assertFalse(meta['sealable_generation'])
                self.assertEqual(transcript_health(prefix)['state'],'interrupted')
                child=int((root/'child.pid').read_text())
                deadline=time.monotonic()+2
                while self.alive(child) and time.monotonic()<deadline:time.sleep(.01)
                self.assertFalse(self.alive(child));self.assertFalse(self.alive(meta['pid']))
            finally:
                if caller.poll() is None:caller.kill();caller.wait(timeout=5)

if __name__=='__main__':unittest.main()
