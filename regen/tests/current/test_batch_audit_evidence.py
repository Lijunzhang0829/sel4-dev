"""Native evidence remains exact even when diagnostic stdout is delayed."""
from copy import deepcopy
import hashlib
import unittest
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from regen.execution.batch_audit import PROTOCOL
from regen.execution.batch_checker import BatchWorker, body
from regen.execution.proof_batches import check_batch, prepare_batch, validate_batch_commit
from regen.tests.current.test_incremental_batches import MemoryWorker
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture


class AuditWorker(MemoryWorker):
    def check_batch(self, source, names):
        result = super().check_batch(source, names)
        root = ET.Element('batch_audit', version='2', worker='test-worker', request='1',
                          theory=source.splitlines()[0].split()[1])
        ET.SubElement(root, 'prefix').text = self.base
        ET.SubElement(root, 'delta').text = body(source)[len(self.base):]
        for group, tag, values in [('imports', 'import', self.imports), ('facts', 'fact', names)]:
            parent = ET.SubElement(root, group)
            for value in values:
                ET.SubElement(parent, tag).text = value
        content = ET.tostring(root, encoding='unicode')
        result.update(incremental=PROTOCOL, worker='test-worker', request_id=1,
            audit_content=content, audit_sha256=hashlib.sha256(content.encode()).hexdigest(),
            log_content='', log_sha256=hashlib.sha256(b'').hexdigest())
        return result


class BatchAuditEvidenceTests(unittest.TestCase):
    def evidence(self):
        package, old = fixture()
        new = prepare_batch(package, old, {'proofs':{'helper':'by simp'}})
        checker = Checker(); checker.batch_incremental = True
        with patch('regen.execution.batch_checker.BatchWorker', AuditWorker):
            evidence = check_batch(checker, old, new)
        return package, old, new, evidence, checker

    def test_empty_stdout_does_not_reject_native_audit(self):
        _, old, new, evidence, _ = self.evidence()
        self.assertEqual(evidence['check']['log_content'], '')
        validate_batch_commit(old, new, evidence)

    def test_native_prefix_after_promotion_is_bound(self):
        package, _, old, _, checker = self.evidence()
        new = prepare_batch(package, old, {'proofs':{'contract':'using helper by simp'}})
        with patch('regen.execution.batch_checker.BatchWorker', AuditWorker):
            evidence = check_batch(checker, old, new)
        validate_batch_commit(old, new, evidence)
        self.assertIn('by simp', ET.fromstring(evidence['check']['audit_content']).findtext('prefix'))

    def test_structural_binding_changes_rejected_even_with_fresh_content_hash(self):
        _, old, new, evidence, _ = self.evidence()
        mutations = {
            'worker': lambda root: root.set('worker', 'different-worker'),
            'request': lambda root: root.set('request', '2'),
            'theory': lambda root: root.set('theory', 'Wrong'),
            'version': lambda root: root.set('version', '1'),
            'prefix': lambda root: setattr(root.find('prefix'), 'text', 'other prefix'),
            'delta': lambda root: setattr(root.find('delta'), 'text', 'different proof'),
            'imports': lambda root: setattr(root.find('imports/import'), 'text', 'Other.Theory'),
            'facts': lambda root: setattr(root.find('facts/fact'), 'text', 'different_fact'),
            'missing_fact': lambda root: root.find('facts').clear(),
            'extra_fact': lambda root: ET.SubElement(root.find('facts'), 'fact'),
            'extra_field': lambda root: ET.SubElement(root, 'unrecognized'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                bad = deepcopy(evidence)
                root = ET.fromstring(bad['check']['audit_content']); mutate(root)
                content = ET.tostring(root, encoding='unicode')
                bad['check'].update(audit_content=content, audit_sha256=hashlib.sha256(content.encode()).hexdigest())
                with self.assertRaises(ValueError):
                    validate_batch_commit(old, new, bad)

    def test_missing_corrupt_or_unrecognized_evidence_cannot_be_green(self):
        _, old, new, evidence, _ = self.evidence()
        for key, value in [('audit_content', None), ('audit_content','<broken'),
                           ('audit_sha256','wrong'), ('request_id',True),
                           ('incremental','unrecognized-worker-v3')]:
            with self.subTest(key=key, value=value):
                bad = deepcopy(evidence); bad['check'][key] = value
                if key == 'audit_content' and isinstance(value, str):
                    bad['check']['audit_sha256'] = hashlib.sha256(value.encode()).hexdigest()
                with self.assertRaises(ValueError):
                    validate_batch_commit(old, new, bad)

    def test_red_cannot_be_promoted_by_an_audit_record(self):
        _, old, new, evidence, _ = self.evidence()
        evidence['check'].update(verdict='red', returncode=1)
        with self.assertRaises(ValueError):
            validate_batch_commit(old, new, evidence)


class BatchAuditTransportTests(unittest.TestCase):
    def test_missing_or_malformed_native_audit_uses_cold_fallback_not_bare_green(self):
        for content in (None, '<broken', '<batch_audit />'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if content is not None:
                    (root/'1.audit.xml').write_text(content)
                package, old = fixture()
                new = prepare_batch(package, old, {'proofs':{'helper':'by simp'}})
                checker = Checker(); checker.batch_incremental = True
                checker.work_dir = root
                instances = []
                def factory(checker, source, names, imports):
                    worker = object.__new__(BatchWorker)
                    worker.checker = checker; worker.root = root; worker.index = 1
                    worker.base = body(source); worker.pending = None
                    worker.label = source.splitlines()[0].split()[1]
                    worker.imports = imports; worker.name = 'test-worker'; worker.closed = False
                    worker.process = Mock(); worker.process.poll.return_value = None
                    worker.transaction = Mock(return_value=('GREEN', 'diagnostic stdout'))
                    worker.close = Mock(side_effect=lambda: setattr(worker, 'closed', True))
                    instances.append(worker)
                    return worker
                with patch('regen.execution.batch_checker.BatchWorker', side_effect=factory):
                    evidence = check_batch(checker, old, new)
                self.assertIn('incremental audit transport failure', evidence['incremental_fallback'])
                self.assertEqual(checker.calls, 1)
                self.assertFalse(evidence['check']['incremental'])
                self.assertTrue(instances[0].closed)
                self.assertIsNone(instances[0].pending)
                validate_batch_commit(old, new, evidence)
