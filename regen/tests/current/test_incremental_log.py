import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from regen.infrastructure import incremental_log as logs
from regen.execution.proof_commits import ProofCommits, durable, digest


class IncrementalLogTests(unittest.TestCase):
    def test_fingerprint_batch_deduplicates_but_rechecks_next_read(self):
        value = {'shared': {'proof': 'x' * 10000}}
        durable(self.path, value, immutable=True)
        second = self.path.with_name('000002.json')
        durable(second, {**value, 'counter': 2}, immutable=True)
        expected = [logs.artifact_fingerprint(p) for p in (self.path, second)]
        seen = []
        original = logs.ObjectStore.node
        def node(store, key, *args, **kwargs):
            seen.append((str(store.root), key))
            return original(store, key, *args, **kwargs)
        with patch.object(logs.ObjectStore, 'node', node):
            with logs.fingerprint_batch():
                actual = [logs.artifact_fingerprint(p) for p in (self.path, second)]
        self.assertEqual(actual, expected)
        self.assertEqual(len(seen), len(set(seen)))
        obj = logs.ObjectStore(self.root).path(expected[0][0][0])
        obj.write_bytes(b'corrupt after first verification')
        with logs.fingerprint_batch(), self.assertRaises(ValueError):
            logs.artifact_fingerprint(self.path)

    def test_fingerprint_batch_is_context_local(self):
        durable(self.path, {'proof': 'x' * 10000}, immutable=True)
        expected = logs.artifact_fingerprint(self.path)
        def read(_):
            with logs.fingerprint_batch():
                return logs.artifact_fingerprint(self.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(read, range(4))), [expected] * 4)
        self.assertIsNone(logs._FINGERPRINT_BATCH.get())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'run'
        logs.enable(self.root)
        self.path = self.root/'theories/T/acceptance/commits/000001.json'

    def test_arbitrary_json_roundtrip_and_duplicate_subtrees(self):
        large = {'proof': 'λ by simp\n'*2000, 'feedback': [False, None, 2, {'ref':'user'}]}
        value = {'first':large, 'second':dict(large), '$log_format':'user value'}
        durable(self.path, value, immutable=True)
        self.assertEqual(logs.read_artifact(self.path), value)
        self.assertLess(self.path.stat().st_size, 200)
        before = set((self.root/'log-objects').iterdir())
        durable(self.path.with_name('000002.json'), value, immutable=True)
        self.assertEqual(before, set((self.root/'log-objects').iterdir()))
        decoded = logs.read_artifact(self.path)
        decoded['first']['feedback'].append('not shared')
        self.assertNotEqual(decoded['first'], decoded['second'])

    def test_delta_snapshot_supports_delete_append_and_old_version(self):
        a = {'cost':2, 'items':[{'proof':'x'*8000}], 'obsolete':True}
        b = {'cost':3, 'items':a['items'] + [{'proof':'new'}]}
        durable(self.path,a,immutable=True)
        second = self.path.with_name('000002.json')
        durable(second,b,immutable=True)
        self.assertEqual(logs.read_artifact(self.path),a)
        self.assertEqual(logs.read_artifact(second),b)

    def test_missing_or_corrupt_objects_invalidate_warm_journal(self):
        j = ProofCommits(self.path.parent.parent,{},lambda *args:None)
        j.append('initial',{'proof':'x'*10000},{})
        self.assertEqual(j.replay()['proof'],'x'*10000)
        manifest=json.loads(self.path.read_text())
        obj=logs.ObjectStore(self.root).path(manifest['root'])
        raw=obj.read_bytes()
        obj.write_bytes(b'corruption')
        with self.assertRaises(ValueError): j.replay()
        obj.write_bytes(raw)
        self.assertEqual(j.replay()['proof'],'x'*10000)
        obj.unlink()
        with self.assertRaises(FileNotFoundError): j.replay()

    def test_objects_before_manifest_interruption_is_not_acceptance(self):
        original=logs.atomic_bytes
        def fail(path,raw,**kwargs):
            if Path(path)==self.path: raise OSError('crash before manifest')
            return original(path,raw,**kwargs)
        j=ProofCommits(self.path.parent.parent,{},lambda *args:None)
        with patch.object(logs,'atomic_bytes',side_effect=fail), self.assertRaises(OSError):
            j.append('initial',{'proof':'x'*8000},{})
        self.assertIsNone(ProofCommits(j.directory,{},lambda *args:None).replay())
        j.append('initial',{'proof':'x'*8000},{})
        self.assertEqual(len(list(j.directory.glob('commits/*.json'))),1)

    def test_published_commit_recovers_without_head(self):
        from regen.execution import proof_commits
        original=proof_commits.durable
        def fail(path,value,**kwargs):
            if path.name=='HEAD.json': raise OSError('crash after publish')
            return original(path,value,**kwargs)
        j=ProofCommits(self.path.parent.parent,{},lambda *args:None)
        with patch.object(proof_commits,'durable',side_effect=fail), self.assertRaises(OSError):
            j.append('initial',{'cost':9},{})
        recovered=ProofCommits(j.directory,{},lambda *args:None)
        self.assertEqual(recovered.replay(),{'cost':9})
        self.assertEqual(json.loads((j.directory/'HEAD.json').read_text())['sequence'],1)
        recovered.append('observe',{'cost':10},{})
        first=logs.read_artifact(self.path)['commit_id']
        self.assertEqual(recovered.replay_snapshot(commit_id=first)['state'],{'cost':9})

    def test_logical_commit_ids_match_inline_and_validation_runs(self):
        seen=[]
        j=ProofCommits(self.path.parent.parent,{'x':1},lambda *a:seen.append(a[2]))
        j.append('initial',{'proof':'by simp','cost':1},{'log':'not a path'})
        record=logs.read_artifact(self.path)
        self.assertEqual(record['commit_id'],digest({k:v for k,v in record.items() if k!='commit_id'}))
        self.assertEqual(seen,['initial'])
        ProofCommits(j.directory,{'x':1},lambda *a:seen.append(a[2])).replay()
        self.assertEqual(seen,['initial','initial'])

    def test_parallel_objects_and_collision(self):
        value={'source':'x'*5000}
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _:durable(self.path,value,immutable=True),range(8)))
        self.assertEqual(logs.read_artifact(self.path),value)
        with self.assertRaises(ValueError):durable(self.path,{'other':1},immutable=True)

    def test_fork_copies_reachable_content(self):
        durable(self.path,{'proof':'x'*10000},immutable=True)
        other=Path(self.temp.name)/'other';logs.enable(other)
        dest=other/'commits/000001.json'
        logs.copy_logged_file(self.path,dest)
        self.assertEqual(logs.read_artifact(dest),logs.read_artifact(self.path))
        self.assertTrue(list((other/'log-objects').glob('*')))

    def test_frozen_inputs_remain_plain_and_existing_run_rejected(self):
        durable(self.root/'run.json',{'frozen':True},immutable=True)
        self.assertEqual(json.loads((self.root/'run.json').read_text()),{'frozen':True})
        existing=Path(self.temp.name)/'existing';existing.mkdir()
        (existing/'run.json').write_text('{}')
        with self.assertRaises(ValueError):logs.enable(existing)

    def test_planning_queries_use_same_store(self):
        from regen.planning.workspace_harness import save
        p=self.root/'queries/000001.json';v={'request':{'action':'validate'},'response':{'valid':False}}
        save(p,v)
        self.assertEqual(logs.read_artifact(p),v)

    def test_planning_restart_repairs_projection_and_does_not_resend_unknown(self):
        from regen.tests.current.test_workspace_planning import WorkspacePlanningTest
        from regen.planning import workspace_harness as wh
        fixture=WorkspacePlanningTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        output=fixture.root/'incremental-planning';logs.enable(output)
        h=wh.WorkspaceHarness(fixture.input,output,fixture.native)
        original=wh.save
        def fail(path,value):
            if Path(path)==h.state_path:raise OSError('projection not updated')
            return original(path,value)
        with patch.object(wh,'save',side_effect=fail),self.assertRaises(OSError):
            h.dispatch({'action':'inspect','values':['p1']})
        h=wh.WorkspaceHarness(fixture.input,output,fixture.native)
        self.assertEqual(h.state['sequence'],1)
        self.assertIn('p1',h.state['inspected_premises'])
        with patch.object(h,'_dispatch',side_effect=OSError('worker interrupted')),self.assertRaises(OSError):
            h.dispatch({'action':'contracts'})
        h=wh.WorkspaceHarness(fixture.input,output,fixture.native)
        self.assertEqual(h.state['sequence'],2)
        self.assertEqual(h.state['interrupted_queries'],[2])
        h.dispatch({'action':'contracts'})
        self.assertEqual(h.state['sequence'],3)

    def test_real_journal_shared_workspace_and_request_recovery_interfaces(self):
        from regen.tests.current.test_shared_proof_workspace import setup_run
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        from regen.execution import repository_execution as execution
        from regen.planning.batch_file_planner import read_bound_json, committed_batch_results
        run,packages,initial,current=setup_run(self.root)
        journal=execution.journal(self.root,run,packages[0])
        self.assertEqual(journal.replay(),current)
        workspace=SharedProofWorkspace(self.root)
        rows=workspace.index(theory='Source')['records']
        self.assertEqual(len(rows),2)
        self.assertTrue(workspace.read([rows[0]['source_ref']])['records'][0]['proof_body'])
        commits=list((journal.directory/'commits').glob('*.json'))
        self.assertTrue(all(json.loads(p.read_text())['$log_format']==logs.FORMAT for p in commits))
        self.assertIsInstance(committed_batch_results(journal.directory.parent),dict)
        path=self.root/'theories/Source/batch-requests/000001/input.json'
        value={'graph':current['graph'],'proofs':current['proofs']}
        durable(path,value,immutable=True)
        self.assertEqual(read_bound_json(path,digest(value)),value)

    def test_nested_object_corruption_is_detected_after_cache_hit(self):
        j=ProofCommits(self.path.parent.parent,{},lambda *args:None)
        j.append('initial',{'proof':'p'*12000},{'log_content':'q'*12000})
        j.replay()
        store=logs.ObjectStore(self.root)
        root=json.loads(self.path.read_text())['root']
        refs=store.fingerprint(root)
        child=next(key for key,_ in refs if key!=root)
        store.path(child).write_bytes(b'corrupt child')
        with self.assertRaises(ValueError):j.replay()

    def test_legacy_copy_remains_byte_exact(self):
        old=Path(self.temp.name)/'legacy.json';old.write_bytes(b'legacy raw\n')
        destination=Path(self.temp.name)/'copy.json'
        logs.copy_logged_file(old,destination)
        self.assertEqual(old.read_bytes(),destination.read_bytes())


if __name__=='__main__': unittest.main()
