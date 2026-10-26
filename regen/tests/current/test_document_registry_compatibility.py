"""Frozen registries stay auditable without weakening current document actions."""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from regen.infrastructure.workspace_transcript import approved_registry
from regen.planning.document_interface import tool_contracts
from regen.planning.generic_codex_agent import Controller, registry


def historical_registry(*, proof_edit=True, history=True, delta=True, sketch=True, help=True):
    """Reconstruct exported historical contracts independently of audit helpers."""
    tools = deepcopy([{'type': 'function', **tool} for tool in registry()])
    if not history:
        tools = [tool for tool in tools if tool['name'] != 'list_attempts']
    if not help:
        tools = [tool for tool in tools if tool['name'] != 'proof_tool_help']
    for tool in tools:
        properties = tool['inputSchema']['properties']
        if tool['name'] == 'read_attempt' and not history:
            tool['description'] = 'Read a bound failed candidate and feedback in any theory as reference only.'
            properties.pop('view', None)
        if tool['name'] == 'read_theory' and not delta:
            properties.pop('if_source_sha256', None)
        if tool['name'] == 'experiment_status' and not help:
            tool['description'] = 'Read deadline, accounting, plan and previous revision identities; optional notes persist across resume.'
        if tool['name'] != 'proof_action':
            continue
        proposal = properties['proposal']
        if not proof_edit:
            proposal['description'] = (
                'Source-changing actions use the exact edit_theory/check_theory/finish schemas. '
                'The enumerated observation actions remain read-only and receive their detailed '
                'runtime contracts from proof_tool_help.')
        actions = []
        for action in proposal['oneOf']:
            action_properties = action['properties']
            kind = action_properties['action'].get('const')
            if not proof_edit and kind in {'read_entry', 'edit_proof'}:
                continue
            if not sketch and kind in {'check_sketch', 'read_sketch'}:
                continue
            if not sketch and kind == 'edit_theory':
                action_properties.pop('sketches', None)
            if not sketch and kind == 'check_theory':
                action_properties.pop('entry_ids', None)
            if not delta and 'enum' in action_properties['action']:
                action_properties['action']['enum'].remove('read_context_index')
            actions.append(action)
        proposal['oneOf'] = actions
    return tools


class DocumentRegistryCompatibilityTests(unittest.TestCase):
    def test_current_and_exact_pre_proof_edit_registries_retain_historical_compatibility(self):
        for values in product((True, False), repeat=5):
            flags = dict(zip(('proof_edit', 'history', 'delta', 'sketch', 'help'), values))
            with self.subTest(**flags):
                self.assertTrue(approved_registry(historical_registry(**flags)))

    def test_historical_compatibility_does_not_approve_tampered_or_partial_schemas(self):
        for proof_edit in (True, False):
            original = historical_registry(proof_edit=proof_edit)
            tampered = deepcopy(original)
            next(tool for tool in tampered if tool['name'] == 'proof_action')['inputSchema'] = {'type': 'object'}
            self.assertFalse(approved_registry(tampered))
            tampered = deepcopy(original)
            next(tool for tool in tampered if tool['name'] == 'proof_tool_help')['description'] += ' changed'
            self.assertFalse(approved_registry(tampered))
            tampered = [tool for tool in original if tool['name'] != 'proof_tool_help']
            self.assertFalse(approved_registry(tampered))
        original = historical_registry()
        proposal = next(tool for tool in original if tool['name'] == 'proof_action')['inputSchema']['properties']['proposal']
        proposal['oneOf'] = [action for action in proposal['oneOf']
                            if action['properties']['action'].get('const') != 'edit_proof']
        self.assertFalse(approved_registry(original))
        old = historical_registry(proof_edit=False)
        old_proposal = next(tool for tool in old if tool['name'] == 'proof_action')['inputSchema']['properties']['proposal']
        old_proposal['description'] = proposal['description']
        self.assertFalse(approved_registry(old))

    def test_pending_seal_allows_entry_read_but_still_blocks_proof_edits(self):
        controller = object.__new__(Controller)
        controller.stage = SimpleNamespace(check=Mock(), event=SimpleNamespace(is_set=lambda: True))
        controller.ledger = Mock()
        controller.scope = {'target_ids': []}
        controller.seal_due = Mock(return_value=['Demo'])
        worker = Mock()
        worker.handle.return_value = {'entry_id': 'contract:one', 'expected_hash': 'sha'}
        controller.controller = Mock(return_value=worker)
        metadata = {'call_id': 'read-call'}
        args = {'theory': 'Demo', 'proposal': {'action': 'read_entry', 'entry_id': 'contract:one'}}
        self.assertEqual(controller.handle('proof_action', args, metadata), worker.handle.return_value)
        worker.handle.assert_called_once_with('proof_action', args, metadata)
        worker.handle.reset_mock()
        args['proposal'] = {'action': 'edit_proof', 'entry_id': 'contract:one',
                            'expected_hash': 'sha', 'proof_body': 'by simp'}
        with self.assertRaisesRegex(ValueError, 'Complete GREEN drafts must attempt seal first'):
            controller.handle('proof_action', args, {'call_id': 'edit-call'})
        worker.handle.assert_not_called()

    def test_entry_help_describes_check_submission_without_promising_no_initialization(self):
        description = tool_contracts()['source_action_schemas']['read_entry']['description']
        self.assertIn('does not submit a candidate proof check', description)
        self.assertNotIn('no Isabelle execution', description)


if __name__ == '__main__':
    unittest.main()
