"""Accounting boundary regressions; no models or dataset proofs are invoked."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

PROJECT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(PROJECT/'vero-isabelle/src'))
spec=importlib.util.spec_from_file_location('official_runtime',Path(__file__).with_name('official_codex_runtime.py'))
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.log=runtime.EventLog(self.root/'metrics',{'schema_version':1,'run_id':'test'})
        self.cfg={'run_id':'test','model':'synthetic','reasoning_effort':'low'}

    def session(self):
        self.events=self.root/'events.jsonl';self.events.write_text(json.dumps({'type':'thread.started','thread_id':'t'})+'\n')
        return runtime.UsageSession(self.log,self.cfg,self.root/'home',self.events)

    def response(self):
        p=self.root/'home/sessions/s.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps({'type':'session_meta','payload':{'id':'t'}})+'\n'+json.dumps({
            'timestamp':time.time(),'type':'token_usage_record','payload':{
            'thread_id':'t','turn_id':'turn','response_id':'r',
            'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}}})+'\n')
        return p

    def test_response_is_owned_before_child_output_and_counted_once(self):
        session=self.session()
        self.assertEqual(list(self.log.events())[0]['kind'],'invocation_started')
        p=self.response();p.write_text(p.read_text()+p.read_text().splitlines()[-1]+'\n')
        session.poll();session.poll()
        result=session.finish('interrupted',returncode=1)
        self.assertEqual(result['reported_responses'],1)
        self.assertEqual(result['usage']['total_tokens'],12)
        self.assertFalse(result['usage_complete'])
        self.assertTrue(result['unreported_usage_possible'])
        records=[e for e in self.log.events() if e['kind']=='response_usage']
        self.assertEqual(records[0]['invocation_id'],session.identity)
        self.assertEqual(list(self.log.events())[-1]['kind'],'invocation_finished')

    def test_lost_invocation_binding_is_an_explicit_blocker(self):
        session=self.session();self.response()
        session.collector.invocations.clear()
        with self.assertRaisesRegex(RuntimeError,'ownership'):session.poll()

    def test_completed_turn_has_known_usage(self):
        session=self.session();self.response()
        with self.events.open('a') as f:
            f.write(json.dumps({'type':'turn.started'})+'\n')
            f.write(json.dumps({'type':'turn.completed'})+'\n')
        result=session.finish('completed',returncode=0)
        self.assertTrue(result['usage_complete'])
        self.assertFalse(result['unreported_usage_possible'])

    def test_duplicate_invocation_is_rejected(self):
        self.session()
        with self.assertRaisesRegex(ValueError,'already exists'):self.session()

    def test_evaluator_changes_uid_before_rendering(self):
        cfg={'final_checker':{'command_prefix':['runuser','-u','checker','--'],
            'backend':'fresh-process-v1','env':{'HOME':'/home/agent'}}}
        command=runtime.evaluation_command(cfg,'/benchmark.json','/artifact.json','/work/eval','/work/report.json')
        self.assertEqual(command[:5],['runuser','-u','checker','--','python3'])
        self.assertNotIn('command_prefix',command[-1])
        self.assertIn('/home/checker',command[-1])
        self.assertEqual(cfg['final_checker']['env']['HOME'],'/home/agent')
        compile(command[-1],'evaluation','exec')
        with self.assertRaises(ValueError):runtime.evaluation_command({'final_checker':{}},'b','a','o','r')

    def test_frozen_method_identity_supports_both_baselines(self):
        self.assertEqual(runtime.method_identity({}), 'official-codex')
        for method in ('vero', 'official-codex'):
            self.assertEqual(runtime.method_identity({'method':method}), method)
        with self.assertRaises(ValueError):runtime.method_identity({'method':'unbound'})

    def test_no_model_accounting_probe(self):
        result=runtime.accounting_probe(self.root/'probe')
        self.assertEqual(result['tokens'],12)
        self.assertTrue(result['duplicate_counted_once'])

if __name__=='__main__':unittest.main()
