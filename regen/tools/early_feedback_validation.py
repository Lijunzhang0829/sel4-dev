"""Replay three frozen generated errors and measure disposable native syntax preflight.

No model calls or acceptance writes. This measures error latency, not proof discovery.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

from regen.execution.batch_checker import BatchWorker, body
from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.proof_commits import durable
from regen.execution.repository_execution import checker_for
from regen.infrastructure.resource_budget import clone_heaps

TASKS = (
    (6023, 'f8a2dd89eb2ae559bd650415174e4f3e16b5df009e969834b49de0161799560a', 'command expected'),
    (6053, '6e74fe540880034968ff9dd6a7894d204229d5ac11f50a09bccb45c8bc8ac724', 'Clash of types'),
    (6121, '80646556f5f207488d6a6755461cbea4286d8b0a9c434b7564b515b026e8ea21', 'natural number expected'),
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def load_cases(root):
    cases = []
    for line, task, signature in TASKS:
        result = json.loads((root/'check-tasks'/task/'result.json').read_text())
        check = result['feedback']['check']
        path = Path(check['source'])
        source = path.read_text()
        delta = path.with_suffix('.request').read_text()
        if sha(source) != check['source_sha256'] or sha(delta) != check['delta_sha256']:
            raise ValueError('saved candidate hash mismatch')
        content = body(source)
        if not content.endswith(delta):
            raise ValueError('candidate delta is not exact suffix')
        prefix = content[:-len(delta)]
        if sha(prefix) != check['prefix_sha256']:
            raise ValueError('saved prefix hash mismatch')
        names = [value.split(':')[1 if value.startswith('helper:') else 2]
                 for value in result['green_node_ids']]
        cases.append(dict(line=line, task=task, signature=signature, source=source,
            delta=delta, prefix=prefix, names=names, check=check,
            label=source.splitlines()[0].split()[1],
            imports=(path.parent/'imports').read_text().splitlines()))
    if len({sha(case['prefix']) for case in cases}) != 1:
        raise ValueError('comparison requires same frozen committed prefix')
    return cases


def theory(label, imports, text):
    return '\n'.join(['theory '+label, 'imports', *map(json.dumps, imports), 'begin', text, 'end', ''])


def preflight_ml(filename, line, label):
    # Only malformed parse transitions run; no valid candidate command is executed.
    return r'''ML \<open>
let
  val thy = @{theory};
  val text = File.read (Path.explode "/worker/''' + filename + r'''");
  val pos = Position.line_file ''' + str(line) + ' ' + json.dumps('/candidate/'+label+'.thy') + r''';
  val trs = Outer_Syntax.parse_text thy (fn () => thy) pos text;
  val malformed = filter (fn tr => Toplevel.name_of tr = "<malformed>") trs;
  val _ = List.app (fn tr => ignore (Toplevel.command_exception true tr
    (Toplevel.make_state (SOME thy)))) malformed;
in writeln "REGEN_SYNTAX_PREFLIGHT_CLEAR" end
\<close>'''


def summarize(report):
    rows=report['cases']
    if not report.get('pass_validation') or len(rows)!=3:
        raise ValueError('complete successful native replay required')
    baseline=sum(r['full']['wall_ms'] for r in rows)
    preflight=sum(r['preflight']['wall_ms'] for r in rows)
    fallback=sum(r['full']['wall_ms'] for r in rows if not r['same_error_detected_early'])
    return {'cases':len(rows),'early_same_error_hits':sum(r['same_error_detected_early'] for r in rows),
        'baseline_full_checks_ms':baseline,'all_preflight_ms':preflight,
        'fallback_full_checks_ms':fallback,'optimized_strategy_ms':preflight+fallback,
        'net_saved_ms':baseline-preflight-fallback,
        'prefix_preparation_seconds':report['prefix_preparation_seconds'],
        'measurement':'One frozen-candidate replay each; optimized aggregate includes preflight overhead for misses; no prefix savings or online success-rate claim'}


def run(output, prepare_only=False):
    run_started = time.monotonic()
    root = Path.cwd()
    source_root = root/'reports/regen/refine-astra-medium-20261005-006/proof'
    cases = load_cases(source_root)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'model':'gpt-6-astra', 'reasoning_effort':'medium', 'model_calls':0,
        'model_role':'supervising subagent; candidate replay is model-free',
        'production_resumed':False, 'acceptance_writes':0,
        'cases':[{'transcript_line':c['line'], 'task':c['task'],
                  'source_sha256':sha(c['source']), 'prefix_sha256':sha(c['prefix']),
                  'delta_sha256':sha(c['delta']), 'expected_error':c['signature']}
                 for c in cases]}
    durable(output/'manifest.json', manifest, immutable=True)
    for c in cases:
        (output/(str(c['line'])+'.thy')).write_text(c['source'])
    if prepare_only:
        return manifest
    prior = root/'reports/regen/provider-bootstrap-repair-20261005/native-validation/Scheduler_Dispatch'
    shutil.copytree(prior/'generated', output/'generated')
    clone_heaps(prior/'heaps', output/'heaps')
    cfg = json.loads((source_root/'run.json').read_text())
    ancestors = json.loads((source_root/'theories/Scheduler_Dispatch/imports.json').read_text())
    checker = checker_for(output, cfg, output/'isabelle', 900, ancestors, heaps_dir=output/'heaps')
    first = cases[0]
    if checker.imports() != first['imports']:
        raise ValueError('frozen provider imports mismatch')
    report = dict(manifest, pass_validation=False, cases=[])
    report['input_and_heap_preparation_seconds'] = time.monotonic()-run_started
    resources = NativeResources(1,4,1)
    token = CURRENT_RESOURCES.set(resources)
    worker = None
    try:
        with resources.lease(checker):
            start = time.monotonic()
            prefix_source = theory(first['label'], first['imports'], first['prefix'])
            # Preserve exact whitespace of body; render directly from original header.
            prefix_source = first['source'].split('\nbegin\n')[0]+'\nbegin\n'+first['prefix']+'\nend\n'
            worker = BatchWorker(checker, prefix_source, first['names'], first['imports'])
            checker._batch_worker = worker
            report['prefix_preparation_seconds'] = time.monotonic()-start
            base, pending, identity = worker.base, worker.pending, worker.name
            for case in cases:
                filename = 'preflight-'+str(case['line'])+'.txt'
                (worker.root/filename).write_text(case['delta'])
                offset = (case['source'].split('\nbegin\n')[0]+'\nbegin\n'+case['prefix']).count('\n')+1
                observation = theory('Preflight'+str(case['line']), first['imports'],
                    preflight_ml(filename, offset, first['label']))
                fast = worker.observe_source(observation, 'Preflight'+str(case['line']), timeout=30)
                assert (worker.base,worker.pending,worker.name) == (base,pending,identity)
                full = worker.check_batch(case['source'], case['check']['audited_facts'])
                assert full['verdict'] == 'red' and case['signature'] in full['diagnostic']
                assert worker.base == base and worker.pending is None
                row = {'transcript_line':case['line'], 'preflight':fast, 'full':full,
                       'same_error_detected_early':case['signature'] in fast['diagnostic'],
                       'milliseconds_saved_if_detected':full['wall_ms']-fast['wall_ms'] if fast['verdict']=='red' else None}
                report['cases'].append(row)
                durable(output/'progress.json',report)
            # Actual legal-prefix check confirms failed/preflight candidates export no fact.
            probe = prefix_source.rsplit('\nend\n',1)[0]+'\nlemma feedback_isolation_probe: "True" by (rule dispatch_tcb_layout_update)\nend\n'
            isolation = worker.check_batch(probe, ['feedback_isolation_probe'])
            report['isolation_check'] = isolation
            assert isolation['verdict']=='red' and 'Undefined fact' in isolation['diagnostic']
            report['same_worker_preserved'] = worker.name==identity and not worker.closed
            report['pass_validation'] = report['cases'][0]['same_error_detected_early'] and report['same_worker_preserved']
    except BaseException as error:
        report['failure'] = {'type':type(error).__name__, 'message':str(error)}
        raise
    finally:
        resources.close()
        CURRENT_RESOURCES.reset(token)
        report['worker_closed'] = worker.closed if worker else None
        report['total_wall_seconds'] = time.monotonic()-run_started
        report['limitations'] = ['Three fixed generated candidates, one warm timing each; no online proof-success claim.',
            'Imports-only syntax preflight does not elaborate proof facts/types or execute valid proof commands.',
            'Cold prefix preparation reported separately; no effective-generation saving claimed for it.']
        durable(output/'report.json',report,immutable=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--summarize-report',type=Path)
    args = parser.parse_args()
    if args.summarize_report:
        result=summarize(json.loads(args.summarize_report.read_text()))
        durable(args.output,result,immutable=True)
        print(json.dumps(result,indent=2))
    else:
        result = run(args.output,args.prepare_only)
        print(json.dumps({key:result.get(key) for key in ('pass_validation','production_resumed',
            'model_calls','prefix_preparation_seconds','total_wall_seconds','failure')},indent=2))
