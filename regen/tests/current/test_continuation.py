from copy import deepcopy
import unittest
from regen.tests.current.test_proof_commits import fixture, check
from regen.execution.proof_commits import digest
from regen.execution.repository_execution import continuation_state, validate_continuation, CONTINUATION_ENV_FIELDS

class ContinuationTest(unittest.TestCase):
    def origin(self):
        p,s=fixture();p['package_payload_sha256']='package'
        run={**{k:k for k in CONTINUATION_ENV_FIELDS},'session_schedule':{},'run_id':'old','implementation':{}}
        binding={'run_id':'old','implementation':digest({}),'package_sha256':'package','imports':[]}
        body={'sequence':1,'parent':None,'binding':binding,'event':'initial','state':s,'evidence':{'check':check()}}
        first={**body,'commit_id':digest(body)}
        charged=deepcopy(s);charged['rounds']['contract']=5
        body={'sequence':2,'parent':first['commit_id'],'binding':binding,'event':'attempt','state':charged,'evidence':{'node_id':'contract'}}
        return p,{'run':run,'imports':[],'commits':[first,{**body,'commit_id':digest(body)}]}

    def test_preserves_costs_and_recovers_attempt_context(self):
        p,o=self.origin();s=continuation_state(p,o)
        self.assertEqual(s['rounds'],{'contract':5});self.assertEqual(s['attempt_contexts'],{'contract':[]})
        validate_continuation(p,o['run'],s,o)
        bad=deepcopy(s);bad['rounds']={}
        with self.assertRaisesRegex(ValueError,'cumulative'):validate_continuation(p,o['run'],bad,o)

    def test_rejects_changed_environment_and_corrupt_source(self):
        p,o=self.origin();s=continuation_state(p,o);runtime={**o['run'],'image':'changed'}
        with self.assertRaisesRegex(ValueError,'environment'):validate_continuation(p,runtime,s,o)
        o['commits'][1]['state']['rounds']={}
        with self.assertRaisesRegex(ValueError,'source chain'):continuation_state(p,o)
