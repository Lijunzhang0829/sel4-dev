"""Real isolated worker retention test using a saved accepted prefix, no model."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import threading
from copy import copy

from regen.execution.batch_checker import BatchWorker, body
from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.proof_commits import durable
from regen.execution.repository_execution import checker_for
from regen.infrastructure.resource_budget import clone_heaps


def run(output, *, quick=False):
    root = Path.cwd()
    source = root/'reports/regen/refine-astra-medium-20261005-006/proof'
    prior = root/'reports/regen/provider-bootstrap-repair-20261005/native-validation/Untyped_Object_Lifecycle'
    saved = next(source.glob('check-tasks/5b0c922*/isabelle/regen-batch-*/prefix')).parent
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prior/'generated', output/'generated')
    clone_heaps(prior/'heaps', output/'heaps')
    cfg = json.loads((source/'run.json').read_text())
    ancestors = json.loads((source/'theories/Untyped_Object_Lifecycle/imports.json').read_text())
    checker = checker_for(output, cfg, output/'isabelle', 900, ancestors, heaps_dir=output/'heaps')
    imports = (saved/'imports').read_text().splitlines()
    assert checker.imports() == imports
    label = (saved/'theory_name').read_text()
    prefix = (saved/'prefix').read_text()
    names = (saved/'prefix_facts').read_text().splitlines()
    if quick:
        prefix, names = 'unbundle l4v_word_context\n', []
    def source_text(name, content):
        return '\n'.join(['theory '+name, 'imports', *[json.dumps(i) for i in imports], 'begin', content, 'end', ''])
    text = source_text(label, prefix)
    # body() includes a leading/trailing newline; use its exact bytes everywhere.
    report = {'model_calls':0, 'acceptance_writes':0, 'quick_control':quick, 'prefix_sha256':hashlib.sha256(prefix.encode()).hexdigest(),
              'source_prefix':str(saved/'prefix'), 'synthetic_controls_only':True, 'pass':False}
    resources = NativeResources(1,4,2)
    token = CURRENT_RESOURCES.set(resources)
    try:
        with resources.lease(checker):
            t=time.monotonic(); w=BatchWorker(checker,text,names,imports); checker._batch_worker=w
            report['initial_ready_seconds']=time.monotonic()-t
        # Old cold path: symbol lookup kills the worker, requiring prefix replay.
        cold_dir=output/'cold-symbol'; cold_dir.mkdir()
        symbol_source=source_text('ColdSymbol','ML \\<open>val _ = Syntax.read_term @{context} "id"\\<close>')
        t=time.monotonic(); cold=checker._check(symbol_source,'ColdSymbol')
        assert cold['verdict']=='green' and w.closed
        with resources.lease(checker):
            w=BatchWorker(checker,text,names,imports); checker._batch_worker=w
        report['cold_observation_plus_recovery_seconds']=time.monotonic()-t
        identity=w.name
        # GREEN is pending in this test only, not a repository acceptance.
        candidate=text.rsplit('\nend\n',1)[0]+'\nlemma regen_probe: "True" by simp\nend\n'
        with resources.lease(checker):
            assert w.check_batch(candidate,['regen_probe'])['verdict']=='green'
        pending=w.pending; base=w.base
        t=time.monotonic()
        task_checker=copy(checker)
        task_checker._batch_worker=None
        task_checker._observation_owner=checker
        task_checker._observation_lock=threading.RLock()
        task_checker._symbol_cache={}
        symbol=task_checker.resolve_symbols(['id'], [])
        declaration=source_text('ObserveDecl','lemma regen_unproved: "False"\noops')
        result=task_checker._check_observation(declaration,'ObserveDecl')
        assert result['verdict']=='green' and result['proofs_checked'] is False
        # Neither accepted-prefix helpers nor speculative facts may be visible.
        for name in [*names[:1],'regen_probe','regen_unproved']:
            probe=source_text('ObserveIsolation', 'ML \\<open>val _ = Global_Theory.get_thm @{theory} '+json.dumps(name)+'\\<close>')
            assert task_checker._check_observation(probe,'ObserveIsolation')['verdict']=='red'
        timeout_source=source_text('ObserveTimeout','ML \\<open>val _ = OS.Process.sleep (Time.fromReal 2.0)\\<close>')
        assert task_checker._check_observation(timeout_source,'ObserveTimeout',timeout=0.1)['returncode']==124
        task_checker.close_prefix()
        assert list(resources.residents)==[id(checker)]
        assert w.pending==pending and w.base==base and w.name==identity and not w.closed
        with resources.lease(checker):
            w.align(body(candidate))
            following=candidate.rsplit('\nend\n',1)[0]+'\nlemma regen_probe_next: "True" by (rule regen_probe)\nend\n'
            assert w.check_batch(following,['regen_probe_next'])['verdict']=='green'
        report['warm_observations_and_following_check_seconds']=time.monotonic()-t
        report['same_worker_after_queries_and_checks']=w.name==identity and not w.closed
        report['symbol_status']=symbol['id']['status']
        report['pending_preserved']=True
        report['imports_only_isolation']=True
        report['pass']=True
    finally:
        resources.close()
        CURRENT_RESOURCES.reset(token)
        durable(output/'report.json',report,immutable=True)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--quick',action='store_true')
    args=parser.parse_args()
    run(args.output,quick=args.quick)
