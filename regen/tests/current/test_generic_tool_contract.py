import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from jsonschema import Draft202012Validator
from regen.planning.generic_codex_agent import Controller,registry,instructions
from regen.planning.parallel_file_planner import TheoryController,tools
from regen.planning.document_interface import tool_contracts
from regen.planning.repository_plan_contract import schema_help
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import ActivityStop,UsageSnapshots,observed_usage
from regen.infrastructure.workspace_transcript import approved_registry


class GenericContractTests(unittest.TestCase):
    def test_help_is_shared_registered_and_does_not_open_a_theory(self):
        shared=tools(True);generic=registry()
        self.assertEqual([t['name'] for t in generic[:len(shared)]],[t['name'] for t in shared])
        shared_action=next(t for t in shared if t['name']=='proof_action')
        generic_action=next(t for t in generic if t['name']=='proof_action')
        self.assertEqual(shared_action['inputSchema']['properties']['proposal'],{'type':'object'})
        self.assertIn('oneOf',generic_action['inputSchema']['properties']['proposal'])
        self.assertIn('proof_tool_help',[t['name'] for t in shared])
        self.assertTrue(approved_registry([{'type':'function',**t} for t in shared]))
        worker=object.__new__(TheoryController);worker.document_mode=True
        worker._document=Mock(side_effect=AssertionError('help must not initialize native context'))
        self.assertEqual(worker._handle('proof_tool_help',{},{}),tool_contracts())
        worker._document.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            c=object.__new__(Controller);c.current=None;c.ledger=Ledger(tmp,'test');c.stage=ActivityStop(c.ledger)
            c.scope={'target_ids':[]};c.controller=Mock(side_effect=AssertionError('unexpected theory'))
            self.assertEqual(c.handle('proof_tool_help',{},{}),tool_contracts())
            c.controller.assert_not_called()

    def test_help_examples_and_edit_exclusivity(self):
        help=tool_contracts()
        for example in help['proof_action_examples']:
            proposal=example['proposal'];schema=help['source_action_schemas'].get(proposal['action'])
            if schema:Draft202012Validator(schema).validate(proposal)
        schema=help['source_action_schemas']['edit_theory']
        invalid={'action':'edit_theory','base_sha256':'x','source':'a','edits':[{'old':'a','new':'b'}]}
        self.assertFalse(Draft202012Validator(schema).is_valid(invalid))
        actions={e['proposal']['action'] for e in help['proof_action_examples']}
        self.assertTrue({'query','read_proof_state','read_diagnostic','resolve_symbols','edit_theory','check_theory','finish'}<=actions)

    def test_generic_proof_action_schema_enforces_source_actions_and_observation_names(self):
        schema=next(t for t in registry() if t['name']=='proof_action')['inputSchema']
        valid=[
            {'theory':'T','proposal':{'action':'edit_theory','base_sha256':'x','source':'theory T imports Main begin end'}},
            {'theory':'T','proposal':{'action':'check_theory','source_sha256':'x'}},
            {'theory':'T','proposal':{'action':'finish','source_sha256':'x'}},
            {'theory':'T','proposal':{'action':'query','request':{'premise_query':'x'}}},
        ]
        for value in valid:Draft202012Validator(schema).validate(value)
        invalid=[
            {'theory':'T','proposal':{'action':'edit_theory','base_sha256':'x','source':'a','edits':[{'old':'a','new':'b'}]}},
            {'theory':'T','proposal':{'action':'submit_batch','proofs':{}}},
            {'theory':'T','proposal':{'action':'finish'}},
        ]
        for value in invalid:self.assertFalse(Draft202012Validator(schema).is_valid(value))

    def test_prompt_uses_configured_stop_and_neutral_schema_help(self):
        self.assertIn('123 seconds',instructions(123))
        self.assertNotIn('7200',instructions(123))
        self.assertNotIn('two hours',instructions(123))
        self.assertNotIn('normally needs multiple',schema_help())
        self.assertNotIn('Return JSON only',schema_help())
        self.assertNotIn('File Planner',schema_help())
        c=object.__new__(Controller)
        c.planner=SimpleNamespace(view=SimpleNamespace(to_original={},payload={'context_profiles':[]}))
        result=c.plan({'action':'schema'},{} )
        self.assertEqual(result['schema_help'],schema_help(allow_revision=False))
        self.assertNotIn('begin_revision',result['tool_contract']['operations'])
        self.assertNotIn('replacement',result['schema_help'])
        self.assertNotIn('replacement',str(result['tool_contract']))

    def test_prompt_distinguishes_proof_feedback_from_infrastructure_and_quota(self):
        for value in (instructions(), instructions(effective_seconds=7200)):
            self.assertIn('candidate-execution timeout', value)
            self.assertIn('infrastructure blocker, not a', value)
            self.assertIn('Authentication or model-quota exhaustion', value)
            self.assertIn('supervising harness handles provider connection retries', value)
            self.assertIn('do not assume its write failed or replay it blindly', value)
            self.assertIn('not remaining account quota', value)
            self.assertIn('agent-enforced token cap', value)

    def test_generic_roles_fail_before_publication_and_publication_failure_pauses(self):
        from regen.tools.generic_serial_application import validate_generic_model_roles
        with self.assertRaisesRegex(ValueError,'File Planner role'):
            validate_generic_model_roles({'role_models':{'VERO Planner':'gpt-5.6-sol'}})
        self.assertEqual(validate_generic_model_roles(
            {'role_models':{'File Planner':'gpt-5.6-sol'}})['File Planner'],'gpt-5.6-sol')
        c=object.__new__(Controller)
        c.current=None;c.planner=Mock();c.planner.dispatch.return_value={'submitted':True}
        c._publish=Mock(side_effect=ValueError('invalid explicit execution role models'))
        c.ledger=Mock();c.stage=Mock()
        with patch('regen.planning.generic_codex_agent.validate_request'):
            with self.assertRaisesRegex(ValueError,'invalid explicit execution role models'):
                c.plan({'action':'submit','proposal':{}},{'call_id':'test'})
        self.assertEqual(c.infrastructure_blocker['phase'],'publish')
        c.stage.cancel.assert_called_once_with('repository_plan_publication_error')

    def test_status_uses_snapshots_without_adding_response_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'test');usage=UsageSnapshots(ledger)
            self.assertIsNone(observed_usage(ledger)['total_tokens'])
            usage.record('a',{'inputTokens':100,'outputTokens':10,'totalTokens':110},scope={},source='test')
            ledger.response({'responseId':'r1','threadId':'a','turnId':'t','usage':{'inputTokens':100,'outputTokens':10,'totalTokens':110}},
                scope={},invocation='i',model='test',effort='medium')
            self.assertEqual(observed_usage(ledger)['total_tokens'],110)
            c=object.__new__(Controller);c.root=Path(tmp);c.ledger=ledger;c.stage=ActivityStop(ledger)
            c.config={'run_id':'test','model':'gpt-6-astra','effort':'medium'}
            c.current=None;c.draft={};c.scope={}
            self.assertEqual(c.status()['reported_tokens'],110)
            self.assertTrue(c.status()['fresh_only'])
            ledger.response({'responseId':'r2','threadId':'b','turnId':'t','usage':None},
                scope={},invocation='i',model='test',effort='medium')
            self.assertIsNone(c.status()['reported_tokens'])

    def test_legacy_registry_remains_auditable_without_weakening_current_help(self):
        from copy import deepcopy
        current=[{'type':'function',**t} for t in registry()]
        malformed=deepcopy(current)
        next(t for t in malformed if t['name']=='proof_tool_help')['inputSchema']={}
        self.assertFalse(approved_registry(malformed))
        missing=[t for t in current if t['name']!='proof_tool_help']
        self.assertFalse(approved_registry(missing))
        next(t for t in missing if t['name']=='experiment_status')['description']='Read deadline, accounting, plan and previous revision identities; optional notes persist across resume.'
        self.assertTrue(approved_registry(missing))

    def test_preflight_budget_is_independent_of_candidate_budget(self):
        from unittest.mock import patch
        c=object.__new__(Controller);c.dataset=Path('/dataset');c.planning_root=Path('/plan')
        for configured,expected in [(None,900),(777,777)]:
            c.config={'check_timeout':120,'threads':4}
            if configured is not None:c.config['preflight_seconds']=configured
            with patch('regen.planning.generic_codex_agent.native_checker') as native,patch('regen.planning.generic_codex_agent.WorkspaceHarness'):
                c._planner()
                native.assert_called_once_with(c.dataset,expected,threads=4)
