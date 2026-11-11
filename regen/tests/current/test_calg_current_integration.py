"""Current C input must bind the same parent, targets and comparison groups."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.preparation.calg_shuffled_dataset import comparison_groups, contracts_for, inventory
from regen.preparation.refine_campaign import sha256_json
from regen.preparation.checker_context import load_context
from regen.preparation.premise_database import digest

PROJECT=Path(__file__).resolve().parents[3]
BINOMIAL=PROJECT/'c-algorithms/binomial'
CURRENT=json.loads((BINOMIAL/'current-dataset.json').read_text())
SOURCE=(BINOMIAL/CURRENT['input_manifest']).parent
PARENT=PROJECT/'c-algorithms/binomial/build/adapter-001/dataset'
DESCRIPTOR=PROJECT/'c-algorithms/binomial/comparison-groups.json'


@unittest.skipUnless(SOURCE.is_dir(),'optional C dataset not bundled')
class CurrentCIntegrationTests(unittest.TestCase):
    def test_comparison_denominators_do_not_supply_outcomes(self):
        manifest,targets,_=inventory(SOURCE)
        groups=comparison_groups(manifest,targets,DESCRIPTOR)
        self.assertEqual(groups['counts'],{'main':78,'fixed_helper':13})
        self.assertEqual(len(groups['targets']),91)
        self.assertNotIn('0054',{t['target_id'] for t in groups['targets']})
        self.assertFalse(groups['expected_outcomes_supplied'])
        self.assertEqual({x['contract_id'] for x in groups['targets']},
            {x['contract_id'] for x in contracts_for(targets)})

    def test_group_drift_and_disabled_or_duplicate_helper_rejected(self):
        manifest,targets,_=inventory(SOURCE)
        value=json.loads(DESCRIPTOR.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'groups.json'
            for change in ({'dataset_binding':'wrong'}, {'fixed_helper_target_ids':['0054']},
                           {'fixed_helper_target_ids':['0089','0089']}):
                p.write_text(json.dumps({**value,**change}))
                with self.assertRaises(ValueError):comparison_groups(manifest,targets,p)

    def test_resealed_semantic_ml_is_rejected(self):
        import hashlib
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'input';shutil.copytree(SOURCE,root)
            p=root/'isabelle/Repair_Phase_Model.thy'
            p.write_text(p.read_text().replace('\nend','\nML \\<open>val probe = 0\\<close>\nend'))
            m=json.loads((root/'manifest.json').read_text())
            files={str(p.relative_to(root)):digest(p) for p in root.rglob('*')
                   if p.is_file() and p.name!='manifest.json'}
            m['input_files_sha256']=files
            m['dataset_binding']=hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'non-definition'):inventory(root)

    @unittest.skipUnless(PARENT.is_dir(),'optional native parent not bundled')
    def test_sealed_semantic_source_edit_and_added_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);semantic=root/'semantic';semantic.mkdir()
            source=semantic/'Test.thy';source.write_text('theory Test imports Main begin end\n')
            c=json.loads((PARENT/'checker-context.json').read_text())
            c['paths']={k:str((PARENT/v).resolve()) for k,v in c['paths'].items()}
            c['paths']['semantic_workspace']=str(semantic)
            c['premise_database']=str(PARENT/'parent-premises.sqlite')
            c['premise_audit_context']=str(PARENT/'premise-audit-context.json')
            c.pop('checker_context_id')
            c['semantic_files_sha256']={'Test.thy':digest(source)}
            c['checker_context_id']='checker-context:'+sha256_json(c)[:20]
            (root/'checker-context.json').write_text(json.dumps(c))
            load_context(root)
            source.write_text(source.read_text()+'(* changed *)')
            with self.assertRaisesRegex(ValueError,'semantic source binding'):load_context(root)
            source.write_text('theory Test imports Main begin end\n')
            (semantic/'Extra.thy').write_text('unexpected')
            with self.assertRaisesRegex(ValueError,'semantic source binding'):load_context(root)

    def test_baseline_rejects_mismatched_prepared_targets_before_deployment(self):
        spec=importlib.util.spec_from_file_location('current_c_deploy',PROJECT/'c-algorithms/binomial/tools/official_codex_deploy.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        manifest,targets,_=inventory(SOURCE)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'preparation.json').write_text(json.dumps({'status':'prepared','dataset_binding':manifest['dataset_binding']}))
            projection={'contracts':contracts_for(targets)}
            projection['contracts']=projection['contracts'][:-1]
            (root/'repository-planning-input.json').write_text(json.dumps(projection))
            with patch('regen.preparation.checker_context.load_context',return_value={
                    'result_scope':manifest['result_scope'],'comparison_groups_sha256':'bound'}):
                with self.assertRaisesRegex(ValueError,'do not match'):module.resolve_sources(SOURCE,root)

    def test_validation_only_refuses_start_before_credentials_or_subprocess(self):
        spec=importlib.util.spec_from_file_location('current_c_deploy',PROJECT/'c-algorithms/binomial/tools/official_codex_deploy.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            module.ROOT=Path(tmp);(module.ROOT/'control').mkdir()
            (module.ROOT/'control/config.json').write_text('{"validation_only":true}')
            with patch.object(module,'call') as call:
                with self.assertRaisesRegex(ValueError,'cannot start'):module.start()
                call.assert_not_called()

    def test_new_vero_prompt_uses_the_benchmark_owned_context_scope(self):
        spec=importlib.util.spec_from_file_location('current_c_deploy',PROJECT/'c-algorithms/binomial/tools/official_codex_deploy.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        text=module.task_text('PROMPT.txt',90,'vero')
        self.assertNotIn('strict refutation',text)
        self.assertIn('checked refutation in its original owned context',text)
        self.assertIn('VERO-style',text)
