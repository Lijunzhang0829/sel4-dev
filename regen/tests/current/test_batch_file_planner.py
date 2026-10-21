import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from regen.execution.proof_commits import ProofCommits
from regen.execution.repository_execution import validator
from regen.tests.current.test_proof_commits import fixture, check
from regen.planning.batch_file_planner import _run_batch_file, batch_query, planner_input, seal_theory


class BatchControllerTest(unittest.TestCase):
    def test_invalid_action_feedback_and_checkpoint_resume_preserve_cost(self):
        package, initial = fixture()
        responses=[['invalid top-level JSON'], {'action':'pause','reason':'explicit checkpoint'}]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/'theories/Demo';directory.mkdir(parents=True)
            journal=ProofCommits(directory/'acceptance',{},validator(package))
            journal.append('initial', initial, {'check':check()})
            checker=Mock(timeout=900, batch_incremental=True); checker.imports.return_value=['Semantic_Base.Semantic_Root']
            run={'run_id':'test-run','execution_models':{'File Planner':'test'}}
            setup=(run,package,directory,journal,checker,[])
            prompts=[]
            def call(*args,**kwargs):
                prompts.append(args[1]);return json.dumps(responses[len(prompts)-1])
            with patch('regen.execution.repository_execution.setup',return_value=setup), \
                 patch('regen.execution.repository_execution.progress',return_value={'checked_contracts':0}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model',return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm',side_effect=call), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact',return_value={'sealable_generation':True}):
                first=_run_batch_file(root,'Demo',max_requests=1)
                self.assertEqual(first['status'],'checkpoint')
                self.assertEqual(journal.replay()['batch_requests'],1)
                second=_run_batch_file(root,'Demo',max_requests=1)
                self.assertEqual(second['status'],'paused')
            state=journal.replay()
            self.assertEqual(state['batch_requests'],2)
            self.assertEqual(state['graph'],initial['graph'])
            self.assertEqual(state['proofs'],{})
            self.assertIn('model response must be one JSON object',prompts[1])
            self.assertEqual(checker.close_prefix.call_count,2)

    def test_submit_batch_commits_multiple_nodes_and_charges_once(self):
        from regen.tests.current.test_proof_batches import Checker
        package, initial = fixture()
        proposal={'action':'submit_batch', 'reason':'one helper and its consumer',
                  'graph':{'contract_dependencies':{'contract':['helper']}},
                  'proofs':{'helper':'by simp','contract':'using helper by simp'}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/'theories/Demo'; directory.mkdir(parents=True)
            journal=ProofCommits(directory/'acceptance',{},validator(package))
            journal.append('initial',initial,{'check':check()})
            checker=Checker(); checker.close_prefix=Mock()
            setup=({'run_id':'test-run','execution_models':{'File Planner':'test'}},package,directory,journal,checker,[])
            with patch('regen.execution.repository_execution.setup',return_value=setup), \
                 patch('regen.execution.repository_execution.progress',return_value={'checked_contracts':1}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model',return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm',return_value=json.dumps(proposal)), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact',return_value={'sealable_generation':True}):
                result=_run_batch_file(root,'Demo',max_requests=1)
            self.assertEqual(result['status'],'checkpoint')
            state=journal.replay()
            self.assertEqual(state['accepted_order'],['helper','contract'])
            self.assertEqual(state['batch_requests'],1)
            self.assertEqual(state['batch_feedback']['status'],'accepted')
            self.assertEqual(checker.calls,1)
            checker.close_prefix.assert_called_once()

    def test_failed_seal_audit_returns_native_diagnostic(self):
        from regen.execution.proof_batches import prepare_batch
        package, initial=fixture()
        state=prepare_batch(package,initial,{'proofs':{'helper':'by simp','contract':'by simp'}})
        checker=Mock(timeout=900, batch_incremental=True); checker.imports.return_value=['Semantic_Base.Semantic_Root'];checker.check_complete.return_value=check()
        checker.last_complete_audit={'verdict':'red','diagnostic':'native missing dependency detail'}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('regen.analysis.retained_semantic_impact.audit_theory',side_effect=ValueError('audit failed')):
            new,feedback=seal_theory(Path(tmp),{'premise_snapshot':tmp},package,Path(tmp),checker,[],state)
        self.assertIsNone(new)
        self.assertEqual(feedback['stage'],'semantic-audit')
        self.assertEqual(feedback['check']['diagnostic'],'native missing dependency detail')
        checker.build_generated_session.assert_not_called()

    def test_published_batch_and_continuation_proof_sources(self):
        from regen.execution.proof_session_runner import publish_generated_records
        package,state=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'theories/Generated_Demo/acceptance/commits'
            directory.mkdir(parents=True)
            commits=[{'event':'initial','evidence':{'continuation':{}},'state':{'proofs':{'helper':'by simp'}}},
                     {'event':'batch','state':{'proofs':{'helper':'by simp','contract':'by simp'}}},
                     {'event':'batch','state':{'proofs':{'helper':'by simp','contract':'by simp'}}}]
            for number,commit in enumerate(commits,1):
                (directory/f'{number:06d}.json').write_text(json.dumps(commit))
            publish_generated_records(root,state['graph'],'seal:test',package)
            records=json.loads((root/'generated-premises.json').read_text())['records']
            by_name={r['canonical_name'].split('.')[-1]:r for r in records}
            self.assertEqual(by_name['helper']['proof_commit_id'],'000001')
            self.assertEqual(by_name['target']['proof_commit_id'],'000002')
            self.assertEqual(by_name['target']['proof_body'],'by simp')
            from regen.execution.proof_commits import digest
            self.assertEqual(by_name['target']['proof_sha256'],digest('by simp'))
            self.assertIn('context',by_name['target'])

    def test_query_uses_frozen_sealed_catalog_and_exact_node_context(self):
        package,state=fixture();state['graph']['nodes'][0]['context']=['context X']
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'theory/batch-requests/000001';directory.mkdir(parents=True)
            checker=Mock(timeout=900, batch_incremental=True); checker.imports.return_value=['Semantic_Base.Semantic_Root']
            with patch('regen.execution.round_recall.select_round_facts',return_value={'records':[]}) as select:
                batch_query({'premise_database':'db'},package,state,checker,directory,
                    {'premise_query':'test','context_node_id':state['graph']['nodes'][0]['id']})
                self.assertEqual(select.call_args.kwargs['generated_records'],Path(tmp)/'theory/sealed-premises.json')
                select.call_args.kwargs['symbol_resolver'](['c'])
                checker.resolve_symbols.assert_called_once_with(['c'],['context X'])
            with self.assertRaises(ValueError):
                batch_query({'premise_database':'db'},package,state,checker,directory,
                    {'premise_query':'test','context_node_id':'invented'})

    def test_view_preserves_requested_proofs_and_failure_not_large_raw_logs(self):
        package,state=fixture();state['batch_examples']={'a':{'proof_body':'by simp'}}
        state['batch_feedback']={'check':{'diagnostic':'precise failure','source_content':'large','log_content':'large'}}
        view=planner_input(package,state)
        self.assertEqual(view['requested_accepted_proofs']['a']['proof_body'],'by simp')
        self.assertEqual(view['last_feedback'],{'check':{'diagnostic':'precise failure'}})
        self.assertEqual(state['batch_feedback']['check']['source_content'],'large')

class BatchMemoryTest(unittest.TestCase):
    def test_view_retains_separate_proof_failure_and_notes_after_query(self):
        package,state=fixture()
        state['batch_feedback']={'status':'available','records':[]}
        state['batch_proof_feedback']={'status':'rejected_batch','check':{
            'diagnostic':'remaining state_relation subgoal', 'source_content':'huge source',
            'log_content':'huge log', 'command':['docker','run']}}
        state['batch_notes']='Use certified components; do not repeat blind simp.'
        view=planner_input(package,state)
        self.assertEqual(view['last_feedback']['status'],'available')
        self.assertEqual(view['last_proof_feedback']['check'],{'diagnostic':'remaining state_relation subgoal'})
        self.assertEqual(view['planner_notes'],state['batch_notes'])
        self.assertEqual(state['batch_proof_feedback']['check']['log_content'],'huge log')

    def test_red_then_empty_query_preserves_failure_query_history_and_notes(self):
        package,initial=fixture()
        responses=[{'action':'submit_batch','proofs':{'helper':'by simp'},
                    'notes':'The pspace component remains unresolved.'},
                   {'action':'query','request':{'premise_queries':[{'query':'absent_helper','mode':'exact'}]}},
                   {'action':'pause','reason':'inspect remaining goal'}]
        empty={'kind':'fact_selection','records':[], 'queries':[{
            'query':'absent_helper','effective_mode':'exact','status':'no_matches','selected_record_ids':[]}]}
        red={'check':{'verdict':'red','returncode':1,'diagnostic':'UNRESOLVED_PSPACE_GOAL'}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/'theories/Demo';directory.mkdir(parents=True)
            journal=ProofCommits(directory/'acceptance',{},validator(package))
            journal.append('initial',initial,{'check':check()})
            checker=Mock(timeout=900, batch_incremental=True); checker.imports.return_value=['Semantic_Base.Semantic_Root'];setup=({'run_id':'test-run','execution_models':{'File Planner':'test'}},package,directory,journal,checker,[])
            prompts=[]
            def call(*args,**kwargs):
                prompts.append(args[1]);return json.dumps(responses[len(prompts)-1])
            with patch('regen.execution.repository_execution.setup',return_value=setup), \
                 patch('regen.execution.repository_execution.progress',return_value={}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model',return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm',side_effect=call), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact',return_value={'sealable_generation':True}), \
                 patch('regen.planning.batch_file_planner.batch_query',return_value=empty), \
                 patch('regen.execution.proof_batches.check_batch',return_value=red):
                result=_run_batch_file(root,'Demo',max_requests=3)
            self.assertEqual(result['status'],'paused')
            state=journal.replay();view=planner_input(package,state)
            self.assertEqual(state['proofs'],{})
            self.assertEqual(state['batch_requests'],3)
            self.assertIn('UNRESOLVED_PSPACE_GOAL',json.dumps(view['last_proof_feedback']))
            third=json.loads(prompts[2].split('\nINPUT:\n',1)[1])
            self.assertIn('UNRESOLVED_PSPACE_GOAL',json.dumps(third['last_proof_feedback']))
            self.assertEqual(third['planner_notes'],'The pspace component remains unresolved.')
            self.assertIn('absent_helper',json.dumps(third['query_memory']))
            self.assertIn('no_matches',json.dumps(third['last_feedback']))
            self.assertEqual(third['query_memory'][0]['returned_record_ids'],[])
            self.assertEqual(third['recent_actions'][1]['read_action'],
                             {'action':'read_attempts','request_numbers':[2]})

    def test_restore_old_artifacts_retains_failure_and_empty_query_without_writing(self):
        from regen.planning.batch_file_planner import restore_batch_memory
        from copy import deepcopy
        package,state=fixture();state['batch_requests']=2
        original=deepcopy(state)
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            one=directory/'batch-requests/000001';one.mkdir(parents=True)
            two=directory/'batch-requests/000002';two.mkdir()
            (one/'proposal.json').write_text(json.dumps({'action':'submit_batch','proofs':{'helper':'by simp'},
                'notes':'Keep the verified decomposition.'}))
            # Interrupted older versions may have only the native check artifact.
            (one/'check.json').write_text(json.dumps({'check':{'verdict':'red','diagnostic':'NATIVE_REMAINING_GOAL'}}))
            (two/'proposal.json').write_text(json.dumps({'action':'query','request':{
                'premise_queries':[{'query':'missing_fact','mode':'exact'}]}}))
            (two/'feedback.json').write_text(json.dumps({'kind':'fact_selection','status':'available',
                'records':[],'queries':[{'query':'missing_fact','status':'no_matches','selected_record_ids':[]}]}))
            before={str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            restored=restore_batch_memory(directory,state)
            self.assertEqual(state,original)
            self.assertEqual(restored['graph'],state['graph'])
            self.assertEqual(restored['proofs'],state['proofs'])
            self.assertEqual(restored['batch_requests'],2)
            self.assertEqual(restored['batch_notes'],'Keep the verified decomposition.')
            self.assertIn('NATIVE_REMAINING_GOAL',json.dumps(restored['batch_proof_feedback']))
            self.assertIn('missing_fact',json.dumps(restored['batch_history']))
            self.assertIn('no_matches',json.dumps(restored['batch_history']))
            after={str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            self.assertEqual(before,after)

    def test_invalid_notes_are_feedback_not_a_crash(self):
        package,initial=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'theories/Demo';directory.mkdir(parents=True)
            journal=ProofCommits(directory/'acceptance',{},validator(package))
            journal.append('initial',initial,{'check':check()})
            setup=({'run_id':'test-run','execution_models':{'File Planner':'test'}},package,directory,journal,Mock(),[])
            with patch('regen.execution.repository_execution.setup',return_value=setup), \
                 patch('regen.execution.repository_execution.progress',return_value={}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model',return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm',return_value=json.dumps({'action':'query','notes':{},'request':{}})), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact',return_value={'sealable_generation':True}):
                result=_run_batch_file(root,'Demo',max_requests=1)
            self.assertEqual(result['status'],'checkpoint')
            feedback=journal.replay()['batch_feedback']
            self.assertEqual(feedback['status'],'rejected_request')
            self.assertIn('notes',feedback['diagnostic'])


if __name__=='__main__':unittest.main()


class BatchDiagnosticTest(unittest.TestCase):
    def test_failure_excerpt_matches_checked_source_location(self):
        from regen.planning.batch_file_planner import compact_feedback
        check={'source':'/checks/Demo.thy', 'source_content':'first\nsecond\nthird\n',
               'diagnostic':'error (line 2 of "/candidate/Demo.thy")', 'log_content':'large'}
        result=compact_feedback(check)
        self.assertEqual(result['source_error_line'],2)
        self.assertEqual(result['source_excerpt'][1],{'line':2,'text':'second'})
        self.assertNotIn('source_content',result)
        other={**check,'diagnostic':'error (line 2 of "/base/Parent.thy")'}
        self.assertNotIn('source_excerpt',compact_feedback(other))

    def test_native_audit_bodies_stay_in_evidence_not_nested_model_feedback(self):
        from copy import deepcopy
        from regen.execution.proof_commits import digest
        from regen.planning.batch_file_planner import compact_feedback
        body='<batch_audit><prefix>'+('accepted proof body\n'*50000)+'</prefix></batch_audit>'
        check={'verdict':'green','audit_content':body,'audit_sha256':digest(body),
               'audit':'/checks/worker/1.audit.xml','request_id':1}
        feedback={'status':'accepted','check':deepcopy(check),
                  'observations':[{'result':{'check':deepcopy(check)}}]}
        saved=deepcopy(feedback)
        compact=compact_feedback(feedback)
        self.assertLess(len(json.dumps(compact)),1000)
        self.assertNotIn('accepted proof body',json.dumps(compact))
        for item in [compact['check'],compact['observations'][0]['result']['check']]:
            self.assertEqual(item['audit_sha256'],check['audit_sha256'])
            self.assertEqual(item['audit'],check['audit'])
            self.assertEqual(item['request_id'],1)
        self.assertEqual(feedback,saved)
        package,state=fixture();state['batch_feedback']=feedback
        payload=planner_input(package,state)
        self.assertNotIn('accepted proof body',json.dumps(payload))
        self.assertEqual(state['batch_feedback']['check']['audit_content'],body)

    def test_query_runtime_error_is_feedback_and_controller_continues(self):
        package, initial=fixture()
        responses=[{'action':'query','request':{'premise_query':'missing'}},
                   {'action':'pause','reason':'tool unavailable'}]
        prompts=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/'theories/Demo';directory.mkdir(parents=True)
            journal=ProofCommits(directory/'acceptance',{},validator(package))
            journal.append('initial',initial,{'check':check()})
            checker=Mock(timeout=900, batch_incremental=True); checker.imports.return_value=['Semantic_Base.Semantic_Root']
            setup=({'run_id':'test-run','execution_models':{'File Planner':'test'}},package,directory,journal,checker,[])
            def call(*args,**kwargs):
                prompts.append(args[1]);return json.dumps(responses[len(prompts)-1])
            with patch('regen.execution.repository_execution.setup',return_value=setup), \
                 patch('regen.execution.repository_execution.progress',return_value={}), \
                 patch('regen.execution.proof_session_runner.frozen_role_model',return_value='test'), \
                 patch('regen.planning.batch_file_planner.call_llm',side_effect=call), \
                 patch('regen.planning.batch_file_planner.batch_query',side_effect=RuntimeError('native lookup failed')), \
                 patch('regen.planning.batch_file_planner.validate_call_artifact',return_value={'sealable_generation':True}):
                result=_run_batch_file(root,'Demo',max_requests=2)
            self.assertEqual(result['status'],'paused')
            self.assertIn('tool_failed',prompts[1])
            self.assertIn('native lookup failed',prompts[1])
            self.assertEqual(journal.replay()['proofs'],{})
