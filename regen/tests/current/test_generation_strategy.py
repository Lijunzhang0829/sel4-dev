"""Workflow A/B changes only an explicitly frozen generic prompt suffix."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning import parallel_file_planner as planner


class GenerationStrategyTest(unittest.TestCase):
    def test_baseline_is_empty_and_variants_are_bound(self):
        self.assertEqual(planner.generation_strategy('baseline')['instructions'], '')
        with self.assertRaisesRegex(ValueError,'removed.*frozen'):
            planner.generation_strategy('and-or-helper-selection-v1')
        value=planner.generation_strategy('helper-closure-v1')
        self.assertEqual(value['instructions_sha256'],hashlib.sha256(value['instructions'].encode()).hexdigest())
        for target in ('schedule_corres','deleteObjects_corres','ul_cap_zrefs'):
            self.assertNotIn(target,value['instructions'])
        with self.assertRaisesRegex(ValueError,'unknown generation strategy'):
            planner.generation_strategy('unreviewed')

    def test_real_session_entry_binds_strategy_without_altering_tools(self):
        prompts=[]
        class Controller:
            options={};run={'run_id':'test'};model='gpt-6-astra';task_ids=set()
            def __init__(self,*a,**kw):pass
            def handle(self,*a):return {'theories':[]}
            def _state(self,*a):return {},{'status':'sealed'}
            def target_contract_green(self):return False
            def close(self):pass
        class Session:
            tool_call_count=0
            def __init__(self,**kw):self.tools=kw['tools']
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def run(self,prompt,handler,**kw):
                prompts.append((prompt,self.tools,kw['model'],kw['reasoning_effort']))
                return {'status':'completed','progress_made':True}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(planner,'TheoryController',Controller), \
                patch('regen.infrastructure.codex_workspace_session.CodexWorkspaceSession',Session), \
                patch('regen.infrastructure.workspace_transcript.audit_workspace_session',return_value={'terminal':True}):
            for name in ('baseline','helper-closure-v1'):
                folder=Path(tmp)/name
                planner.run_theory_session(Path(tmp),'Test',resources=type('R',(),{'policy':{}})(),
                    executor=object(), settings={'codex_bin':'codex','reasoning_effort':'medium'},
                    directory=folder,generation_strategy_name=name)
                value=json.loads((folder/'policy.json').read_text())
                self.assertEqual(value['generation_strategy'],planner.generation_strategy(name))
        suffix=planner.generation_strategy('helper-closure-v1')['instructions']
        self.assertEqual(prompts[1][0].replace(suffix,''),prompts[0][0])
        self.assertEqual(prompts[0][1:],prompts[1][1:])
        self.assertNotIn('AND/OR HELPER SELECTION',prompts[0][0])
        self.assertIn('batch several proofs',prompts[0][0])

    def test_unknown_strategy_rejected_before_resource_or_model_start(self):
        with patch.object(planner,'NativeResources') as native:
            with self.assertRaisesRegex(ValueError,'unknown generation strategy'):
                planner.run_parallel_workspace(Path('/unused'),allow_external_call=True,
                                               generation_strategy_name='unreviewed')
            native.assert_not_called()


if __name__=='__main__':unittest.main()
