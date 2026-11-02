"""Evidence UI evolution must preserve exact frozen registry acceptance."""
from copy import deepcopy
import hashlib
import json
import unittest

from regen.infrastructure.workspace_transcript import (
    _approved_generic_registry, _pre_delta_registry, _pre_history_registry,
    _pre_proof_edit_registry, _pre_retrieval_registry, _pre_sketch_registry,
    approved_registry,
)
from regen.planning.evidence_reads import evidence_schema
from regen.planning.generic_codex_agent import registry


# Canonical digest of the actual complete generic registry captured before the
# read_evidence change, not a fixture reconstructed from today's inverse mapping.
PRE_RETRIEVAL_SHA256 = '73d1eaf59eac4e23d405bcde5ddab2ddbe777e1aa6c064af798801f62dbea13a'


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def alternatives(tools):
    return next(tool for tool in tools if tool['name'] == 'proof_action')[
        'inputSchema']['properties']['proposal']['oneOf']


def old_variants(original):
    for proof_edit in (original, _pre_proof_edit_registry(original)):
        legacy = deepcopy([tool for tool in proof_edit if tool['name'] != 'proof_tool_help'])
        next(tool for tool in legacy if tool['name'] == 'experiment_status')['description'] = (
            'Read deadline, accounting, plan and previous revision identities; '
            'optional notes persist across resume.')
        for help_version in (proof_edit, legacy):
            for sketch in (help_version, _pre_sketch_registry(help_version)):
                for delta in (sketch, _pre_delta_registry(sketch)):
                    yield delta
                    yield _pre_history_registry(delta)


class EvidenceRegistryCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.current = [{'type': 'function', **tool} for tool in registry()]
        self.previous = _pre_retrieval_registry(self.current)

    def test_exact_pre_retrieval_golden_registry_is_recovered(self):
        self.assertEqual(canonical_digest(self.previous), PRE_RETRIEVAL_SHA256)
        self.assertIn(evidence_schema(), alternatives(self.current))
        self.assertNotIn(evidence_schema(), alternatives(self.previous))
        self.assertTrue(approved_registry(self.current))
        self.assertTrue(approved_registry(self.previous))

    def test_every_preexisting_historical_variant_remains_approved(self):
        variants = list(old_variants(self.previous))
        self.assertEqual(len(variants), 32)
        self.assertEqual(len({canonical_digest(value) for value in variants}), 32)
        for index, value in enumerate(variants):
            with self.subTest(variant=index):
                self.assertTrue(approved_registry(value))

    def test_old_registry_mutations_do_not_gain_approval(self):
        for mutation in ('unknown_action', 'removed_read', 'loose_edit', 'changed_description'):
            with self.subTest(mutation=mutation):
                changed = deepcopy(self.previous)
                if mutation in {'unknown_action', 'removed_read'}:
                    observation = next(action for action in alternatives(changed)
                        if 'enum' in action.get('properties', {}).get('action', {}))
                    values = observation['properties']['action']['enum']
                    if mutation == 'unknown_action':
                        values.append('read_unreviewed')
                    else:
                        values.remove('read_queries')
                elif mutation == 'loose_edit':
                    next(action for action in alternatives(changed)
                        if action.get('properties', {}).get('action', {}).get('const') == 'edit_proof')[
                            'additionalProperties'] = True
                else:
                    next(tool for tool in changed if tool['name'] == 'read_theory')['description'] += ' changed'
                self.assertFalse(approved_registry(changed))
                self.assertFalse(_approved_generic_registry(changed, self.current))

    def test_partial_migration_and_loosened_evidence_schema_are_rejected(self):
        without_new = deepcopy(self.current)
        alternatives(without_new).remove(evidence_schema())
        self.assertFalse(approved_registry(without_new))
        loose = deepcopy(self.current)
        evidence = next(action for action in alternatives(loose) if action == evidence_schema())
        evidence['oneOf'][0]['additionalProperties'] = True
        self.assertFalse(approved_registry(loose))
        self.assertEqual(_pre_retrieval_registry(loose), loose)

    def test_mapping_is_idempotent_and_does_not_mutate_input(self):
        before = deepcopy(self.current)
        restored = _pre_retrieval_registry(self.current)
        self.assertEqual(self.current, before)
        self.assertEqual(_pre_retrieval_registry(restored), restored)
        restored[0]['description'] = 'test mutation'
        self.assertEqual(self.current, before)


if __name__ == '__main__':
    unittest.main()
