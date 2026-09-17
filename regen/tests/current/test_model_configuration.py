import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from regen.infrastructure.audited_llm import call_codex
from regen.execution.proof_session_runner import frozen_role_model

class ModelConfigurationTest(unittest.TestCase):
    def settings(self):
        return {'codex_bin':'/bin/true','codex_sha256':hashlib.sha256(Path('/bin/true').resolve().read_bytes()).hexdigest(),'reasoning_effort':'high'}

    def test_explicit_medium_overrides_configuration_and_is_audited(self):
        def complete(argv, **kwargs):
            Path(argv[argv.index('-o')+1]).write_text('READY')
            return subprocess.CompletedProcess(argv,0,'{"type":"turn.completed"}\n','')
        with tempfile.TemporaryDirectory() as tmp, patch('regen.infrastructure.audited_llm.model_settings',return_value=self.settings()), patch('regen.infrastructure.audited_llm.subprocess.run',side_effect=complete) as run:
            prefix=Path(tmp)/'call';call_codex('test',prefix,model='gpt-6-astra',clean_runtime=True,reasoning_effort='medium')
            meta=json.loads(prefix.with_suffix('.meta.json').read_text())
            self.assertEqual(meta['reasoning_effort'],'medium')
            self.assertIn('model_reasoning_effort="medium"',run.call_args.args[0])
            self.assertEqual(meta['codex_sha256'],self.settings()['codex_sha256'])

    def test_changed_default_binary_is_rejected_before_model_call(self):
        settings={**self.settings(),'codex_sha256':'wrong'}
        with tempfile.TemporaryDirectory() as tmp, patch('regen.infrastructure.audited_llm.model_settings',return_value=settings), patch('regen.infrastructure.audited_llm.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError,'CLI changed'):call_codex('test',Path(tmp)/'call',clean_runtime=True)
            run.assert_not_called()

    def test_run_frozen_model_takes_precedence_over_environment_default(self):
        self.assertEqual(frozen_role_model({'execution_models':{'Lemma Prover':'gpt-6-astra'}},'Lemma Prover'),'gpt-6-astra')
