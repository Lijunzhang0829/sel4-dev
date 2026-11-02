"""Synthetic receipt validation tests; these do not execute Isabelle."""
from copy import deepcopy
import hashlib
import unittest

from regen.execution.import_semantics import (
    MARKER, POLICY, observation_source, records_from_log,
    check_import_semantics, validate_import_semantics,
)
from regen.tests.current.test_proof_commits import fixture


BASE = ['Semantic_Base.Semantic_Root']
PROVIDER = ['Generated_Refine.Regen_Provider']


def bound(name, text):
    return {name + '_content': text,
            name + '_sha256': hashlib.sha256(text.encode()).hexdigest()}


def synthetic_observation(package, imports, identities=None):
    identities = identities or {c['contract_id']: 'a' * 40 for c in package['contracts']}
    log = ''.join(MARKER + key.encode().hex() + ' ' + value + '\n'
                  for key, value in identities.items())
    return {'imports': list(imports), 'records': dict(identities), 'check': {
        'verdict': 'green', 'returncode': 0,
        **bound('source', observation_source(package, imports)), **bound('log', log)}}


def synthetic_semantics(package, before=BASE, after=PROVIDER):
    return {'policy': POLICY, 'valid': True,
            'before': synthetic_observation(package, before),
            'after': synthetic_observation(package, after)}


class SyntheticObserver:
    def __init__(self, imports, package, *, identities=None, verdict='green'):
        self.names, self.package = imports, package
        self.identities, self.verdict, self.calls = identities, verdict, 0

    def imports(self):
        return self.names

    def _check(self, source, label):
        self.calls += 1
        result = synthetic_observation(self.package, self.names, self.identities)['check']
        result.update(verdict=self.verdict, returncode=0 if self.verdict == 'green' else 1)
        if source != result['source_content']:
            raise AssertionError('observer received a different fixed target source')
        return result


class ImportSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.package, _ = fixture()

    def test_matching_typed_records_are_bound_to_both_exact_contexts(self):
        evidence = synthetic_semantics(self.package)
        records = validate_import_semantics(evidence, self.package, BASE, PROVIDER)
        self.assertEqual(records, {'contract': 'a' * 40})
        records.clear()
        self.assertEqual(evidence['before']['records'], {'contract': 'a' * 40})

    def test_source_log_and_records_tampering_are_rejected(self):
        def changed_source(row):
            row['check'].update(bound('source', row['check']['source_content'] + '\n'))
        def changed_record(row):
            row['records']['contract'] = 'b' * 40
        def changed_log(row):
            row['check']['log_content'] += 'changed without updating digest\n'
        for change in (changed_source, changed_record, changed_log,
                       lambda r: r.update(imports=BASE),
                       lambda r: r['check'].update(returncode=False)):
            evidence = synthetic_semantics(self.package)
            change(evidence['after'])
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_import_semantics(evidence, self.package, BASE, PROVIDER)

    def test_rehashed_target_meaning_change_is_rejected(self):
        evidence = synthetic_semantics(self.package)
        evidence['after'] = synthetic_observation(self.package, PROVIDER, {'contract': 'b' * 40})
        with self.assertRaisesRegex(ValueError, 'changes fixed target meanings'):
            validate_import_semantics(evidence, self.package, BASE, PROVIDER)

    def test_observation_requires_exact_once_contract_coverage(self):
        valid = synthetic_observation(self.package, BASE)['check']['log_content']
        for log in ('', valid + valid, valid + MARKER + b'other'.hex() + ' ' + 'b' * 40 + '\n'):
            with self.subTest(log=log), self.assertRaises(ValueError):
                records_from_log(log, self.package)

    def test_check_reports_meaning_difference_and_native_failure_separately(self):
        before = SyntheticObserver(BASE, self.package)
        after = SyntheticObserver(PROVIDER, self.package, identities={'contract': 'b' * 40})
        result = check_import_semantics(before, after, self.package)
        self.assertFalse(result['valid'])
        self.assertEqual(result['stage'], 'changed-target-meaning')
        self.assertEqual(result['changed_contract_ids'], ['contract'])
        failed = SyntheticObserver(PROVIDER, self.package, verdict='red')
        result = check_import_semantics(before, failed, self.package)
        self.assertEqual(result['stage'], 'candidate-context')
        before_failure = SyntheticObserver(BASE, self.package, verdict='red')
        untouched = SyntheticObserver(PROVIDER, self.package)
        result = check_import_semantics(before_failure, untouched, self.package)
        self.assertEqual(result['stage'], 'previous-context')
        self.assertEqual(untouched.calls, 0)

    def test_false_valid_flag_and_changed_frozen_statement_do_not_validate(self):
        evidence = synthetic_semantics(self.package)
        evidence['valid'] = False
        with self.assertRaises(ValueError):
            validate_import_semantics(evidence, self.package, BASE, PROVIDER)
        changed = deepcopy(self.package)
        changed['contracts'][0]['exact_statement'] = 'lemma target: "False"'
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            validate_import_semantics(synthetic_semantics(self.package), changed, BASE, PROVIDER)


if __name__ == '__main__':
    unittest.main()
