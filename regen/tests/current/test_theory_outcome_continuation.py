"""Full workspace continuations retain stops independently of positive proofs."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from regen.execution import repository_execution as execution
from regen.execution.proof_commits import digest, durable
from regen.execution.fact_context import manifest
from regen.planning.proof_graph import build_graph
from regen.planning.theory_outcomes import _publish, current_outcome, record_unresolved
from regen.tests.current.test_proof_commits import fixture, check


class SyntheticNativeChecker:
    """Native I/O fixture only; these tests do not claim a real Isabelle check."""
    base_session = 'HOL'
    base_import = 'Main'
    previous_theories = []

    def __init__(self, root, contract_id):
        self.root, self.contract_id = root, contract_id

    def imports(self):
        return ['Main']

    def _check(self, source, label, *, writable_mounts):
        path = self.root/(label+'.thy')
        path.write_text(source)
        log = path.with_suffix('.log')
        log.write_text('synthetic native transport fixture')
        xml = ('<contract_refutation version="1" contract_id="'+self.contract_id+'">'
            '<expected_term>not False</expected_term><proved_term>not False</proved_term>'
            '<skip_proof>false</skip_proof><oracles>0</oracles><hypotheses>0</hypotheses>'
            '<changed_axioms>false</changed_axioms><exact_closed_negation>true</exact_closed_negation>'
            '<dependencies/></contract_refutation>')
        (writable_mounts[0][0]/'refutation.xml').write_text(xml)
        return {'returncode':0, 'verdict':'green', 'source':str(path), 'log':str(log)}


class TheoryOutcomeContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root/'source'; self.source.mkdir()
        self.run = {'run_id':'source', 'implementation':{}, 'session_schedule':{'source':'schedule'},
            'environment_run':str(self.root), 'base_import':'Main',
            'contract_package_dir':str(self.source/'packages')}
        self.packages = {}; self.files = []
        for name in ('Committed', 'Unstarted'):
            package, base = fixture()
            package.update(theory=name, file_slot=name+'.thy')
            package['contracts'][0].update(contract_id=name.lower(), exact_statement='lemma target: "False"')
            package['package_payload_sha256'] = digest(package)
            self.packages[name] = package
            self.files.append({'theory':name, 'file_slot':name+'.thy'})
            durable(self.source/'packages'/(name+'.thy.json'), package)
        durable(self.source/'run.json', self.run)
        durable(self.source/'execution-input.json', {'files':self.files, 'package_hashes':{
            p['file_slot']:p['package_payload_sha256'] for p in self.packages.values()}})
        p = self.packages['Committed']
        graph = execution.context_graph(build_graph(p, {'generated_nodes':[
            n for n in base['graph']['nodes'] if n['role']=='helper']}, run_id='source', revision=1), [])
        state = {**base, 'graph':graph, 'batch_requests':7, 'rounds':{'helper':2}}
        native = check()
        native['source_content'] = native['source_content'].replace('lemma target: "True"', 'lemma target: "False"')
        import hashlib
        native['source_sha256'] = hashlib.sha256(native['source_content'].encode()).hexdigest()
        durable(self.source/'theories/Committed/imports.json', [])
        journal = execution.journal(self.source, self.run, p)
        journal.append('initial', state, {'check':native})
        green = deepcopy(state)
        green.update(proofs={'helper':'by simp'}, accepted_order=['helper'])
        green['graph'] = execution.context_graph(graph, ['helper'])
        journal.append('green', green, {'node_id':'helper', 'check':native,
            'sealable_generation':True, 'visibility':manifest(graph, {}, 'helper')})

    def controller(self, root, run, theory):
        package = execution.package_for(run, theory+'.thy')
        state = execution.journal(root, run, package).replay(repair=False)
        if state is None:
            state = execution._unstarted_outcome_state(package, run, document_mode=True)
        return SimpleNamespace(root=root, run=run, theory=theory,
            _state=lambda _: (deepcopy(package), deepcopy(state)))

    def initialize(self, destination, *_args):
        destination.mkdir()
        shutil.copytree(self.source/'packages', destination/'packages')
        shutil.copy2(self.source/'execution-input.json', destination/'execution-input.json')
        run = {**self.run, 'run_id':destination.name, 'contract_package_dir':str(destination/'packages')}
        durable(destination/'run.json', run)
        return run

    def fork(self, *, initialize=None, checkpoint=None):
        destination = self.root/'continuation'
        with patch.object(execution, 'init_run', side_effect=initialize or self.initialize), \
                patch.object(execution, 'progress', return_value={}):
            run = execution.fork_workspace_continuation(self.source, destination, checkpoint=checkpoint)
        return destination, run

    def unknown(self, theory):
        return record_unresolved(self.controller(self.source, self.run, theory),
            reason='bounded test stop', origin='controller', classification='no_progress')

    def certificate(self, theory):
        from regen.execution.contract_refutation import check_refutation
        contract = self.packages[theory]['contracts'][0]
        return check_refutation(SyntheticNativeChecker(self.root, contract['contract_id']),
            contract, 'by simp', self.root/'negative-check')['certificate']

    def refuted(self, theory):
        controller = self.controller(self.source, self.run, theory)
        contract = self.packages[theory]['contracts'][0]
        certificate = self.certificate(theory)
        durable(self.source/'theories'/theory/'imports.json', [])
        return _publish(controller, status='refuted', reason='synthetic checked fixture',
            origin='native_checker', details={'certificate':certificate}, entry_ids=[contract['contract_id']])

    def assert_preserved(self, source_outcome, theory):
        before = execution.journal(self.source, self.run, self.packages['Committed']).replay()
        destination, run = self.fork()
        result = current_outcome(self.controller(destination, run, theory))
        self.assertEqual(result['status'], source_outcome['status'])
        self.assertNotEqual(result['binding']['run_id'], source_outcome['binding']['run_id'])
        self.assertEqual(result['metadata']['continuation']['source_receipt'], source_outcome['receipt'])
        self.assertEqual(result['positive_proofs_added'], 0)
        after = execution.journal(destination, run, self.packages['Committed']).replay()
        for field in ('graph', 'proofs', 'accepted_order', 'rounds', 'revisions', 'batch_requests'):
            self.assertEqual(after[field], before[field])
        receipt = execution.read(destination/'theory-outcomes-continuation.json')
        self.assertEqual(receipt['refund_seconds'], 0)
        self.assertFalse((destination/'effective-generation-budget').exists())
        if theory == 'Unstarted':
            self.assertFalse(list((destination/'theories'/theory/'acceptance/commits').glob('*.json')))
            self.assertEqual(self.controller(destination, run, theory)._state(theory)[1]['proofs'], {})
        return result

    def test_committed_unknown_survives_with_proofs_and_costs_unchanged(self):
        self.assert_preserved(self.unknown('Committed'), 'Committed')

    def test_unstarted_unknown_survives_without_fabricating_a_commit(self):
        self.assert_preserved(self.unknown('Unstarted'), 'Unstarted')

    def test_only_unstarted_stops_can_continue_without_positive_commits(self):
        shutil.rmtree(self.source/'theories/Committed/acceptance')
        self.unknown('Unstarted')
        destination, run = self.fork()
        self.assertEqual(current_outcome(self.controller(destination, run, 'Unstarted'))['status'], 'unknown')
        self.assertFalse(list((destination/'theories').glob('*/acceptance/commits/*.json')))
        receipt = execution.read(destination/'workspace-continuation.json')
        self.assertEqual(receipt['theories'], [])
        self.assertEqual(receipt['sealed_theories'], [])

    def test_committed_refutation_survives_with_revalidated_certificate(self):
        old = self.refuted('Committed')
        new = self.assert_preserved(old, 'Committed')
        self.assertEqual(new['details']['certificate'], old['details']['certificate'])

    def test_unstarted_refutation_survives_with_no_positive_proof(self):
        self.assert_preserved(self.refuted('Unstarted'), 'Unstarted')

    def test_tampered_stop_rejected_before_destination_is_created(self):
        outcome = self.unknown('Committed')
        path = Path(outcome['receipt']['directory'])/'events/000000001.json'
        row = json.loads(path.read_text()); row['data']['reason'] = 'tampered'
        path.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, 'chain mismatch'):
            self.fork()
        self.assertFalse((self.root/'continuation').exists())

    def test_source_changed_between_preflight_and_transfer_rejected(self):
        self.unknown('Committed')
        def initialize(*args):
            run = self.initialize(*args)
            self.unknown('Committed')
            return run
        with self.assertRaisesRegex(ValueError, 'changed after preflight'):
            self.fork(initialize=initialize)
        self.assertFalse((self.root/'continuation/workspace-continuation.json').exists())

    def test_checkpoint_with_outcomes_fails_closed(self):
        self.unknown('Committed')
        with self.assertRaisesRegex(ValueError, 'checkpoint continuation with theory outcomes'):
            self.fork(checkpoint={'source_run':str(self.source), 'theories':{}})
        self.assertFalse((self.root/'continuation').exists())

    def test_tampered_certificate_in_valid_stop_ledger_rejected(self):
        self.refuted('Committed')
        from regen.infrastructure.experiment_ledger import Ledger
        ledger = Ledger(self.source/'theory-outcomes'/digest('Committed'), self.run['run_id'])
        bad = deepcopy(ledger.rows[-1]['data'])
        bad['details']['certificate']['log'] = 'tampered native evidence'
        ledger.append('theory_outcome', **bad)
        with self.assertRaisesRegex(ValueError, 'evidence hash mismatch'):
            self.fork()
        self.assertFalse((self.root/'continuation').exists())

    def test_legacy_completed_refutation_without_stop_is_recovered_and_carried(self):
        from regen.execution.check_executor import CheckExecutor
        from regen.planning.theory_outcomes import _snapshot
        controller = self.controller(self.source, self.run, 'Committed')
        binding = _snapshot(controller)[2]
        metadata = {'thread_id':'thread', 'turn_id':'turn', 'call_id':'refute'}
        proposal = {'action':'check_refutation', 'entry_id':'committed', 'proof_body':'by simp'}
        certificate = self.certificate('Committed')
        task_id = digest({'kind':'refutation', 'binding':binding, 'call':metadata})
        executor = CheckExecutor(self.source/'check-tasks', workers=1)
        try:
            executor.submit(kind='refutation', theory='Committed', binding=binding,
                request={'proposal':proposal, 'metadata':metadata}, task_id=task_id,
                owner='Committed', execute=lambda _: {'status':'refuted', 'certificate':certificate})
            self.assertEqual(executor.wait([task_id], timeout=5)[0]['status'], 'completed')
        finally:
            executor.close()
        self.assertFalse((self.source/'theory-outcomes').exists())
        destination, run = self.fork()
        source = current_outcome(controller)
        target = current_outcome(self.controller(destination, run, 'Committed'))
        self.assertTrue(source['details']['recovered_from_completed_task'])
        self.assertEqual(target['status'], 'refuted')
        self.assertEqual(target['metadata']['continuation']['source_receipt'], source['receipt'])
        self.assertFalse((destination/'check-tasks').exists())

    def test_wrong_certificate_imports_rejected_even_if_native_fixture_checks(self):
        self.refuted('Committed')
        self.run['base_import'] = 'Other_Parent'
        durable(self.source/'run.json', self.run)
        # Re-publish under the changed run binding to isolate import validation.
        controller = self.controller(self.source, self.run, 'Committed')
        from regen.infrastructure.experiment_ledger import Ledger
        ledger = Ledger(self.source/'theory-outcomes'/digest('Committed'), self.run['run_id'])
        old = ledger.rows[-1]['data']
        from regen.planning.theory_outcomes import _snapshot
        ledger.append('theory_outcome', **{**old, 'binding':_snapshot(controller)[2]})
        with self.assertRaisesRegex(ValueError, 'import binding mismatch'):
            self.fork()


if __name__ == '__main__':
    unittest.main()
