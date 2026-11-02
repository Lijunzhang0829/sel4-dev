"""Public evidence reads and cold recovery with real controller/task journals.

The proof checker and catalog selection are synthetic fixtures. No Isabelle or
model process runs; query execution, receipt storage, readers and restoration use
the production paths. Complete statements must survive those boundaries.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import digest, durable
from regen.planning.parallel_file_planner import TheoryController
from regen.tests.current import test_proof_workspace_agent as fixtures


class RetrievalRecoveryControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProofWorkspaceAgentTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.stack.enter_context(patch('regen.planning.parallel_file_planner.emit_event'))
        self.fixture.fresh_target(nodes=[])
        self.root = self.fixture.root
        package = self.fixture.packages[1]
        package['contracts'].extend([
            {'contract_id': 'other_contract', 'name': 'other_contract',
             'exact_statement': 'lemma other_contract: "True"', 'contract_owned_context': []},
            {'contract_id': 'accepted_contract', 'name': 'accepted_contract',
             'exact_statement': 'lemma accepted_contract: "True"', 'contract_owned_context': []},
        ])
        package['package_payload_sha256'] = digest({k: v for k, v in package.items()
                                                   if k != 'package_payload_sha256'})
        durable(self.root / 'packages/Target.thy.json', package)
        inputs = json.loads((self.root / 'execution-input.json').read_text())
        inputs['package_hashes']['Target.thy'] = package['package_payload_sha256']
        durable(self.root / 'execution-input.json', inputs)
        self.resources = NativeResources(1, 1, 1)
        self.addCleanup(self.resources.close)
        self.task_directory = self.root / 'retrieval-test-tasks'
        self.executor = CheckExecutor(self.task_directory, workers=1)
        self.addCleanup(self.executor.close)
        self.controller = self.new_controller(self.executor)
        self.checkers, self.setup_calls = self.fixture.local_runtime(self.controller)
        # Let the real observation worker create its own read-only checker, but
        # give the fixture's synthetic proof checker the required path metadata.
        from regen.execution import repository_execution
        setup = repository_execution.setup.side_effect

        def configured_setup(root, theory, timeout):
            runtime = setup(root, theory, timeout)
            checker = runtime[4]
            metadata = IsabelleChecker(root, runtime[2] / 'synthetic-checker',
                generated_dir=runtime[2] / 'generated', heaps_dir=root / 'heaps',
                l4v_dir=root, semantic_workspace=root, isabelle_dir=root / 'isabelle')
            checker.__dict__.update(metadata.__dict__)
            checker.batch_incremental = False
            return runtime

        repository_execution.setup.side_effect = configured_setup
        self.catalog = {}
        self.recall = self.fixture.stack.enter_context(patch(
            'regen.planning.batch_file_planner.batch_query', side_effect=self.select))
        self.serial = 0
        self.read_theory()
        saved = self.action({'action': 'edit_proof', 'entry_id': 'accepted_contract',
            'expected_hash': self.read_theory()['document']['source_sha256'], 'proof_body': 'by simp'})
        checked = self.action({'action': 'check_theory', 'source_sha256': saved['source_sha256'],
                               'entry_ids': ['accepted_contract']})
        self.assertEqual(checked.get('feedback', {}).get('status'), 'accepted', checked)

    def new_controller(self, executor):
        controller = TheoryController(self.root, 'Target', resources=self.resources,
                                      executor=executor, document_mode=True)
        self.addCleanup(controller.close)
        return controller

    def action(self, proposal):
        self.serial += 1
        return self.controller.handle('proof_action', {'theory': 'Target', 'proposal': proposal},
            {'thread_id': 'synthetic', 'turn_id': 'synthetic', 'call_id': str(self.serial),
             'transcript_path': 'synthetic-test-transcript', 'transcript_offset': 1})

    def read_theory(self):
        return self.controller.handle('read_theory', {'theory': 'Target'}, {})

    def entry(self, entry_id='target_contract'):
        return self.action({'action': 'read_entry', 'entry_id': entry_id})

    def select(self, run, package, state, checker, directory, request, *, audit_path=None):
        token = request['premise_queries'][0]['query']
        records = deepcopy(self.catalog[token])
        page = {'status': 'available', 'kind': 'fact_selection', 'records': records,
            'queries': [{'query': token, 'mode': 'exact', 'status': 'matched',
                         'selected_record_ids': [row['record_id'] for row in records]}]}
        if audit_path is not None:
            durable(audit_path, {'request': request, 'result': page}, immutable=True)
        return page

    def query(self, token, *, entry_id='target_contract', count=1):
        # Longer than the legacy prompt excerpt budgets; compare every byte.
        proposition = ' & '.join(['(x = x)'] * 900)
        self.catalog[token] = [{
            'record_id': f'premise:{token}:{index}', 'canonical_name': f'Base.{token}_{index}',
            'exact_statement': f'lemma {token}_{index}: "{proposition}"',
            'context': ['context Synthetic_Locale begin'], 'attributes': ['simp'],
            'source_class': 'frozen_parent', 'owner_theory': 'Base.Synthetic',
        } for index in range(count)]
        request = {'premise_queries': [{'query': token, 'mode': 'exact', 'scope': 'upstream'}]}
        if entry_id is not None:
            request['context_entry_id'] = entry_id
        result = self.action({'action': 'query', 'request': request})
        selection = result['observations'][0]['result']
        self.assertEqual(selection['records'], self.catalog[token])
        return selection['query_identity'], deepcopy(self.catalog[token])

    def authority(self):
        runtime = self.controller._runtime()
        state = runtime[3].replay(repair=False)
        commits = {p.name: p.read_bytes() for p in
                   (self.root / 'theories/Target/acceptance/commits').glob('*.json')}
        return deepcopy(state), commits, self.checkers['Target'].calls

    def assert_authority_unchanged(self, before):
        self.assertEqual(self.authority(), before)
        self.fixture.llm.assert_not_called()

    @staticmethod
    def result_groups(result):
        return {row['request']['action']: row['result'] for row in result['observations']}

    def test_mixed_evidence_preserves_full_fact_query_and_local_green_without_checking(self):
        query_id, facts = self.query('mixed')
        before = self.authority()
        result = self.action({'action': 'read_evidence', 'refs': [
            {'kind': 'fact', 'id': facts[0]['record_id']},
            {'kind': 'query', 'id': query_id},
            {'kind': 'proof', 'id': 'accepted_contract'},
        ]})
        groups = self.result_groups(result)
        self.assertEqual(groups['read_facts']['records'], facts)
        self.assertEqual(groups['read_queries']['queries'][0]['query_id'], query_id)
        self.assertEqual(groups['read_queries']['queries'][0]['returned_record_ids'],
                         [facts[0]['record_id']])
        proof = groups['read_proofs']['proofs'][0]
        self.assertEqual(proof['id'], 'accepted_contract')
        self.assertEqual(proof['proof_body'], 'by simp')
        self.assertEqual(proof['statement'], 'lemma accepted_contract: "True"')
        self.assert_authority_unchanged(before)

    def test_cold_controller_and_executor_restore_target_full_facts_from_receipts(self):
        query_id, facts = self.query('restarted', count=2)
        original = self.entry()['retrieval_memory']
        self.assertEqual({q['query_id'] for q in original['queries']}, {query_id})
        self.controller.close()
        self.executor.close()
        self.executor = CheckExecutor(self.task_directory, workers=1)
        self.addCleanup(self.executor.close)
        self.controller = self.new_controller(self.executor)
        self.read_theory()
        before = self.authority()
        # Recovery must not dispatch the catalog again or request a native check.
        with patch('regen.planning.batch_file_planner.batch_query',
                   side_effect=AssertionError('cold evidence read repeated retrieval')):
            recovered = self.entry()['retrieval_memory']
            self.assertEqual(recovered, original)
            by_id = {row['record_id']: row for row in recovered['facts']}
            for fact in facts:
                self.assertEqual(by_id[fact['record_id']]['exact_statement'], fact['exact_statement'])
                self.assertFalse(by_id[fact['record_id']]['is_proof_authority'])
            read = self.action({'action': 'read_evidence', 'refs': [
                {'kind': 'fact', 'id': facts[0]['record_id']}, {'kind': 'query', 'id': query_id}]})
            self.assertEqual(self.result_groups(read)['read_facts']['records'], [facts[0]])
        self.assert_authority_unchanged(before)

    def test_other_entry_and_unscoped_queries_do_not_pollute_target_memory(self):
        target_query, target_facts = self.query('target', count=2)
        other_query, other_facts = self.query('other', entry_id='other_contract')
        global_query, global_facts = self.query('global', entry_id=None)
        before = self.authority()
        target = self.entry()['retrieval_memory']
        other = self.entry('other_contract')['retrieval_memory']
        untouched = self.entry('accepted_contract')['retrieval_memory']
        self.assertEqual({row['record_id'] for row in target['facts']},
                         {row['record_id'] for row in target_facts})
        self.assertEqual({row['query_id'] for row in target['queries']}, {target_query})
        self.assertEqual({row['record_id'] for row in other['facts']},
                         {row['record_id'] for row in other_facts})
        self.assertEqual({row['query_id'] for row in other['queries']}, {other_query})
        self.assertEqual(untouched['facts'], [])
        self.assertEqual(untouched['queries'], [])
        self.assertEqual(target['global_queries']['total'], 1)
        self.assertNotIn(global_query, {row['query_id'] for row in target['queries']})
        self.assertNotIn(global_facts[0]['record_id'], {row['record_id'] for row in target['facts']})
        self.assert_authority_unchanged(before)

    def test_target_index_paginates_and_snapshot_ignores_unrelated_query_growth(self):
        _, facts = self.query('many', count=10)
        memory = self.entry()['retrieval_memory']
        self.assertEqual(len(memory['facts']), 8)
        self.assertEqual(memory['totals']['facts'], 10)
        continuation = memory['continuation']['facts']
        self.assertEqual(continuation['action'], 'read_evidence')
        self.assertEqual(continuation['entry_id'], 'target_contract')
        first = self.action({'action': 'read_evidence', 'index': 'facts',
                             'entry_id': 'target_contract', 'limit': 3})
        self.assertEqual(first['total'], 10)
        self.assertEqual(first['next_action']['action'], 'read_evidence')
        self.assertEqual(first['next_action']['entry_id'], 'target_contract')
        self.query('unrelated', entry_id='other_contract')
        before = self.authority()
        found = list(first['records'])
        page = first
        tasks_before = len(self.executor.records)
        while page['next_action']:
            page = self.action(page['next_action'])
            self.assertEqual(page['snapshot_sha256'], first['snapshot_sha256'])
            found.extend(page['records'])
        self.assertEqual({row['record_id'] for row in found}, {row['record_id'] for row in facts})
        self.assertEqual(len(found), 10)
        self.assertEqual(len(self.executor.records), tasks_before)
        self.assert_authority_unchanged(before)
        self.query('new_target_result')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            self.action(first['next_action'])

    def test_legacy_read_facts_returns_identical_complete_records(self):
        _, facts = self.query('legacy', count=2)
        before = self.authority()
        legacy = self.action({'action': 'read_facts', 'record_ids': [f['record_id'] for f in facts]})
        modern = self.action({'action': 'read_evidence',
                             'refs': [{'kind': 'fact', 'id': f['record_id']} for f in facts]})
        self.assertEqual(self.result_groups(legacy)['read_facts'],
                         self.result_groups(modern)['read_facts'])
        self.assertEqual(self.result_groups(modern)['read_facts']['records'], facts)
        self.assert_authority_unchanged(before)


if __name__ == '__main__':
    unittest.main()
