"""Repository-planning log checks; mock native preflight is explicitly labelled."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from regen.execution.proof_commits import durable
from regen.infrastructure.incremental_log import enable, read_artifact
from regen.planning.workspace_harness import WorkspaceHarness
from regen.tests.current.test_workspace_planning import WorkspacePlanningTest


def run(output, plan):
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    fixture=WorkspacePlanningTest();fixture.setUp()
    checks={}
    try:
        root=fixture.root/'new-planning';enable(root)
        h=WorkspaceHarness(fixture.input,root,fixture.native);h.active_call=1
        h.dispatch({'action':'inspect','values':['p1']})
        invalid=deepcopy(fixture.proposal);invalid['theories'][0]['contract_ids']=['missing']
        checks['invalid_plan_rejected']=not h.dispatch({'action':'validate','proposal':invalid})['valid']
        fixture.native.return_value={'valid':False,'diagnostic':'injected preflight failure'}
        checks['native_failure_not_submitted']=not h.dispatch({'action':'submit','proposal':fixture.proposal})['valid'] and h.submitted is None
        fixture.native.return_value={'valid':True,'proved_contracts':0}
        checks['successful_submit']=h.dispatch({'action':'submit','proposal':fixture.proposal})['submitted']
        canonical,bundle=h.artifacts()
        restored=WorkspaceHarness(fixture.input,root,fixture.native)
        checks['restart_preserves_plan_and_allocation']=restored.artifacts()==(canonical,bundle)
        checks['query_objects_decode']=all(isinstance(read_artifact(p),dict) for p in (root/'queries').glob('*.json'))
        (root/'control/state.json').unlink()
        checks['missing_projection_rebuilt']=WorkspaceHarness(fixture.input,root,fixture.native).artifacts()==(canonical,bundle)
        # Explicitly record gaps rather than silently treating detection as recovery.
        (root/'control/state.json').write_text('{broken')
        try:
            WorkspaceHarness(fixture.input,root,fixture.native)
            checks['corrupt_projection_rebuilt']=True
        except json.JSONDecodeError:
            checks['corrupt_projection_rebuilt']=False
        (root/'control/state.json').unlink()
        last=max(p for p in (root/'queries').glob('*.json') if '.started.' not in p.name)
        final=last.read_bytes();last.unlink()
        try:
            restored=WorkspaceHarness(fixture.input,root,fixture.native)
            checks['missing_completed_record_detected']=False
            checks['missing_completed_record_observation']={'interrupted_queries':restored.state.get('interrupted_queries'),
                                                          'submitted_still_present':restored.submitted is not None}
        except ValueError:
            checks['missing_completed_record_detected']=True
        last.write_bytes(final)
    finally:fixture.doCleanups()
    # Real previous repository grouping, without model or native calls.
    listing=json.loads((plan/'file-generation-list.json').read_text())
    ids=[]
    for entry in listing['files']:
        projection=json.loads((plan/entry['projection']).read_text())
        ids.extend(c['contract_id'] for c in projection['contracts'])
    actual={'theories':len(listing['files']),'contracts':len(ids),
            'unique_contracts':len(set(ids)),'exact_once':len(ids)==len(set(ids))==listing['contract_count']}
    report={'checks':checks,'historical_plan':actual,'model_calls':0,'native_calls':0,
            'native_preflight':'mocked for protocol tests; not real Isabelle validation',
            'production_code_changed':False}
    durable(output/'report.json',report,immutable=True)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--plan',type=Path,required=True)
    a=p.parse_args();run(a.output,a.plan)
