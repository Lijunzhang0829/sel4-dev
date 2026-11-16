from pathlib import Path
import sys,copy,unittest

from regen.execution.deferred_audit import policy,DEFERRED
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.tests.current.test_seal_evidence import SealEvidenceTests
from regen.tests.current.seal_fixture import seal,bound
from regen.execution.seal_evidence import validate_seal
from regen.execution.proof_commits import digest
class PolicyTests(unittest.TestCase):
 def candidate(self):
  s=SealEvidenceTests().state();e=seal(s);r=object.__new__(IsabelleChecker);r.imports=lambda:e['imports'];r.complete_context_policy=e['complete_context_policy'];r.audit_policy=DEFERRED
  e['dependencies'].update(bound('source',r.complete_audit_source(s['graph'],s['proofs'])));e['audit_policy']=DEFERRED;s['seal_id']='theory-seal:'+digest(e)[:20];return s,e
 def test_legacy_unchanged(self):
  s=SealEvidenceTests().state();e=seal(s);validate_seal(s,e)
 def test_new_bound_policy(self):
  s,e=self.candidate();validate_seal(s,e,runtime={'audit_policy':DEFERRED})
 def test_no_silent_migration(self):
  s,e=self.candidate()
  with self.assertRaisesRegex(ValueError,'audit policy'):validate_seal(s,e)
  s=SealEvidenceTests().state();e=seal(s)
  with self.assertRaisesRegex(ValueError,'audit policy'):validate_seal(s,e,runtime={'audit_policy':DEFERRED})
 def test_no_missing_flush(self):
  s,e=self.candidate();text=e['dependencies']['source_content'].replace('List.app (fn f => f ()) (rev (!regen_deferred_audits));','');e['dependencies'].update(bound('source',text));s['seal_id']='theory-seal:'+digest(e)[:20]
  with self.assertRaisesRegex(ValueError,'audit source'):validate_seal(s,e,runtime={'audit_policy':DEFERRED})
 def test_combined_policy_binding(self):
  s,e=self.candidate();e['complete_context_policy']='arch-reuse-v1';s['seal_id']='theory-seal:'+digest(e)[:20]
  runtime={'audit_policy':DEFERRED,'complete_context_policy':'arch-reuse-v1'}
  validate_seal(s,e,runtime=runtime)
  with self.assertRaisesRegex(ValueError,'render policy'):validate_seal(s,e,runtime={'audit_policy':DEFERRED})
 def test_empty_positive_projection(self):
  r=object.__new__(IsabelleChecker);r.imports=lambda:['Main'];r.complete_context_policy='isolated-v1';r.audit_policy=DEFERRED
  graph={'nodes':[],'execution_order':[],'file_slot':'Empty.thy','partial_export_projection':'contract-dispositions-v1'}
  text=r.complete_audit_source(graph,{})
  self.assertIn('DEFERRED_AUDIT_COMPLETE',text)
  self.assertIn('regen_deferred_audits := []',text)
 def test_unknown_rejected(self):
  with self.assertRaises(ValueError):policy({'audit_policy':'unknown'})
 def test_runner_forwards_explicit_audit_policy(self):
  import tempfile,json
  from unittest.mock import patch
  from regen.execution.proof_session_runner import _init_run_unlocked
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp); schedule=root/'schedule.json'
   schedule.write_text(json.dumps({'artifact_type':'repository-file-generation-list'}))
   with patch('regen.execution.repository_execution.init_run',return_value={}) as init:
    _init_run_unlocked(root/'proof',root,run_id='fixture',schedule_path=schedule,
      complete_context_policy='arch-reuse-v1',audit_policy=DEFERRED)
   self.assertEqual(init.call_args.kwargs['audit_policy'],DEFERRED)
   self.assertEqual(init.call_args.kwargs['complete_context_policy'],'arch-reuse-v1')
 def test_policy_is_in_journal_binding(self):
  from regen.execution.checker_policy import policy_binding
  self.assertEqual(policy_binding({}),{})
  self.assertEqual(policy_binding({'audit_policy':DEFERRED,'complete_context_policy':'arch-reuse-v1'}),
   {'audit_policy':DEFERRED,'complete_context_policy':'arch-reuse-v1'})
if __name__=='__main__':unittest.main()
