"""Read-only comparison of retained legacy and optimized sealed Isabelle heaps.

Loads existing sessions; it neither rebuilds nor publishes a repository seal.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from regen.execution.proof_commits import durable
from regen.tools.arch_reuse_audit import snapshot, parse, CATEGORIES
from regen.tools.native_backend_acceptance import Limits
from regen.tools.replay_finish_step import sha


def probe_command(command, output):
    args = list(command)
    if args[:2] != ['docker', 'run'] or args.count('-T') != 1 or '--name' not in args:
        raise ValueError('expected a recorded, owned native theory check')
    if args[args.index('--network')+1] != 'none':
        raise ValueError('network must remain disabled')
    args[args.index('--name')+1] = 'regen-arch-heap-probe-'+uuid.uuid4().hex
    heap_mounts = 0
    for i, value in enumerate(args):
        if value.startswith('type=bind,'):
            if ',dst=/candidate,' in value:
                args[i] = f'type=bind,src={output},dst=/candidate,readonly'
            elif ',dst=/replay-heaps' in value:
                args[i] = value if value.endswith(',readonly') else value+',readonly'
                heap_mounts += 1
            elif not value.endswith(',readonly'):
                raise ValueError('unexpected writable input mount')
    if heap_mounts != 1:
        raise ValueError('expected exactly one sealed heap mount')
    args[args.index('-l')+1] = 'Sealed_Refine'
    args[args.index('-T')+1] = '/candidate/Compare_Sealed'
    return args


def run(baseline, optimized, output):
    baseline, optimized, output = (p.resolve() for p in (baseline, optimized, output))
    if output.exists() or any(output.is_relative_to(p) for p in (baseline, optimized)):
        raise ValueError('comparison needs a new, separate output directory')
    evidence, protected = {}, {}
    for label, root in (('baseline', baseline), ('optimized', optimized)):
        report = json.loads((root/'report.json').read_text())
        if (not all(report.get(k) is True for k in ('step_pass', 'protocol_pass',
                'frozen_inputs_unchanged', 'protected_unchanged', 'production_unchanged'))
                or report.get('guard_failure') is not None or report.get('cleanup_errors')
                or not all(r['confirmed_absent'] for r in report['cleanup'])):
            raise ValueError('input finish replay did not pass')
        evidence[label] = json.loads((root/'step-evidence.json').read_text())
        actual_source = root/'generated/sealed-session/Regen_Scheduler_Queues.thy'
        if sha(actual_source) != evidence[label]['source_sha256']:
            raise ValueError('sealed source differs from finish evidence')
        paths = [root/'report.json', root/'step-evidence.json', root/'complete-native-command.json']
        paths += [p for directory in ('build-heaps', 'generated') for p in (root/directory).rglob('*') if p.is_file()]
        protected.update({str(p): sha(p) for p in paths})
    a = evidence['baseline']['dependencies']['complete_theory_audit']
    b = evidence['optimized']['dependencies']['complete_theory_audit']
    nodes = set(a['proof_dependencies'])
    if nodes != set(b['proof_dependencies']):
        raise ValueError('node identity sets differ')
    # The saved graph supplies exact theorem names, not heuristic ID parsing.
    request = json.loads((optimized/'saved-tool-request.json').read_text())
    manifest = json.loads((optimized/'manifest.json').read_text())
    source_run = Path(manifest['source_run'])
    frozen_request = source_run/'theories/Scheduler_Queues/batch-requests/000121/input.json'
    protected[str(frozen_request)] = sha(frozen_request)
    graph = json.loads(frozen_request.read_text())['graph']
    if {n['id'] for n in graph['nodes']} != nodes or request['theory'] != 'Scheduler_Queues':
        raise ValueError('saved request does not match audit nodes')
    names = [n['name'] for n in graph['nodes']]
    text = ('theory Compare_Sealed\nimports "Sealed_Refine.Regen_Scheduler_Queues"\nbegin\n' +
            snapshot('export', global_context=True, targets=names)+'\nend\n')
    output.mkdir(parents=True)
    (output/'Compare_Sealed.thy').write_text(text)
    for module in (Path(__file__), Path(__file__).with_name('arch_reuse_audit.py')):
        shutil.copyfile(module, output/module.name)
    durable(output/'manifest.json', {'protected_sha256': protected, 'model_calls': 0,
        'experiment_resumed': False, 'seal_published': False, 'node_count':len(nodes),
        'probe_source_sha256': sha(output/'Compare_Sealed.thy'),
        'scope': 'Read-only loading of retained sealed heaps, no proof replay or timing claim'}, immutable=True)
    limits = Limits(output, 480, 512*1024**2)
    result = {'pass': False, 'trials': [], 'model_calls': 0, 'experiment_resumed': False}
    result['phase_comparison'] = {
        phase: {'baseline_ms':evidence['baseline'][key]['wall_ms'],
                'optimized_ms':evidence['optimized'][key]['wall_ms']}
        for phase, key in (('complete','complete'), ('audit','dependencies'), ('build','build'))}
    result['timing_notice'] = ('Historical full-step baseline, not contemporaneous ABBA. '
        'Heap loading below is an equivalence check, not part of these phase timings.')
    expected = {'export/'+c for c in CATEGORIES}
    expected.update('export/'+kind+n for n in names for kind in ('target_', 'deps_'))
    try:
        limits.check(); limits.monitor.start()
        for label, root in (('baseline', baseline), ('optimized', optimized)):
            command = probe_command(json.loads((root/'complete-native-command.json').read_text())['command'], output)
            limits.register(command[command.index('--name')+1])
            durable(output/(label+'-command.json'), command, immutable=True)
            started = time.monotonic()
            native = subprocess.run(command, capture_output=True, text=True, timeout=210)
            log = native.stdout+'\n'+native.stderr
            (output/(label+'.log')).write_text(log)
            row = {'label':label, 'returncode':native.returncode,
                   'wall_seconds':time.monotonic()-started, 'log_sha256':sha(output/(label+'.log'))}
            result['trials'].append(row)
            if native.returncode:
                raise RuntimeError('sealed heap probe failed: '+label)
            row['fingerprints'] = parse(log)
            if set(row['fingerprints']) != expected:
                raise RuntimeError('incomplete sealed export fingerprints')
        left, right = [t['fingerprints'] for t in result['trials']]
        result['export_differences'] = [k for k in sorted(expected) if left[k] != right[k]]
        def deps(row):
            return sorted((d['canonical_name'],d['fact_index']) for d in row['dependencies'])
        result['dependency_differences'] = [n for n in sorted(nodes)
            if deps(a['proof_dependencies'][n]) != deps(b['proof_dependencies'][n])]
        result['no_skip_proofs'] = all(r['has_skip_proof'] is False for data in (a,b)
            for r in data['proof_dependencies'].values())
        result['automation_context_equal'] = a['automation_context']['records'] == b['automation_context']['records']
        result['generated_adoption_equal'] = evidence['baseline']['impact']['generated_adoption'] == evidence['optimized']['impact']['generated_adoption']
        result['pass'] = (not result['export_differences'] and not result['dependency_differences']
            and result['no_skip_proofs'] and result['automation_context_equal'] and result['generated_adoption_equal'])
    except Exception as error:
        result['failure'] = {'type':type(error).__name__, 'message':str(error)}
    finally:
        limits.stop.set()
        if limits.monitor.ident is not None:
            limits.monitor.join(timeout=20)
        result['cleanup'] = limits.cleanup()
        result['guard_failure'] = limits.failure
        result['protected_unchanged'] = all(sha(Path(p))==v for p,v in protected.items())
        result['pass'] = (result['pass'] and result['guard_failure'] is None and result['protected_unchanged']
            and all(r['confirmed_absent'] for r in result['cleanup']))
        durable(output/'report.json', result, immutable=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'optimized', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    result = run(args.baseline, args.optimized, args.output)
    print({k:v for k,v in result.items() if k!='trials'})
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
