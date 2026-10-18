import unittest
from regen.tools.reviewed_planning import decisions_valid,replace_ids

class ReviewedPlanningTests(unittest.TestCase):
    def test_complete_decisions(self):
        review={'issues':[{'id':'I1'}]}
        good={'issue_id':'I1','decision':'local','reason':'local proof suffices','tradeoff':'no boundary change','remaining_risk':'proof pending'}
        decisions_valid([good],review)
        for bad in [[],[good,good],[{**good,'issue_id':'I2'}],[{**good,'reason':''}],[{**good,'decision':'proved'}]]:
            with self.assertRaises(ValueError):decisions_valid(bad,review)

    def test_public_ids_include_prose(self):
        x={'ids':['contract:long:id'],'reason':'Use contract:long:id here'}
        self.assertEqual(replace_ids(x,{'contract:long:id':'C0001'}),{'ids':['C0001'],'reason':'Use C0001 here'})

if __name__=='__main__':unittest.main()
