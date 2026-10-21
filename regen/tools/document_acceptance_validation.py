"""Opt-in scripted document-tools acceptance against real Isabelle and journals.

The only substitutes are isolated fixture setup and explicitly scripted action
provenance. No model is contacted and no checker result or proof validator is
replaced. This is NOT real-model provenance, a seal, or repository completion.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import time
from unittest.mock import patch
from uuid import uuid4

from jsonschema import Draft202012Validator

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.repository_execution import journal
from regen.planning.parallel_file_planner import TheoryController, tools
from regen.preparation.checker_context import load_context


THEORY = 'Document_Acceptance'
ENTRY = 'validation_target'
MODEL = 'scripted-validation-no-model'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def readonly_heaps(command):
    return [value + ',readonly' if value.startswith('type=bind,')
            and value.endswith('dst=/root/.isabelle/heaps') else value
            for value in command]


def create_fixture(root, context):
    """One fresh target, no imported synthetic accepted history."""
    package = {'theory': THEORY, 'file_slot': THEORY + '.thy',
               'repository_projection': {'nodes': []}, 'contracts': [{
                   'contract_id': ENTRY, 'name': ENTRY,
                   'exact_statement': 'lemma validation_target: "(n::nat) = n"',
                   'contract_owned_context': []}]}
    package['package_payload_sha256'] = digest(package)
    listing = {'files': [{'theory': THEORY, 'file_slot': THEORY + '.thy',
                         'contract_ids': [ENTRY]}]}
    repository = Path(__file__).resolve().parents[2]
    implementation = {
        str(path.relative_to(repository)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ('execution', 'planning', 'infrastructure', 'analysis')
        for path in (repository/'regen'/folder).iterdir()
        if path.suffix in {'.py', '.ML', '.sh'}}
    run = {'run_id': 'scripted-document-validation-' + uuid4().hex,
           'execution_mode': 'repository-projection-v1',
           'execution_input_kind': 'refine-dataset',
           'execution_models': {'File Planner': MODEL},
           'contract_package_dir': str(root/'packages'),
           'implementation': implementation,
           'session_progress_ledger': str(root/'session-progress-ledger.json'),
           'session_schedule': {'file_list_sha256': digest(listing),
                                'plan_id': 'scripted-generic-fixture'},
           'validation_only': True, 'model_calls': 0,
           'checker_context_id': context['checker_context_id']}
    durable(root/'run.json', run, immutable=True)
    durable(root/'execution-input.json', {'file_list': listing, 'files': listing['files'],
            'package_hashes': {package['file_slot']: package['package_payload_sha256']}}, immutable=True)
    durable(root/'packages'/(package['file_slot'] + '.json'), package, immutable=True)
    (root/'generated').mkdir()
    return run, package


class ScriptedProvenance:
    """Hash-bound local inputs, explicitly not a Codex/model attestation.

    Production acceptance requires an audited real provider. This test replaces
    ONLY that eligibility gate so fixed generic scripts may traverse the actual
    document acceptance validator. The override is recorded in every receipt and
    the top-level report, and must never be used for benchmark generations.
    """
    def __init__(self, root):
        self.root = root.resolve()
        self.registry = {}

    @staticmethod
    def path(prefix):
        return Path(str(prefix) + '.scripted-provenance.json')

    def record(self, prefix, prompt, proposal, metadata, *, theory, model):
        path = self.path(prefix)
        require(path.resolve().is_relative_to(self.root), 'script receipt escapes test run')
        require(model == MODEL and theory == THEORY, 'foreign scripted identity')
        body = {'provider': 'local-scripted-validation', 'real_model': False,
                'provider_network_call': False, 'model_calls': 0,
                'theory': theory, 'model': model, 'proposal': deepcopy(proposal),
                'metadata': deepcopy(metadata), 'context_snapshot': prompt,
                'eligibility_override': 'isolated-test-only-not-model-attestation'}
        checksum = digest(body)
        durable(path, {**body, 'receipt_sha256': checksum}, immutable=True)
        self.registry[str(path)] = checksum
        return json.dumps(proposal)

    def validate(self, prefix, *, require_real=True):
        path = self.path(prefix)
        receipt = json.loads(path.read_text())
        checksum = receipt.pop('receipt_sha256')
        require(checksum == digest(receipt) == self.registry.get(str(path)),
                'scripted action receipt was changed')
        require(receipt['real_model'] is False and receipt['model'] == MODEL,
                'scripted receipt cannot claim real model provenance')
        return {'sealable_generation': True, 'provider_network_call': False,
                'provider': 'local-scripted-validation', 'real_model': False,
                'eligibility_override': receipt['eligibility_override']}


def runtime_factory(root, run, package, context):
    def setup(requested_root, theory, timeout):
        require(Path(requested_root).resolve() == root and theory == THEORY,
                'fixture setup cannot operate on another run')
        directory = root/'theories'/THEORY
        directory.mkdir(parents=True, exist_ok=True)
        durable(directory/'imports.json', [], immutable=True)
        durable(directory/'sealed-premises.json', {'records': []}, immutable=True)
        checker = IsabelleChecker(root, directory/'isabelle',
            image=context['image'], architecture=context['architecture'], timeout=timeout,
            heaps_dir=context['heaps'], l4v_dir=context['l4v'],
            semantic_workspace=context['semantic_workspace'],
            base_session=context['base_session'], base_import=context['base_import'],
            base_catalog=context['base_catalog'], isabelle_dir=context['isabelle'],
            generated_dir=directory/'generated')
        original = checker._command
        checker._command = lambda *a, **kw: readonly_heaps(original(*a, **kw))
        checker.declaration_environment = digest(run)
        return run, package, directory, journal(root, run, package), checker, []
    return setup


def replay_uncached(root, run, package):
    actual = journal(root, run, package)
    # No shared verified-prefix cache: a new validator-bound instance replays all
    # this tiny fixture's records. Never point this helper at a production run.
    independent = ProofCommits(actual.directory, actual.binding, actual.validate)
    return independent.replay(repair=False)


def oracle_fact_audit_ml():
    # `isabelle process -T /absolute/file` loads a non-session theory in the
    # Draft namespace. This is independent of the source header's short name.
    return ('val thms = Global_Theory.get_thms (Thy_Info.get_theory "Draft.' + THEORY + '") "' + ENTRY + '"; '
            'val _ = Thm.consolidate thms; '
            'val _ = if null thms orelse Thm_Deps.has_skip_proof thms orelse '
            'not (null (Thm_Deps.all_oracles thms)) then error "unclean oracle fact" else (); '
            'val _ = writeln "DOCUMENT_VALIDATION_ORACLE_CLEAN";')


def cold_oracle(root, context, source, timeout):
    """Fresh process checks exact submitted document, not the harness renderer."""
    directory = root/'cold-oracle'
    directory.mkdir()
    source_path = directory/(THEORY + '.thy')
    source_path.write_text(source)
    name = 'regen-document-oracle-' + uuid4().hex[:16]
    command = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
        '--mount', f'type=bind,src={context["l4v"]},dst=/sel4-project/verification/l4v,readonly',
        '--mount', f'type=bind,src={context["isabelle"]},dst=/workspace/verification/isabelle,readonly',
        '--mount', f'type=bind,src={context["semantic_workspace"]},dst=/semantic-base,readonly',
        '--mount', f'type=bind,src={context["heaps"]},dst=/root/.isabelle/heaps,readonly',
        '--mount', f'type=bind,src={directory},dst=/oracle,readonly',
        '-e', 'L4V_ARCH=' + context['architecture'], context['image'],
        '/workspace/verification/isabelle/bin/isabelle', 'process', '-o', 'threads=2',
        '-l', context['base_session']]
    from regen.execution.isabelle_node_checker import SESSION_CATALOGS
    for catalog in SESSION_CATALOGS:
        command.extend(['-d', '/sel4-project/verification/l4v/' + catalog])
    command.extend(['-d', context['base_catalog'], '-T', '/oracle/' + THEORY])
    # An explicit post-load theorem lookup/consolidation is a second oracle, not
    # a grep for a success label. This ML is test-owned, never model-provided.
    command.extend(['-e', oracle_fact_audit_ml()])
    durable(directory/'command.json', {'command': command, 'source_sha256':
            hashlib.sha256(source.encode()).hexdigest()}, immutable=True)
    started = time.monotonic()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        report = {'returncode': result.returncode, 'stdout': result.stdout,
                  'stderr': result.stderr, 'wall_seconds': time.monotonic()-started}
    except BaseException:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        raise
    durable(directory/'result.json', report, immutable=True)
    require(result.returncode == 0 and 'DOCUMENT_VALIDATION_ORACLE_CLEAN' in result.stdout,
            'fresh native exact-document oracle did not certify target')
    return {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'returncode': result.returncode, 'wall_seconds': report['wall_seconds'],
            'exact_document': True, 'new_process': True, 'worker_cache_used': False}


def run(dataset, output, *, timeout=180, candidate_timeout=60):
    require(timeout > 0 and candidate_timeout > 0, 'positive timeouts required')
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'scripted-document-native-acceptance', 'pass': False,
              'model_calls': 0, 'benchmark_contracts_proved': 0,
              'scope': 'generic fixture only; no seal, repository integration or real model audit',
              'substitutions': ['single-target fixture runtime setup with readonly parent heaps',
                  'hash-bound local scripted action provenance; provider eligibility gate is test-only'],
              'unmodified_authorities': ['IsabelleChecker/native ML execution',
                  'TheoryController', 'CheckExecutor', 'repository proof validator',
                  'document evidence validator', 'ProofCommits'], 'cases': []}
    script_bytes = Path(__file__).read_bytes()
    (root/'validation-source.py').write_bytes(script_bytes)
    report['validation_source_sha256'] = hashlib.sha256(script_bytes).hexdigest()
    resources = executor = controller = None
    def case(name, **evidence):
        report['cases'].append({'name': name, 'pass': True, **evidence})
        durable(root/'report.json', report)
    try:
        context = load_context(Path(dataset))
        report['checker_context_id'] = context['checker_context_id']
        report['resources'] = {'native_slots': 1, 'threads': 2, 'resident_limit': 1,
                               'parent_heaps_readonly': True, 'heap_copies': 0,
                               'check_timeout': timeout, 'candidate_timeout': candidate_timeout}
        run_config, package = create_fixture(root, context)
        scripted = ScriptedProvenance(root)
        resources = NativeResources(1, 2, 1)
        executor = CheckExecutor(root/'check-tasks', workers=1)
        schema = {t['name']: Draft202012Validator(t['inputSchema']) for t in tools(True)}
        serial = 0
        with ExitStack() as stack:
            stack.enter_context(patch('regen.execution.repository_execution.setup',
                side_effect=runtime_factory(root, run_config, package, context)))
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',
                                      side_effect=scripted.record))
            stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact',
                                      side_effect=scripted.validate))
            stack.enter_context(patch('regen.planning.batch_file_planner.call_llm',
                                      side_effect=AssertionError('model invocation forbidden')))
            controller = TheoryController(root, THEORY, resources=resources, executor=executor,
                document_mode=True, model=MODEL, check_timeout=timeout,
                candidate_timeout=candidate_timeout)
            def call(name, arguments):
                nonlocal serial
                schema[name].validate(arguments)
                serial += 1
                metadata = {'thread_id': 'scripted-thread', 'turn_id': 'scripted-turn',
                            'call_id': str(serial), 'scripted': True}
                durable(root/'tool-calls'/f'{serial:03d}-request.json',
                        {'tool': name, 'arguments': arguments, 'metadata': metadata}, immutable=True)
                try:
                    result = controller.handle(name, arguments, metadata)
                except BaseException as error:
                    durable(root/'tool-calls'/f'{serial:03d}-error.json',
                        {'error_type': type(error).__name__, 'diagnostic': str(error)}, immutable=True)
                    raise
                durable(root/'tool-calls'/f'{serial:03d}-result.json', result, immutable=True)
                return result
            def action(proposal):
                return call('proof_action', {'theory': THEORY, 'proposal': proposal})

            draft = call('read_theory', {'theory': THEORY})['document']
            bad_source = draft['source'].replace('\noops\n', '\nby (rule TrueI)\n')
            saved = action({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                            'source': bad_source})
            require(saved['checked'] is False, 'saving draft conferred proof authority')
            try:
                action({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                        'source': draft['source']})
            except ValueError as error:
                require('stale document' in str(error), 'wrong stale-edit failure')
                case('stale_edit_rejected', diagnostic=str(error))
            else:
                raise AssertionError('stale edit unexpectedly saved')

            stale = action({'action': 'check_theory', 'source_sha256': draft['source_sha256']})
            require(stale['feedback']['status'] == 'rejected_request', 'stale check was not rejected')
            require(not replay_uncached(root, run_config, package)['proofs'], 'stale check accepted a proof')
            case('stale_check_rejected', task_id=stale['task_id'])

            bad = action({'action': 'check_theory', 'source_sha256': saved['source_sha256']})
            require(bad['feedback']['status'] == 'rejected_batch', 'native invalid proof was not rejected')
            require(bad['feedback']['check']['returncode'] == 1, 'expected logical failure, not timeout')
            require(not bad['feedback']['check'].get('timed_out', False), 'negative control timed out')
            require('Failed to apply initial proof method' in bad['feedback']['check']['diagnostic'],
                    'negative control did not fail at the intended incorrect proof method')
            require(not replay_uncached(root, run_config, package)['proofs'], 'failed candidate became accepted')
            case('native_failure_not_accepted', task_id=bad['task_id'], request_number=bad['request_number'])

            good_source = bad_source.replace('by (rule TrueI)', 'by (rule refl)')
            good = action({'action': 'edit_theory', 'base_sha256': saved['source_sha256'],
                           'source': good_source})
            accepted = action({'action': 'check_theory', 'source_sha256': good['source_sha256']})
            require(accepted['feedback']['status'] == 'accepted', 'valid native proof was not accepted')
            state = replay_uncached(root, run_config, package)
            require(state['proofs'].get(ENTRY, '').strip() == 'by (rule refl)', 'wrong accepted proof')
            require(state['document_source_sha256'] == good['source_sha256'], 'accepted source mismatch')
            case('native_success_real_document_commit', task_id=accepted['task_id'],
                 request_number=accepted['request_number'], source_sha256=good['source_sha256'])

            controller.close()
            controller = TheoryController(root, THEORY, resources=resources, executor=executor,
                document_mode=True, model=MODEL, check_timeout=timeout,
                candidate_timeout=candidate_timeout)
            resumed = call('read_theory', {'theory': THEORY})
            require(resumed['accepted_ids'] == [ENTRY] and resumed['accepted_source_sha256'] == good['source_sha256'],
                    'new controller did not recover exact acceptance')
            require(replay_uncached(root, run_config, package)['proofs'] == state['proofs'],
                    'uncached full journal replay changed proofs')
            case('fresh_controller_and_uncached_journal_replay', accepted_ids=resumed['accepted_ids'])

            rows = [json.loads(path.read_text()) for path in sorted(
                (root/'theories'/THEORY/'acceptance/commits').glob('*.json'))]
            position = next(i for i, row in enumerate(rows) if row['event'] == 'document')
            accepted_record, preceding = rows[position], rows[position-1]['state']
            corrupted = deepcopy(accepted_record['evidence'])
            corrupted['document_source'] = corrupted['document_source'].replace('by (rule refl)', 'by (rule TrueI)')
            corrupted['document_source_sha256'] = hashlib.sha256(corrupted['document_source'].encode()).hexdigest()
            durable(root/'mutated-evidence.json', corrupted, immutable=True)
            try:
                journal(root, run_config, package).validate(preceding, accepted_record['state'], 'document', corrupted)
            except ValueError as error:
                case('rehashed_source_mutation_rejected', diagnostic=str(error),
                     accepted_journal_unchanged=True)
            else:
                raise AssertionError('rehashed source mutation was accepted')
            controller.close()
            controller = None
            oracle = cold_oracle(root, context, good_source, timeout)
            require(oracle['source_sha256'] == state['document_source_sha256'], 'oracle checked different source')
            case('independent_cold_exact_document_oracle', **oracle)
        report['pass'] = True
    except BaseException as error:
        report['failure'] = {'error_type': type(error).__name__, 'diagnostic': str(error)}
        raise
    finally:
        cleanup = []
        for value in (controller, executor, resources):
            if value is not None:
                try:
                    value.close()
                except BaseException as error:
                    cleanup.append({'error_type': type(error).__name__, 'diagnostic': str(error)})
        if cleanup:
            report['pass'] = False
            report['cleanup_errors'] = cleanup
        durable(root/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--candidate-timeout', type=int, default=60)
    args = parser.parse_args()
    result = run(args.dataset_root, args.output, timeout=args.timeout,
                 candidate_timeout=args.candidate_timeout)
    print(json.dumps({'pass': result['pass'], 'cases': result['cases']}, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
