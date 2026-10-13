import unittest
from regen.tools.provider_bootstrap_validation import conditions, candidate_header, proof_failure_category


class ProviderValidationTests(unittest.TestCase):
    def report(self):
        row = dict(resource_context_present=True, build={'verdict':'green'},
                   replay={'verdict':'red','returncode':1}, inputs_unchanged=True,
                   same_proof_failure=True)
        return {'peak_native_active':1,'theories':{'a':dict(row),'b':dict(row)}}

    def test_expected_red_is_success(self):
        self.assertTrue(conditions(self.report()))

    def test_timeout_oom_and_parallel_are_not_success(self):
        for code in (124,137):
            report=self.report();report['theories']['a']['replay']={'verdict':'red','returncode':code}
            self.assertFalse(conditions(report))
        report=self.report();report['peak_native_active']=2
        self.assertFalse(conditions(report))

    def test_missing_context_or_input_drift_fails(self):
        for field in ('resource_context_present','inputs_unchanged','same_proof_failure'):
            report=self.report();report['theories']['a'][field]=False
            self.assertFalse(conditions(report))

    def test_candidate_header_and_proof_failure(self):
        candidate_header('theory A\nimports X\nbegin\nend','A')
        with self.assertRaises(ValueError):
            candidate_header('theory B imports X begin end','A')
        self.assertIsNone(proof_failure_category('Bad theory import'))
        self.assertEqual(proof_failure_category('RED Failed to finish proof'), 'Failed to finish proof')


if __name__=='__main__':unittest.main()
