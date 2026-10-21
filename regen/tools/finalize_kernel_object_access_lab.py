"""Cold-check and audit the exact exported theory name from the isolated lab."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable, freeze_evidence
from regen.planning.proof_graph import graph_payload_sha256, validate_graph
from regen.tools.proof_lab_journal import replay_lab


def write_text(path, text):
    with path.open("x") as stream:
        stream.write(text); stream.flush(); os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    lab, out = args.lab.resolve(), args.output.resolve()
    if out.exists():
        raise ValueError('use a fresh output directory; evidence is immutable')
    initial = json.loads((lab / 'initial.json').read_text())
    state = replay_lab(lab)
    graph, proofs = state['graph'], state['proofs']
    package = {'repository_projection': True, 'contracts': [
        {'contract_id': n['id'], 'name': n['name'],
         'exact_statement': n['statement'], 'contract_owned_context': n.get('context', [])}
        for n in initial['graph']['nodes'] if n['role'] == 'contract'
    ]}
    validate_graph(graph, package)
    if set(proofs) != {n['id'] for n in graph['nodes']}:
        raise ValueError('every active node must have an accepted proof')
    if any(re.search(r'\b(?:sorry|oops|axiomatization|oracle|skip_proof)\b', body)
           for body in proofs.values()):
        raise ValueError('untrusted proof command')
    graph['graph_payload_sha256'] = graph_payload_sha256(graph)
    source_run = Path(initial['source_run'])
    run = json.loads((source_run / 'run.json').read_text())

    def checker(work):
        return IsabelleChecker(
            source_run, work, heaps_dir=source_run / 'heaps',
            generated_dir=source_run / 'generated', l4v_dir=Path(run['l4v']),
            semantic_workspace=Path(run['semantic_workspace']),
            isabelle_dir=Path(run['isabelle_dir']), image=run['image'],
            base_session=run['base_session'], base_import=run['base_import'],
            base_catalog=run['base_catalog'], timeout=900)

    name = 'Kernel_Object_Access'
    whole = checker(out / 'whole-check')
    source = whole.complete_source(graph, proofs, theory_name=name)
    durable(out / 'input.json', {'initial': initial, 'state': state,
                               'source_sha256': hashlib.sha256(source.encode()).hexdigest()},
            immutable=True)
    check = freeze_evidence(whole._check(source, name))
    durable(out / 'whole-check.json', check, immutable=True)
    if check['verdict'] != 'green':
        raise RuntimeError('whole theory check failed; see whole-check.json')
    audit = freeze_evidence(checker(out / 'audit').audit_complete_theory(
        graph, proofs, theory_name=name))
    durable(out / 'audit.json', audit, immutable=True)
    if audit['verdict'] != 'green':
        raise RuntimeError('whole theory audit failed; see audit.json')
    dependencies = audit['complete_theory_audit']['proof_dependencies']
    if set(dependencies) != set(proofs):
        raise RuntimeError('audit does not cover every accepted proof')
    if any(item.get('has_skip_proof') is not False for item in dependencies.values()):
        raise RuntimeError('audit found skipped or unknown proof status')
    theory = out / (name + '.thy')
    write_text(theory, source)
    write_text(out / 'ROOT', 'session Generated_Kernel_Object_Access = Semantic_Base +\n'
            '  options [quick_and_dirty = false]\n  theories Kernel_Object_Access\n')
    durable(out / 'manifest.json', {
        'status': 'complete', 'setting': 'developer/LLM collaborative experiment',
        'theory': str(theory), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'fixed_contracts': package['contracts'], 'graph': graph,
        'proof_sha256': {key: hashlib.sha256(body.encode()).hexdigest()
                         for key, body in proofs.items()},
        'whole_check': str(out / 'whole-check.json'), 'audit': str(out / 'audit.json'),
        'source_run': str(source_run), 'session_build_performed': False,
    }, immutable=True)
    print(theory, flush=True)


if __name__ == '__main__':
    main()
