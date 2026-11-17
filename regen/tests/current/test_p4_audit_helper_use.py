import hashlib
import unittest

from regen.tools.audit_helper_use import dependency_names, reachable


class AuditHelperUseUnitTests(unittest.TestCase):
    def test_reachable_follows_edges_once(self):
        edges = {'a': ['b'], 'b': ['c'], 'c': []}
        self.assertEqual(reachable('a', edges), {'b', 'c'})

    def test_dependency_names_parses_and_verifies_receipt(self):
        raw = 'THM_DEP\t' + '6d79206c656d6d61'.encode().decode() + '\n'
        receipt = {'raw_content': raw, 'raw_sha256': hashlib.sha256(raw.encode()).hexdigest()}
        self.assertEqual(dependency_names(receipt), {'my lemma'})


if __name__ == '__main__':
    unittest.main()
