from copy import deepcopy
import json
import unittest
from regen.tests.current import test_sketch_route_choice as route_fixtures
from regen.planning.sketch_workflow import diagnose_failure, workflow_view, _pending, _failure
from regen.planning.sketch_check import authorize, load_groups
from regen.planning.theory_document import DocumentStore, parse, header, block


class WithdrawalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=route_fixtures.RouteChoiceTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store
        self.entries,self.proofs=parse(self.store.read()['source'],self.store.package,self.store.imports)
        self.groups=deepcopy(load_groups(self.store))

    def withdraw(self):
        failure=self.fixture.fixture.base.fail()
        diagnose_failure(self.store,failure,'known_fact','Abandon this optional route; preserve its evidence.')
        self.fixture.withdraw()
        return failure

    def recreate(self, *, same_id=False):
        entries=deepcopy(self.entries);proofs=deepcopy(self.proofs);groups=deepcopy(self.groups)
        old=next(n for n in entries if n['id']=='helper');oldname=old['name']
        new_id='helper' if same_id else 'fresh-helper'
        new_name=oldname if same_id else 'fresh_helper'
        old.update(id=new_id,name=new_name,statement='lemma '+new_name+': "True"')
        source=header(self.store.package['theory'],self.store.imports)+''.join(
            block(n,proofs.get('helper' if n['id']==new_id else n['id'],'oops').replace(oldname,new_name))
            for n in entries)+'end\n'
        for g in groups:
            g['helper_ids']=[new_id if k=='helper' else k for k in g['helper_ids']]
            for u in g.get('use_sites',[]):
                if u['helper_id']=='helper':u.update(helper_id=new_id,fact=u['fact'].replace(oldname,new_name))
        return self.store.edit({'action':'edit_theory','base_sha256':self.store.read()['source_sha256'],
            'source':source,'sketches':groups},{})

    def test_new_route_after_legal_withdrawal_is_not_locked_by_retired_helper(self):
        failure=self.withdraw();state=deepcopy(self.store.state)
        self.assertEqual(self.recreate()['status'],'draft_saved')
        self.assertEqual(self.store.state,state)
        self.assertNotIn('helper',_pending(self.store))
        history=workflow_view(self.store)['pending_failures']
        self.assertEqual(history[0]['failure_id'],failure)
        self.assertTrue(history[0]['route_withdrawn'])
        self.assertFalse(history[0]['active_in_draft'])

    def test_same_helper_id_new_declaration_still_needs_new_certificate_and_review(self):
        self.withdraw();self.recreate(same_id=True)
        with self.assertRaisesRegex(ValueError,'successful current check_sketch'):
            authorize(self.store,self.store.read()['source'],['helper'])
        self.assertNotIn('helper',_pending(self.store))

    def test_same_id_new_failure_keeps_prior_withdrawal_receipt_navigable(self):
        old=self.withdraw();self.recreate(same_id=True)
        entries,proofs=parse(self.store.read()['source'],self.store.package,self.store.imports)
        new=_failure(self.store,{n['id']:n for n in entries},'helper','by fail',
                     {'verdict':'red','diagnostic':'new failure'},'formal:new',proofs)
        self.assertNotEqual(old,new)
        view=workflow_view(self.store)
        self.assertEqual(view['pending_failures'][0]['failure_id'],new)
        self.assertEqual(view['withdrawn_failures'][0]['failure_id'],old)
        self.assertTrue(view['withdrawn_failures'][0]['route_withdrawn'])
        from regen.planning.sketch_assessment import read_failure
        self.assertEqual(read_failure(self.store,old)['failure_id'],old)

    def test_undiagnosed_withdrawal_still_rejected_atomically(self):
        self.fixture.fixture.base.fail();before=self.store.path.read_text()
        with self.assertRaisesRegex(ValueError,'diagnose failure'):self.fixture.withdraw()
        self.assertEqual(before,self.store.path.read_text())

    def test_active_route_still_requires_local_repair(self):
        failure=self.fixture.fixture.base.fail()
        diagnose_failure(self.store,failure,'known_fact','Repair the current helper locally.')
        with self.assertRaisesRegex(ValueError,'attempt the local proof repair'):self.recreate(same_id=True)

    def test_retirement_survives_another_saved_edit(self):
        failure=self.withdraw();current=self.store.read()
        self.store.edit({'action':'edit_theory','base_sha256':current['source_sha256'],
            'source':current['source'].replace('by simp','by auto')},{})
        self.assertEqual(self.recreate()['status'],'draft_saved')
        self.assertEqual(workflow_view(self.store)['pending_failures'][0]['failure_id'],failure)

    def test_corrupt_withdrawal_binding_is_not_silently_ignored(self):
        self.withdraw()
        pointer=json.loads((self.store.root/'last-edit.json').read_text())
        path=self.store.root/'workflow/withdrawals'/(pointer['sketch_withdrawal_snapshot']+'.json')
        data=json.loads(path.read_text());data['source_sha256']='bad';path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'binding differs'):_pending(self.store)

    def test_shared_failure_keeps_other_root_active_but_releases_withdrawn_root(self):
        package=deepcopy(self.store.package);state=deepcopy(self.store.state)
        extra={'contract_id':'contract2','name':'target2','exact_statement':'lemma target2: "True"',
               'contract_owned_context':[]}
        package['contracts'].append(extra)
        node={'id':'contract2','name':'target2','role':'contract','statement':extra['exact_statement'],'context':[]}
        state['graph']['nodes'].append(node);state['graph']['execution_order'].append('contract2')
        store=DocumentStore(self.store.root/'shared',package,state,self.store.imports,
                            generation_strategy='sketch-reviewed-v2')
        entries=deepcopy(self.entries)+[node];proofs={**self.proofs,'helper':'by fail','contract2':'using helper by simp'}
        groups=deepcopy(self.groups)+[{'target_entry_id':'contract2','helper_ids':['helper'],
            'use_sites':[{'helper_id':'helper','consumer_line':1,'fact':'helper'}]}]
        def edit(current_entries,current_proofs,updates):
            source=header(package['theory'],store.imports)+''.join(
                block(n,current_proofs.get(n['id'])) for n in current_entries)+'end\n'
            return store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                               'source':source,'sketches':updates},{})
        edit(entries,proofs,groups)
        failure=_failure(store,{n['id']:n for n in entries},'helper',proofs['helper'],
                         {'verdict':'red','diagnostic':'failed'},'formal:shared',proofs)
        diagnose_failure(store,failure,'known_fact','Keep the second route, withdraw the first.')
        proofs['contract']='by simp'
        edit(entries,proofs,[{'target_entry_id':'contract','helper_ids':[]}])
        self.assertEqual(_pending(store)['helper'],failure)
        fresh={'id':'fresh-helper','name':'fresh_helper','role':'helper',
               'statement':'lemma fresh_helper: "True"','context':[]}
        entries.insert(1,fresh);proofs['fresh-helper']='by simp';proofs['contract']='using fresh_helper by simp'
        result=edit(entries,proofs,[{'target_entry_id':'contract','helper_ids':['fresh-helper'],
            'use_sites':[{'helper_id':'fresh-helper','consumer_line':1,'fact':'fresh_helper'}]}])
        self.assertEqual(result['status'],'draft_saved')
        row=workflow_view(store)['pending_failures'][0]
        self.assertFalse(row['route_withdrawn'])
        self.assertEqual(row['withdrawn_contract_ids'],['contract'])
        entries[0]['statement']='lemma helper: "True"'
        with self.assertRaisesRegex(ValueError,'attempt the local proof repair'):
            edit(entries,proofs,[])


if __name__=='__main__':unittest.main()
