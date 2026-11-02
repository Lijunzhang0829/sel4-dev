"""Synthetic, zero-model proof-direction handoff checked by real Isabelle.

The three fixed contracts are candidate_true: True, candidate_false: False,
and consumer: True & True. Advice suggests both candidates, without making
either a premise or a mandatory route. A consumer using the unproved false
candidate must fail. Proving the true candidate and its consumer in one batch
must succeed while the false candidate remains an open, unchanged contract.

Only fixture construction and model-origin eligibility are substituted. The
production document controller, Isabelle checker and acceptance journal run
unchanged. This does not prove benchmark contracts or measure model quality.
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

from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal, progress, projection_package
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.import_contract import build_bundle_v6
from regen.planning.parallel_file_planner import TheoryController
from regen.preparation.checker_context import load_context


THEORY = 'Proof_Direction_Consumer'
MODEL = 'synthetic-proof-direction-validation-no-model'
CONTRACTS = [('candidate_true', 'True'), ('candidate_false', 'False'),
             ('consumer', 'True & True')]
SUGGESTIONS = [{
    'id': 'TryCandidateReuse',
    'target_contract_id': 'consumer',
    'direction': 'Try a useful candidate, or prove the target independently.',
    'candidate_premise_ids': [],
    'candidate_contract_ids': ['candidate_true', 'candidate_false'],
    'open_questions': ['Which candidates are proved and available in the current prefix?'],
}]


def require(value, message):
    if not value:
        raise AssertionError(message)


def create_fixture(root, dataset, context):
    contracts = [{'contract_id': name, 'name': name, 'kind': 'lemma',
                  'exact_statement': f'lemma {name}: "{proposition}"',
                  'contract_owned_context': [], 'contract_owned_attributes': []}
                 for name, proposition in CONTRACTS]
    payload = {'schema_version': 1, 'artifact_type': 'repository-planning-input',
        'benchmark': {'id': 'synthetic-proof-direction', 'target_session': 'Synthetic',
                      'cross_experiment_inputs': []},
        'planning_input_id': 'synthetic-proof-direction-input',
        'contract_visibility': 'owner-blind-v1', 'contracts': contracts,
        'contract_cards': [{k: c[k] for k in ('contract_id', 'name')} for c in contracts],
        'contract_relation_candidates': [],
        'context_profiles': [{'context_profile_id': 'SyntheticContext',
                              'generated_import': context['base_import']}],
        'parent_premises': {'database_sha256': 'synthetic-unused-parent'},
        'planner_interfaces': {}}
    proposal = {'schema_version': 6,
        'ordering_rationale': 'Keep these synthetic alternatives in one independent theory.',
        'contract_relations': [], 'unresolved_retrieval_questions': [],
        'collaboration_groups': [], 'reuse_suggestions': [],
        'proof_direction_suggestions': deepcopy(SUGGESTIONS),
        'theories': [{'name': THEORY, 'purpose': 'Check non-authoritative proof advice.',
            'grouping_reason': 'A local consumer may try either fixed candidate.',
            'contract_ids': [c['contract_id'] for c in contracts],
            'context_profile_ids': ['SyntheticContext'], 'helper_decisions': [],
            'required_imports': []}]}
    artifacts = build_bundle_v6(payload, proposal)
    plan = artifacts['repository-plan.json']
    projection = artifacts['files/' + THEORY + '.json']
    require(not projection['required_imports'] and not projection['required_providers'],
            'advice introduced mandatory imports or readiness dependencies')
    package = projection_package(projection, plan)
    durable(root / 'planning/input.json', payload, immutable=True)
    durable(root / 'planning/proposal.json', proposal, immutable=True)
    for filename, value in artifacts.items():
        durable(root / 'planning' / filename, value, immutable=True)
    files = [{'theory': THEORY, 'file_slot': package['file_slot'], 'order': 0, 'layer': 0,
              'status': 'pending', 'generation_ids': [], 'seal_id': None,
              'contract_ids': [c['contract_id'] for c in contracts]}]
    listing = {'schema_version': 7, 'files': files}
    repository = Path(__file__).resolve().parents[2]
    implementation = {str(path.relative_to(repository)):
                      hashlib.sha256(path.read_bytes()).hexdigest()
                      for folder in ('execution', 'planning', 'infrastructure', 'analysis')
                      for path in (repository / 'regen' / folder).iterdir()
                      if path.suffix in {'.py', '.ML', '.sh'}}
    run = {'schema_version': 2, 'execution_mode': 'repository-projection-v1',
        'execution_input_kind': 'refine-dataset',
        'run_id': 'synthetic-proof-directions-' + uuid4().hex,
        'target': 'Synthetic_Proof_Directions', 'state': 'ready',
        'checker_context_root': str(dataset), 'checker_context_id': context['checker_context_id'],
        'execution_models': {'File Planner': MODEL},
        'contract_package_dir': str(root / 'packages'),
        'session_progress_ledger': str(root / 'session-progress-ledger.json'),
        'session_schedule': {'file_list_sha256': digest(listing),
                             'plan_id': plan['plan_id']},
        'implementation': implementation, 'validation_only': True, 'model_calls': 0,
        'checker_policy': 'fixed-prefix-cold-green-v1',
        'premise_database': str(context['premise_database_path']),
        'premise_snapshot': str(context['premise_audit_context_path']),
        'semantic_workspace': str(context['semantic_workspace']),
        'l4v': str(context['l4v']), 'base_session': context['base_session'],
        'base_import': context['base_import'], 'base_catalog': context['base_catalog'],
        'isabelle_dir': str(context['isabelle']), 'image': context['image'],
        'isabelle_sha256': context['isabelle_sha256'], 'budgets': context['budgets']}
    durable(root / 'run.json', run, immutable=True)
    durable(root / 'execution-input.json', {'file_list': listing, 'files': files,
        'package_hashes': {package['file_slot']: package['package_payload_sha256']}}, immutable=True)
    durable(root / 'packages' / (package['file_slot'] + '.json'), package, immutable=True)
    durable(root / 'heap-copy-budget.json', clone_heaps(context['heaps'], root / 'heaps'), immutable=True)
    (root / 'generated').mkdir()
    progress(root)
    return run, package


class ScriptedProvenance:
    """Hash-bound synthetic action origin; it never claims a real model call."""
    def __init__(self, root):
        self.root, self.records = root, {}

    @staticmethod
    def path(prefix):
        return Path(str(prefix) + '.synthetic-origin.json')

    def record(self, prefix, prompt, proposal, metadata, *, theory, model):
        path = self.path(prefix)
        require(path.resolve().is_relative_to(self.root), 'foreign provenance path')
        require(theory == THEORY and model == MODEL, 'foreign synthetic identity')
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
        'scope': 'synthetic proof-direction handoff; no model or benchmark proof',
        'substitutions': ['synthetic fixed-contract fixture',
                          'hash-bound synthetic action eligibility and no-model transcript marker'],
        'unmodified_authorities': ['build_bundle_v6', 'projection_package',
                                  'TheoryController', 'IsabelleChecker', 'ProofCommits',
                                  'document validation', 'candidate availability projection'],
        'cases': []}
    resources = executor = controller = None

    def case(name, **values):
        report['cases'].append({'name': name, 'pass': True, **values})
        durable(root / 'report.json', report)
        print(json.dumps({'case': name, 'pass': True}), flush=True)

    try:
        context = load_context(dataset)
        report['checker_context_id'] = context['checker_context_id']
        run_config, package = create_fixture(root, dataset, context)
        original_suggestions = deepcopy(package['repository_projection']['proof_direction_suggestions'])
        resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
            pids_limit=1024, provider_build_timeout=600, build_ml_heap_mb=2048)
        executor = CheckExecutor(root / 'check-tasks', workers=1)
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
            controller = TheoryController(root, THEORY, resources=resources, executor=executor,
                document_mode=True, model=MODEL, check_timeout=timeout,
                candidate_timeout=candidate_timeout)
            serial = 0

            def action(proposal):
                nonlocal serial
                serial += 1
                metadata = {'thread_id': 'synthetic', 'turn_id': 'synthetic',
                    'call_id': str(serial), 'synthetic': True,
                    'transcript_path': str(root / 'synthetic-transcript.json'), 'transcript_offset': 1}
                durable(root / 'tool-calls' / f'{serial:03d}-request.json',
                    {'theory': THEORY, 'proposal': proposal, 'metadata': metadata}, immutable=True)
                result = controller.handle('proof_action',
                    {'theory': THEORY, 'proposal': proposal}, metadata)
                durable(root / 'tool-calls' / f'{serial:03d}-result.json', result, immutable=True)
                return result

            def read():
                return controller.handle('read_theory', {'theory': THEORY}, {})

            def state():
                return journal(root, run_config, package).replay(repair=False)

            def edit(replacements):
                draft = read()['document']
                return action({'action': 'edit_theory', 'base_sha256': draft['source_sha256'],
                    'edits': [{'old': old, 'new': new} for old, new in replacements]})

            initial = read()
            durable(root / 'initial-view.json', initial, immutable=True)

            def candidates(view):
                advice = view.get('planning_advice', {})
                suggestions = advice.get('proof_direction_suggestions', [])
                require(len(suggestions) == 1 and suggestions[0]['id'] == 'TryCandidateReuse',
                        'repository advice was not handed to the File Planner view')
                require(suggestions[0]['target_contract_id'] == 'consumer',
                        'advice target identity changed')
                records = {row['contract_id']: row for row in suggestions[0]['candidate_contracts']}
                require(set(records) == {'candidate_true', 'candidate_false'},
                        'runtime candidate inventory differs from planning advice')
                require(all(row['applicability_verified'] is False for row in records.values()),
                        'runtime advice incorrectly certifies mathematical applicability')
                return records

            before_candidates = candidates(initial)
            require(all(row['authority_status'] == 'pending' and
                        row['can_use_as_current_premise'] is False
                        for row in before_candidates.values()),
                    'unproved advice candidate was exposed as a current premise')
            entry_view = action({'action': 'read_entry', 'entry_id': 'consumer'})
            entry_candidates = candidates(entry_view)
            expected_statements = {row['contract_id']: row['exact_statement']
                                   for row in package['contracts']}
            require(all(row['exact_statement'] == expected_statements[key]
                        and row['contract_owned_context'] == []
                        for key, row in entry_candidates.items()),
                    'entry advice did not expand the unchanged fixed candidate declarations')
            case('planner_handoff_preserves_both_candidates_without_premise_authority',
                 candidates=entry_candidates)
            false_statement = 'lemma candidate_false: "False"'
            consumer_slot = 'lemma consumer: "True & True"\n(* REGEN-PROOF *)\noops'
            true_slot = 'lemma candidate_true: "True"\n(* REGEN-PROOF *)\noops'
            invalid_body = 'using candidate_false by blast'
            saved = edit([(consumer_slot, consumer_slot.removesuffix('oops') + invalid_body)])
            rejected = action({'action': 'check_theory', 'source_sha256': saved['source_sha256'],
                               'entry_ids': ['consumer']})
            require(rejected.get('proof_verdict') == 'red',
                    'unproved candidate did not produce a native RED result: ' + json.dumps(rejected))
            require('Undefined fact' in json.dumps(rejected) and 'candidate_false' in json.dumps(rejected),
                    'negative check did not identify the unavailable suggested candidate')
            require(not state().get('proofs'), 'unproved suggested candidate published a GREEN proof')
            after_failed_candidates = candidates(read())
            require(all(row['can_use_as_current_premise'] is False
                        for row in after_failed_candidates.values()),
                    'failed proof attempt promoted a suggested candidate to a premise')
            case('suggestion_does_not_make_unproved_false_contract_a_fact',
                 proof_verdict=rejected['proof_verdict'], accepted_ids=list(state()['proofs']),
                 candidates=after_failed_candidates)

            saved = edit([(true_slot, true_slot.removesuffix('oops') + 'by simp'),
                          (invalid_body, 'using candidate_true by blast')])
            accepted = action({'action': 'check_theory', 'source_sha256': saved['source_sha256'],
                               'entry_ids': ['candidate_true', 'consumer']})
            require(accepted.get('proof_verdict') == 'green',
                    'valid alternative was blocked by unused advice: ' + json.dumps(accepted))
            require(set(state()['proofs']) == {'candidate_true', 'consumer'},
                    'acceptance includes an unproved contract or omits a proved contract')
            require('candidate_true' in state()['proofs']['consumer'],
                    'consumer proof did not retain its actual candidate reference')
            require(state()['status'] != 'sealed', 'open false contract was silently sealed')
            final = read()
            durable(root / 'final-view.json', final, immutable=True)
            final_candidates = candidates(final)
            require(final_candidates['candidate_true']['authority_status'] == 'local_green' and
                    final_candidates['candidate_true']['visibility_status'] == 'local_current_prefix' and
                    final_candidates['candidate_true']['can_use_as_current_premise'] is True,
                    'accepted preceding candidate did not become a current local premise')
            require(final_candidates['candidate_false']['authority_status'] == 'pending' and
                    final_candidates['candidate_false']['can_use_as_current_premise'] is False,
                    'ignored unproved candidate gained premise authority')
            require(false_statement + '\n(* REGEN-PROOF *)\noops' in final['document']['source'],
                    'unhelpful suggested contract was removed, proved, or weakened')
            commits = [read_artifact(path) for path in
                       sorted((root / 'theories' / THEORY / 'acceptance/commits').glob('*.json'))]
            latest = next(row for row in reversed(commits) if row['event'] == 'document')
            check = latest['evidence']['check']
            require(check.get('verdict') == 'green' and check.get('returncode') == 0,
                    'accepted result lacks a native GREEN receipt')
            durable(root / 'accepted-native-check.json', check, immutable=True)
            case('file_planner_can_choose_true_candidate_and_ignore_false_advice',
                 accepted_ids=list(state()['proofs']), open_contract='candidate_false',
                 status=state()['status'], commit_id=latest['commit_id'],
                 native_receipt=check.get('native_receipt'),
                 source_sha256=check.get('source_sha256'), candidates=final_candidates)
            require(package['repository_projection']['proof_direction_suggestions'] == original_suggestions,
                    'fixture advice mutated during execution')
            report['pass'] = True
    except BaseException as error:
        report['failure'] = {'type': type(error).__name__, 'diagnostic': str(error)}
        raise
    finally:
        if controller is not None:
            controller.close()
        if executor is not None:
            executor.close()
        if resources is not None:
            report['native_operations'] = deepcopy(resources.metrics)
            resources.close()
        report['wall_seconds'] = time.monotonic() - started
        durable(root / 'report.json', report)
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
