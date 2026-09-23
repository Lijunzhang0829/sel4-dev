"""Workspace provenance tests use synthetic protocol records, never a provider."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure.workspace_transcript import record_workspace_action, audit_workspace_session
from regen.infrastructure.llm_transcript import inspect_call_artifact, artifact_paths, _hashes


class WorkspaceTranscriptTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.binary=self.root/'codex';self.binary.write_bytes(b'fixture executable identity')
        self.settings={'codex_bin':str(self.binary),'codex_sha256':hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        patcher=patch('regen.infrastructure.workspace_transcript.model_settings',return_value=self.settings)
        patcher.start();self.addCleanup(patcher.stop)
        self.proposal={'action':'submit_batch','proofs':{'helper':'by simp'}}
        self.request={'id':50,'method':'item/tool/call','params':{
            'threadId':'thread-one','turnId':'turn-one','callId':'call-one','namespace':None,
            'tool':'proof_action','arguments':{'theory':'Demo','proposal':self.proposal}}}
        self.events=[
            self.event('local',{'event':'launch','provider':'codex-app-server','synthetic':False,
                'command':[str(self.binary),'app-server','--stdio'],'model':'test',
                'executable_sha256':self.settings['codex_sha256']}),
            self.event('out',{'id':1,'method':'thread/start','params':{
                'model':'test','sandbox':'read-only','approvalPolicy':'never',
                'dynamicTools':[{'type':'function','name':'proof_action','description':'harness',
                                'inputSchema':{'type':'object'}}]}}),
            self.event('in',{'id':1,'result':{'thread':{'id':'thread-one'}}}),
            self.event('out',{'id':2,'method':'turn/start','params':{'threadId':'thread-one','model':'test',
                                'input':[{'type':'text','text':'prove the theory'}]}}),
            self.event('in',{'id':2,'result':{'turn':{'id':'turn-one'}}}),
            self.event('in',self.request)]
        self.path=self.root/'session.jsonl'
        self.write_events()
        self.prefix=self.root/'call'

    def event(self,direction,message):
        return {'time_unix':0,'direction':direction,'message':
                {'raw_json':json.dumps(message),'parsed':message} if direction=='in' else message}

    def write_events(self):
        self.path.write_text(''.join(json.dumps(e)+'\n' for e in self.events))
        self.metadata={'thread_id':'thread-one','turn_id':'turn-one','call_id':'call-one',
            'raw_request':deepcopy(self.request),'transcript_path':str(self.path),
            'transcript_offset':self.path.stat().st_size}

    def record(self,**kwargs):
        return record_workspace_action(self.prefix,'current harness context',self.proposal,self.metadata,
                                       theory='Demo',model='test',**kwargs)

    def test_real_bound_action_has_distinct_provider_and_no_false_no_tool_claim(self):
        self.assertEqual(json.loads(self.record()),self.proposal)
        meta=inspect_call_artifact(self.prefix)
        self.assertEqual(meta['provider'],'codex-workspace-audited')
        self.assertFalse(meta['no_tool_events'])
        self.assertEqual(meta['dispatch_kind'],'dynamic_tool_call')
        self.assertEqual(meta['provider_dispatches'],0)
        self.assertTrue(meta['sealable_generation'])
        self.assertEqual(len([p for p in artifact_paths(self.prefix).values() if p.is_file()]),6)
        audit=audit_workspace_session(self.path,require_terminal=False)
        self.assertEqual(audit['tool_calls'],1)
        with self.assertRaises(ValueError): audit_workspace_session(self.path)

    def test_proposal_offset_and_thread_forgery_are_rejected(self):
        for field,value in [('thread_id','wrong-thread'),('transcript_offset',1),('transcript_offset',-1),('tool_request_offset',1)]:
            with self.subTest(field=field,value=value):
                metadata={**self.metadata,field:value}
                with self.assertRaises(ValueError):
                    record_workspace_action(self.prefix,'context',self.proposal,metadata,theory='Demo',model='test')
        with self.assertRaises(ValueError):
            record_workspace_action(self.prefix,'context',{'action':'pause'},self.metadata,theory='Demo',model='test')

    def test_tampered_final_even_with_rehashed_artifacts_does_not_match_provider_call(self):
        self.record();paths=artifact_paths(self.prefix)
        paths['final'].write_text(json.dumps({'action':'pause'}))
        meta=json.loads(paths['meta'].read_text());meta.update(_hashes(paths));paths['meta'].write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError,'proposal'):
            inspect_call_artifact(self.prefix)

    def test_transcript_prefix_hash_tampering_is_rejected(self):
        self.record()
        self.path.write_text(self.path.read_text().replace('call-one','call-two'))
        with self.assertRaisesRegex(ValueError,'prefix hash'):
            inspect_call_artifact(self.prefix)

    def test_synthetic_marker_cannot_be_overridden_by_metadata(self):
        self.events[0]['message']['synthetic']=True;self.write_events()
        self.metadata['real']=True
        self.record()
        self.assertFalse(inspect_call_artifact(self.prefix,require_real=False)['sealable_generation'])
        with self.assertRaises(ValueError): inspect_call_artifact(self.prefix)

    def test_unconfirmed_turn_is_rejected_even_if_tool_names_are_valid(self):
        self.events.pop(4)  # omit the turn/start response
        self.write_events()
        with self.assertRaisesRegex(ValueError,'unconfirmed'):
            self.record()

    def test_forged_production_executable_is_rejected(self):
        self.events[0]['message']['executable_sha256']='forged';self.write_events()
        with self.assertRaisesRegex(ValueError,'trusted Codex'):
            self.record()

    def test_builtins_approvals_and_unknown_tools_rejected(self):
        for message in [
            {'method':'item/started','params':{'item':{'type':'commandExecution'}}},
            {'id':66,'method':'item/commandExecution/requestApproval','params':{}},
            {'id':66,'method':'item/tool/call','params':{**self.request['params'],'tool':'shell','callId':'bad'}}]:
            with self.subTest(message=message):
                original=self.events[:]
                self.events.insert(-1,self.event('in',message));self.write_events()
                with self.assertRaises(ValueError):self.record()
                self.events=original

    def test_terminal_audit_rejects_later_violation_without_rewriting_prior_action(self):
        self.record()
        self.events.extend([
            self.event('out',{'id':50,'result':{'success':True,'contentItems':[{'type':'inputText','text':'ok'}]}}),
            self.event('in',{'method':'turn/completed','params':{'threadId':'thread-one','turn':{'id':'turn-one','status':'completed'}}})])
        self.write_events()
        self.assertTrue(audit_workspace_session(self.path)['terminal'])
        self.events.append(self.event('local',{'event':'session_failed','nonsealable':True}));self.write_events()
        with self.assertRaises(ValueError):audit_workspace_session(self.path)
        self.assertTrue(inspect_call_artifact(self.prefix)['sealable_generation'])


if __name__=='__main__':unittest.main()
