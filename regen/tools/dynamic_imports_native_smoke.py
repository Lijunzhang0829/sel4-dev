"""Model-free dynamic-import lifecycle through real document tools and Isabelle.

Only fixture construction and model-origin eligibility are substituted. The
checker, provider preparation, import transaction, journal validation and seal
remain production implementations. All authored statements are synthetic HOL
tautologies; no benchmark proof or removed source is read.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch
from uuid import uuid4

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.proof_commits import digest, durable
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.execution.repository_execution import journal, progress
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.parallel_file_planner import TheoryController
from regen.preparation.checker_context import load_context


PROVIDER = 'Dynamic_Import_Provider'
CONSUMER = 'Dynamic_Import_Consumer'
DEPENDENT = 'Dynamic_Import_Dependent'
MODEL = 'synthetic-import-validation-no-model'


def require(value, message):
    if not value:
        raise AssertionError(message)


def create_fixture(root, dataset, context):
    """A new isolated run with generic theories and no accepted history."""
    packages = []
    for theory, declarations in (
            (PROVIDER, [('provided_truth', 'True')]),
            (CONSUMER, [('stable_truth', 'True'), ('consumer_truth', 'True & True')]),
            (DEPENDENT, [('dependent_truth', 'True')])):
        contracts = [{'contract_id': name, 'name': name,
                      'exact_statement': f'lemma {name}: "{proposition}"',
                      'contract_owned_context': []} for name, proposition in declarations]
        package = {'theory': theory, 'file_slot': theory + '.thy',
                   'contracts': contracts, 'support_contracts': [],
                   'repository_projection': {'schema_version': 6, 'theory': theory,
                       'required_imports': [], 'required_providers': [],
                       'auxiliary_intents': [], 'contracts': contracts,
                       'contract_ids': [c['contract_id'] for c in contracts]}}
        package['package_payload_sha256'] = digest(package)
        packages.append(package)
    files = [{'theory': p['theory'], 'file_slot': p['file_slot'],
              'order': index, 'layer': 0, 'status': 'pending', 'generation_ids': [],
              'seal_id': None, 'contract_ids': [c['contract_id'] for c in p['contracts']]}
             for index, p in enumerate(packages)]
    listing = {'schema_version': 7, 'files': files}
    repository = Path(__file__).resolve().parents[2]
    implementation = {str(path.relative_to(repository)):
                      hashlib.sha256(path.read_bytes()).hexdigest()
                      for folder in ('execution', 'planning', 'infrastructure', 'analysis')
                      for path in (repository/'regen'/folder).iterdir()
                      if path.suffix in {'.py', '.ML', '.sh'}}
    run = {'schema_version': 2, 'execution_mode': 'repository-projection-v1',
           'execution_input_kind': 'refine-dataset',
           'run_id': 'synthetic-dynamic-imports-' + uuid4().hex,
           'target': 'Synthetic_Dynamic_Imports', 'state': 'ready',
           'checker_context_root': str(dataset),
           'checker_context_id': context['checker_context_id'],
           'execution_models': {'File Planner': MODEL},
           'contract_package_dir': str(root/'packages'),
           'session_progress_ledger': str(root/'session-progress-ledger.json'),
           'session_schedule': {'file_list_sha256': digest(listing),
                                'plan_id': 'synthetic-dynamic-imports-fixture'},
           'implementation': implementation, 'validation_only': True,
           'model_calls': 0, 'checker_policy': 'fixed-prefix-cold-green-v1',
           'premise_database': str(context['premise_database_path']),
           'premise_snapshot': str(context['premise_audit_context_path']),
           'semantic_workspace': str(context['semantic_workspace']),
           'l4v': str(context['l4v']), 'base_session': context['base_session'],
           'base_import': context['base_import'], 'base_catalog': context['base_catalog'],
           'isabelle_dir': str(context['isabelle']), 'image': context['image'],
           'isabelle_sha256': context['isabelle_sha256'], 'budgets': context['budgets']}
    durable(root/'run.json', run, immutable=True)
    durable(root/'execution-input.json', {'file_list': listing, 'files': files,
        'package_hashes': {p['file_slot']: p['package_payload_sha256'] for p in packages}}, immutable=True)
    for package in packages:
        durable(root/'packages'/(package['file_slot'] + '.json'), package, immutable=True)
    durable(root/'heap-copy-budget.json', clone_heaps(context['heaps'], root/'heaps'), immutable=True)
    (root/'generated').mkdir()
    progress(root)
    return run, {p['theory']: p for p in packages}


class ScriptedProvenance:
    """Explicitly synthetic, hash-bound origin; never a model attestation."""
    def __init__(self, root):
        self.root = root
        self.records = {}

    @staticmethod
    def path(prefix):
        return Path(str(prefix) + '.synthetic-origin.json')

    def record(self, prefix, prompt, proposal, metadata, *, theory, model):
        path = self.path(prefix)
        require(path.resolve().is_relative_to(self.root), 'foreign provenance path')
        require(theory in {PROVIDER, CONSUMER, DEPENDENT} and model == MODEL, 'foreign synthetic identity')
        value = {'theory': theory, 'model': model, 'proposal': deepcopy(proposal),
                 'metadata': deepcopy(metadata), 'context_snapshot': prompt,
                 'real_model': False, 'model_calls': 0,
                 'eligibility_override': 'synthetic-native-smoke-only'}
        fingerprint = digest(value)
        durable(path, {**value, 'sha256': fingerprint}, immutable=True)
        self.records[str(path)] = fingerprint
        return json.dumps(proposal)

    def validate(self, prefix, *, require_real=True):
        path = self.path(prefix)
        value = json.loads(path.read_text())
        fingerprint = value.pop('sha256')
        require(fingerprint == digest(value) == self.records.get(str(path)),
                'synthetic provenance mismatch')
        require(value['real_model'] is False and value['model'] == MODEL,
                'synthetic action claimed model authority')
        return {'sealable_generation': True, 'real_model': False, 'model_calls': 0,
                'eligibility_override': value['eligibility_override']}


def run(dataset, output, *, timeout=180, candidate_timeout=60):
    require(timeout > 0 and candidate_timeout > 0, 'positive timeouts required')
    root, dataset = Path(output).resolve(), Path(dataset).resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'pass': False, 'model_calls': 0, 'benchmark_contracts_proved': 0,
              'scope': 'synthetic cross-theory import lifecycle; not a benchmark run',
              'substitutions': ['synthetic fixed-contract fixture',
                  'hash-bound synthetic action eligibility and no-model transcript marker'],
              'unmodified_authorities': ['TheoryController', 'runtime import transactions',
                  'IsabelleChecker', 'ProofCommits', 'document validation', 'seal validation'],
              'cases': []}
    resources = executor = None
    controllers = []

    def case(name, **values):
        report['cases'].append({'name': name, 'pass': True, **values})
        durable(root/'report.json', report)
        print(json.dumps({'case': name, 'pass': True}), flush=True)

    try:
        context = load_context(dataset)
        report['checker_context_id'] = context['checker_context_id']
        run_config, packages = create_fixture(root, dataset, context)
        resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
            pids_limit=1024, provider_build_timeout=600, build_ml_heap_mb=2048)
        executor = CheckExecutor(root/'check-tasks', workers=1)
        provenance = ScriptedProvenance(root)
        with ExitStack() as stack:
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',
                                      side_effect=provenance.record))
            stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact',
                                      side_effect=provenance.validate))
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.audit_workspace_session',
                return_value={'synthetic': True, 'real_model': False, 'model_calls': 0}))
            stack.enter_context(patch('regen.planning.batch_file_planner.call_llm',
                                      side_effect=AssertionError('model invocation forbidden')))
            serial = 0

            def controller(theory):
                value = TheoryController(root, theory, resources=resources, executor=executor,
                    document_mode=True, model=MODEL, check_timeout=timeout,
                    candidate_timeout=candidate_timeout)
                controllers.append(value)
                return value

            def action(agent, proposal):
                nonlocal serial
                serial += 1
                metadata = {'thread_id': 'synthetic', 'turn_id': 'synthetic',
                    'call_id': str(serial), 'synthetic': True,
                    'transcript_path': str(root/'synthetic-transcript.json'), 'transcript_offset': 1}
                durable(root/'tool-calls'/f'{serial:03d}-request.json',
                    {'theory': agent.theory, 'proposal': proposal, 'metadata': metadata}, immutable=True)
                result = agent.handle('proof_action', {'theory': agent.theory, 'proposal': proposal}, metadata)
                durable(root/'tool-calls'/f'{serial:03d}-result.json', result, immutable=True)
                return result

            def read(agent):
                return agent.handle('read_theory', {'theory': agent.theory}, {})['document']

            def edit(agent, *, imports=None, replace=None):
                draft = read(agent)
                if imports is not None:
                    saved = action(agent, {'action': 'edit_theory',
                        'base_sha256': draft['source_sha256'], 'imports': imports})
                    if replace is None:
                        return saved
                    draft = read(agent)
                if replace is not None:
                    return action(agent, {'action': 'edit_theory',
                        'base_sha256': draft['source_sha256'],
                        'edits': [{'old': replace[0], 'new': replace[1]}]})
                raise ValueError('fixture edit needs imports or a proof replacement')

            def state(theory):
                return journal(root, run_config, packages[theory]).replay(repair=False)

            provider = controller(PROVIDER)
            saved = edit(provider, replace=('\noops\n', '\nby simp\n'))
            result = action(provider, {'action': 'finish', 'source_sha256': saved['source_sha256']})
            require(state(PROVIDER)['status'] == 'sealed', 'provider finish did not check and seal draft')
            case('native_provider_finish_checks_unaccepted_draft', seal_id=state(PROVIDER)['seal_id'])
            provider.close()
            controllers.remove(provider)

            consumer = controller(CONSUMER)
            original = read(consumer)
            base_imports = list(consumer._runtime()[4].imports())
            imported = ['Generated_Refine.' + generated_checkpoint_theory(packages[PROVIDER]['file_slot'])]
            before_tasks = len(executor.records)
            edit(consumer, imports=imported)
            edit(consumer, imports=base_imports)
            require(len(executor.records) == before_tasks, 'draft import edits started native work')
            require(consumer._runtime()[4].imports() == base_imports, 'draft imports changed live checker')
            require(read(consumer)['source'] == original['source'], 'cancelled edits changed final source')
            case('draft_add_remove_coalesces_without_native_switch')

            stable_slot = 'lemma stable_truth: "True"\n(* REGEN-PROOF *)\noops'
            fact = generated_checkpoint_theory(packages[PROVIDER]['file_slot']) + '.provided_truth'
            saved = edit(consumer, imports=imported,
                replace=(stable_slot, stable_slot.removesuffix('oops') + 'using ' + fact + ' by simp'))
            require(state(CONSUMER) is None, 'draft edits initialized a fresh theory journal')
            result = action(consumer, {'action': 'check_theory', 'source_sha256': saved['source_sha256']})
            require('stable_truth' in state(CONSUMER)['proofs'], 'first check could not use the new import')
            commits = [json.loads(path.read_text()) for path in
                       sorted((root/'theories'/CONSUMER/'acceptance/commits').glob('*.json'))]
            context_events = [row['event'] for row in commits
                              if row['event'] in {'initial', 'imports', 'document'}]
            require(context_events == ['initial', 'imports', 'document'],
                    'first native action did not certify old scaffold before changing imports')
            require(state(CONSUMER).get('batch_requests') == 1,
                    'initializing imports charged an extra planner request')
            require(consumer._runtime()[4].imports() == imported,
                    'first native action did not apply draft imports')
            case('first_native_action_initializes_then_imports_and_checks',
                 context_events=context_events, planner_requests=1)

            # Establish a checked local proof in the base environment before
            # testing the original add/remove lifecycle against existing GREEN.
            saved = edit(consumer, imports=base_imports,
                replace=('using ' + fact + ' by simp', 'by simp'))
            result = action(consumer, {'action': 'check_theory', 'source_sha256': saved['source_sha256']})
            require('stable_truth' in state(CONSUMER)['proofs'], 'repaired local proof not accepted')
            require(consumer._runtime()[4].imports() == base_imports,
                    'removing unused provider did not restore base environment')
            case('native_remove_unused_import_rechecks_repaired_draft')
            old_state = deepcopy(state(CONSUMER))
            saved = edit(consumer, imports=imported,
                replace=('\noops\n', '\nusing ' + fact + ' by simp\n'))
            require(state(CONSUMER)['proofs'] == old_state['proofs'], 'draft edit rewrote acceptance history')
            result = action(consumer, {'action': 'check_theory', 'source_sha256': saved['source_sha256']})
            current = state(CONSUMER)
            require(set(current['proofs']) == {'stable_truth', 'consumer_truth'},
                    'imported fact was not usable in current context')
            require(consumer._runtime()[4].imports() == imported, 'checker did not apply accumulated imports')
            commits = [json.loads(path.read_text()) for path in
                       sorted((root/'theories'/CONSUMER/'acceptance/commits').glob('*.json'))]
            latest_document = next(row for row in reversed(commits) if row['event'] == 'document')
            require(latest_document['evidence'].get('document_check_mode') == 'full',
                    'changed imports reused old accepted prefix')
            case('native_add_import_rechecks_old_proof_and_uses_provider',
                 check_mode=latest_document['evidence']['document_check_mode'])

            saved = edit(consumer, imports=base_imports)
            result = action(consumer, {'action': 'check_theory', 'source_sha256': saved['source_sha256']})
            require('consumer_truth' not in state(CONSUMER)['proofs'],
                    'removed provider retained stale consumer GREEN')
            require(state(CONSUMER)['status'] != 'sealed', 'failed removal published a seal')
            require('Undefined fact' in json.dumps(result), 'negative check lacked missing-fact evidence')
            require(fact in read(consumer)['source'], 'failed check erased proof draft')
            case('native_remove_import_fails_without_reusing_green', feedback=result.get('feedback'))

            saved = edit(consumer, replace=('using ' + fact + ' by simp', 'by simp'))
            result = action(consumer, {'action': 'finish', 'source_sha256': saved['source_sha256']})
            require(state(CONSUMER)['status'] == 'sealed', 'repaired draft did not seal')
            require(consumer._runtime()[4].imports() == base_imports, 'seal retained removed import')
            case('file_planner_repairs_then_finish_rechecks_and_seals', seal_id=state(CONSUMER)['seal_id'])
            try:
                edit(consumer, imports=imported)
            except ValueError as error:
                case('sealed_theory_imports_are_immutable', diagnostic=str(error))
            else:
                raise AssertionError('sealed theory accepted import edit')
            consumer.close()
            controllers.remove(consumer)

            dependent = controller(DEPENDENT)
            saved = edit(dependent, imports=imported,
                replace=('\noops\n', '\nusing ' + fact + ' by simp\n'))
            require(state(DEPENDENT) is None, 'fresh dependent journal initialized before native finish')
            result = action(dependent, {'action': 'finish', 'source_sha256': saved['source_sha256']})
            require(state(DEPENDENT)['status'] == 'sealed',
                    'first native finish could not initialize, import and seal the complete draft')
            require(state(DEPENDENT).get('batch_requests') == 1,
                    'first finish charged extra planner requests for initialization')
            require(dependent._runtime()[4].imports() == imported,
                    'dependent seal omitted its dynamically added provider')
            case('first_native_finish_initializes_imports_and_seals',
                 seal_id=state(DEPENDENT)['seal_id'], planner_requests=1)
            dependent.close()
            controllers.remove(dependent)

            from regen.execution.repository_integration import (
                integration_manifest, integrate_repository, current_verification)
            _, manifest, _ = integration_manifest(root)
            entries = {row['theory']: row for row in manifest['theories']}
            require(entries[CONSUMER]['required_imports'] == [],
                    'integration manifest retained removed consumer imports')
            require(entries[DEPENDENT]['required_imports'] == [PROVIDER],
                    'integration manifest omitted the dynamically added provider')
            order = [row['theory'] for row in manifest['theories']]
            require(order.index(PROVIDER) < order.index(DEPENDENT),
                    'integration order does not respect the effective import relation')
            durable(root/'effective-integration-manifest.json', manifest, immutable=True)
            marker = CURRENT_RESOURCES.set(resources)
            try:
                integrated = integrate_repository(root, timeout=timeout)
            finally:
                CURRENT_RESOURCES.reset(marker)
            require(integrated.get('repository_verified') and current_verification(root),
                    'native integration did not validate the final synthetic repository: ' + json.dumps(integrated))
            case('native_integration_uses_final_effective_imports',
                 theory_order=order, contract_count=integrated['contract_count'],
                 effective_imports={name: row['required_imports'] for name, row in entries.items()},
                 artifact_dir=integrated['artifact_dir'])
            report['pass'] = True
    except BaseException as error:
        report['failure'] = {'type': type(error).__name__, 'diagnostic': str(error)}
        raise
    finally:
        for controller in reversed(controllers):
            controller.close()
        if executor is not None:
            executor.close()
        if resources is not None:
            report['native_operations'] = deepcopy(resources.metrics)
            resources.close()
        report['wall_seconds'] = time.monotonic() - started
        durable(root/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--candidate-timeout', type=int, default=60)
    args = parser.parse_args()
    result = run(args.dataset, args.output, timeout=args.timeout,
                 candidate_timeout=args.candidate_timeout)
    print(json.dumps({'pass': result['pass'], 'wall_seconds': result['wall_seconds']}))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
