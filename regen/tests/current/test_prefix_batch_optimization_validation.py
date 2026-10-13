import unittest
from copy import deepcopy

from regen.tools.prefix_batch_optimization_validation import load_fixture,one_declaration
from regen.planning.theory_document import baseline


class FrozenPrefixReplayTests(unittest.TestCase):
    def test_saved_context_and_incremental_prefix(self):
        old,package,rows,imports,manifest=load_fixture()
        self.assertEqual(manifest['requests'],[45,46])
        original=deepcopy(old)
        current=old
        for key in rows[0]['state']['accepted_order'][len(old['accepted_order']):]:
            new,source=one_declaration(package,current,rows[0]['state'],key,imports)
            self.assertEqual(baseline(current,new)[1],'append')
            self.assertEqual(len(new['proofs']),len(current['proofs'])+1)
            self.assertEqual(new['proofs'][key],rows[0]['state']['proofs'][key])
            current=new
        self.assertEqual(old,original)

    def test_cannot_overwrite_accepted_proof(self):
        old,package,rows,imports,_=load_fixture()
        saved=deepcopy(rows[0]['state'])
        saved['proofs'][old['accepted_order'][0]]='by simp'
        with self.assertRaisesRegex(ValueError,'changes already accepted'):
            one_declaration(package,old,saved,saved['accepted_order'][len(old['accepted_order'])],imports)


if __name__=='__main__': unittest.main()
