"""Audit the real pre-import registry, independent of today's action schemas."""
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path
import unittest

from regen.infrastructure.workspace_transcript import (
    PRE_IMPORT_GENERIC_REGISTRY_SHA256, approved_registry,
)
from regen.planning.generic_codex_agent import registry


def frozen_registry(**flags):
    tools = json.loads((Path(__file__).parent/'fixtures'/'generic-registry-763b620.json').read_text())
    if not flags.get('history', True):
        tools = [tool for tool in tools if tool['name'] != 'list_attempts']
    if not flags.get('help', True):
        tools = [tool for tool in tools if tool['name'] != 'proof_tool_help']
    for tool in tools:
        properties = tool['inputSchema']['properties']
        if tool['name'] == 'read_attempt' and not flags.get('history', True):
            tool['description'] = 'Read a bound failed candidate and feedback in any theory as reference only.'
            properties.pop('view')
        if tool['name'] == 'read_theory' and not flags.get('delta', True):
            properties.pop('if_source_sha256')
        if tool['name'] == 'experiment_status' and not flags.get('help', True):
            tool['description'] = 'Read deadline, accounting, plan and previous revision identities; optional notes persist across resume.'
        if tool['name'] != 'proof_action':
            continue
        proposal = properties['proposal']
        if not flags.get('proof_edit', True):
            proposal['description'] = (
                'Source-changing actions use the exact edit_theory/check_theory/finish schemas. '
                'The enumerated observation actions remain read-only and receive their detailed '
                'runtime contracts from proof_tool_help.')
        actions = []
        for action in proposal['oneOf']:
            action_properties = action['properties']
            kind = action_properties['action'].get('const')
            if not flags.get('proof_edit', True) and kind in {'read_entry', 'edit_proof'}:
                continue
            if not flags.get('sketch', True) and kind in {'check_sketch', 'read_sketch'}:
                continue
            if not flags.get('sketch', True) and kind == 'edit_theory':
                action_properties.pop('sketches')
            if not flags.get('sketch', True) and kind == 'check_theory':
                action_properties.pop('entry_ids')
            if not flags.get('delta', True) and 'enum' in action_properties['action']:
                action_properties['action']['enum'].remove('read_context_index')
            actions.append(action)
        proposal['oneOf'] = actions
    return tools


class ImportRegistryCompatibilityTests(unittest.TestCase):
    def test_actual_published_registry_and_its_32_reviewed_variants_remain_auditable(self):
        fingerprints = set()
        for values in product((True, False), repeat=5):
            flags = dict(zip(('proof_edit', 'history', 'delta', 'sketch', 'help'), values))
            with self.subTest(**flags):
                tools = frozen_registry(**flags)
                fingerprints.add(hashlib.sha256(json.dumps(tools, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())
                self.assertTrue(approved_registry(tools))
        self.assertEqual(fingerprints, PRE_IMPORT_GENERIC_REGISTRY_SHA256)

    def test_reviewed_hashes_never_approve_modified_or_incomplete_old_registries(self):
        original = frozen_registry()
        changed = deepcopy(original)
        next(t for t in changed if t['name'] == 'proof_action')['inputSchema'] = {'type': 'object'}
        self.assertFalse(approved_registry(changed))
        changed = deepcopy(original)
        next(t for t in changed if t['name'] == 'repository_plan')['description'] += ' changed'
        self.assertFalse(approved_registry(changed))
        self.assertFalse(approved_registry(original[:-1]))
        self.assertFalse(approved_registry(original + [deepcopy(original[0])]))
        self.assertFalse(approved_registry(list(reversed(original))))

    def test_historical_audit_does_not_rewrite_or_replace_current_action_schemas(self):
        old = frozen_registry()
        before = deepcopy(old)
        self.assertTrue(approved_registry(old))
        self.assertEqual(old, before)
        current = [{'type': 'function', **tool} for tool in registry()]
        proposal = next(t for t in current if t['name'] == 'proof_action')['inputSchema']['properties']['proposal']
        edit = next(a for a in proposal['oneOf']
                    if a.get('properties', {}).get('action', {}).get('const') == 'edit_theory')
        self.assertIn('imports', edit['properties'])
        self.assertNotEqual(current, old)
        self.assertTrue(approved_registry(current))


if __name__ == '__main__':
    unittest.main()
