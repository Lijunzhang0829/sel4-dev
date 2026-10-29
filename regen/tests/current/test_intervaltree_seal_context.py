"""Missing trusted metadata must fail before native work, never as candidate RED."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.analysis.retained_semantic_impact import audit_theory, load_audit_context
from regen.execution.check_tasks import NativePreparationError
from regen.planning.batch_file_planner import seal_theory
from regen.preparation.checker_context import load_context
from regen.preparation.intervaltree_dataset import premise_audit_context
from regen.preparation.premise_database import digest
from regen.preparation.refine_campaign import sha256_json


class IntervaltreeSealContextTests(unittest.TestCase):
    def test_preparation_metadata_is_consumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'audit.json'
            value=premise_audit_context('input:fixed', 'a'*64)
            path.write_text(json.dumps(value))
            self.assertEqual(load_audit_context(path),value)
            self.assertEqual(value['campaign_id'],'independent-intervaltree:input:fixed')
            self.assertEqual(value['records'],[])

    def test_broken_context_stops_before_native_audit(self):
        for value in ([], None, {'semantic_base_seal_id':'base','records':[]},
                      {'campaign_id':'','semantic_base_seal_id':'base','records':[]},
                      {'campaign_id':'test','semantic_base_seal_id':'base','records':[None]}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'audit.json';path.write_text(json.dumps(value))
                checker=Mock()
                with self.assertRaises(NativePreparationError):
                    audit_theory(checker=checker,graph={},proofs={},
                        premise_snapshot_path=path,output_path=Path(tmp)/'impact.json')
                checker.audit_complete_theory.assert_not_called()

    def test_missing_campaign_is_not_a_retryable_seal_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'audit.json'
            path.write_text(json.dumps({'semantic_base_seal_id':'base','records':[]}))
            checker=Mock()
            state={'graph':{'nodes':[{'id':'goal'}]},'proofs':{'goal':'by simp'}}
            with self.assertRaisesRegex(NativePreparationError,'campaign_id'):
                seal_theory(root,{'premise_snapshot':str(path)}, {},root,checker,[],state)
            checker.check_complete.assert_not_called()
            checker.audit_complete_theory.assert_not_called()
            self.assertEqual(state['proofs'],{'goal':'by simp'})

    def test_loader_rejects_missing_or_rebound_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            paths={k:k for k in ('l4v','semantic_workspace','heaps','isabelle')}
            for p in paths.values():(root/p).mkdir()
            (root/'parent-premises.sqlite').touch()
            audit=root/'audit.json'
            good=premise_audit_context('input:fixed','a'*64)
            audit.write_text(json.dumps(good))
            core={'target_session':'Intervaltree','artifact_type':'intervaltree-checker-context',
                  'paths':paths,'premise_database':'parent-premises.sqlite',
                  'premise_audit_context':'audit.json','premise_audit_context_sha256':digest(audit)}
            (root/'checker-context.json').write_text(json.dumps({**core,
                'checker_context_id':'checker-context:'+sha256_json(core)[:20]}))
            with patch('regen.preparation.premise_database.verify_database',return_value=1):
                self.assertEqual(load_context(root)['target_session'],'Intervaltree')
                audit.write_text(json.dumps({k:v for k,v in good.items() if k!='campaign_id'}))
                with self.assertRaisesRegex(NativePreparationError,'campaign_id'):load_context(root)
                audit.write_text(json.dumps({**good,'campaign_id':'different'}))
                with self.assertRaisesRegex(NativePreparationError,'binding mismatch'):load_context(root)


if __name__=='__main__': unittest.main()
