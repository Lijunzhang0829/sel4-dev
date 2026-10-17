"""Bounded, pre-submission plan experiments; no facts enter production state.

V1 tests root-theory startability only. It does not test speculative provider
facts, shared-helper profitability, or certify architecture quality.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    tmp.replace(path)


def settings(value):
    defaults = dict(max_rounds=2, max_targets=3, target_seconds=300,
                    model_seconds=120, check_seconds=60, max_attempts=2,
                    model='gpt-6-astra', reasoning_effort='medium')
    if set(value) - defaults.keys():
        raise ValueError('Unknown plan pilot option')
    defaults.update(value)
    for key in ('max_rounds', 'max_targets', 'target_seconds', 'model_seconds',
                'check_seconds', 'max_attempts'):
        if type(defaults[key]) is not int or defaults[key] <= 0:
            raise ValueError('Pilot budgets must be positive integers')
    if defaults['max_rounds'] > 2 or defaults['max_targets'] > 3:
        raise ValueError('V1 allows at most two rounds and three targets')
    return defaults


def select_targets(proposal, count):
    """Spread over root theories first; selection is a heuristic, not difficulty."""
    from regen.planning.import_contract import required_imports
    roots, skipped = [], []
    for theory in proposal['theories']:
        if required_imports({**theory, 'schema_version': proposal['schema_version']}):
            skipped.append(theory['name'])
        else:
            roots.append(theory)
    result = []
    for index in range(max((len(t['contract_ids']) for t in roots), default=0)):
        for theory in roots:
            if index < len(theory['contract_ids']):
                result.append({'theory': theory['name'], 'contract_id': theory['contract_ids'][index]})
                if len(result) == count:
                    return result, skipped
    return result, skipped


class PlanPilot:
    def __init__(self, root, binding, policy, attempt):
        self.root = Path(root)
        self.policy = settings(policy)
        self.binding = deepcopy(binding)
        self.attempt = attempt
        self.identity = digest({'binding': binding, 'policy': self.policy})

    def key(self, proposal):
        return digest({'pilot': self.identity, 'proposal': proposal})

    def load(self, proposal):
        path = self.root / self.key(proposal) / 'result.json'
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        receipt = value.pop('receipt_sha256')
        if receipt != digest(value) or value['binding'] != self.identity or value['proposal_sha256'] != digest(proposal):
            raise ValueError('Pilot evidence binding mismatch')
        for name, expected in value.get('artifact_sha256', {}).items():
            artifact = self.root / name
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
                raise ValueError('Pilot artifact integrity mismatch')
        return {**value, 'receipt_sha256': receipt}

    def run(self, payload, proposal):
        existing = self.load(proposal)
        if existing:
            return existing
        rounds = list(self.root.glob('*/started.json'))
        if len(rounds) >= self.policy['max_rounds']:
            raise ValueError('Pilot round budget exhausted; interrupted rounds remain charged')
        directory = self.root / self.key(proposal)
        if (directory / 'started.json').exists():
            raise ValueError('Interrupted pilot must not silently restart')
        selected, skipped = select_targets(proposal, self.policy['max_targets'])
        write(directory / 'started.json', {'binding': self.identity, 'proposal_sha256': digest(proposal),
                                          'selected': selected, 'round': len(rounds) + 1})
        contracts = {c['contract_id']: c for c in payload['contracts']}
        results = []
        for row in selected:
            start = time.monotonic()
            history = []
            for number in range(self.policy['max_attempts']):
                remaining = self.policy['target_seconds'] - (time.monotonic() - start)
                if remaining < 2:
                    break
                result = self.attempt(contracts[row['contract_id']], history,
                                      directory / ('target-' + str(len(results))) / str(number + 1),
                                      remaining, self.policy)
                history.append(result)
                write(directory / 'progress.json', {'completed': results, 'current': row, 'attempts': history})
                if result['status'] in {'proved', 'infrastructure_error', 'model_error', 'budget_exhausted'}:
                    break
            status = history[-1]['status'] if history else 'budget_exhausted'
            results.append({**row, 'status': status, 'attempts': history,
                            'wall_seconds': time.monotonic() - start})
        report = {'schema_version': 1, 'binding': self.identity, 'proposal_sha256': digest(proposal),
                  'selected': selected, 'results': results, 'untested_provider_theories': skipped,
                  'coverage': 'root-contract-startability', 'architecture_quality_certified': False,
                  'publishable': False, 'production_green': [],
                  'interpretation': 'Failures/timeouts are inconclusive; no automatic regrouping or import inference.'}
        report['artifact_sha256'] = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}
        report['receipt_sha256'] = digest(report)
        write(directory / 'result.json', report)
        return report

    def submission(self, proposal, review):
        result = self.load(proposal)
        if result is None:
            raise ValueError('Run pilot for this exact plan before submission')
        if not isinstance(review, str) or not review.strip():
            raise ValueError('Submission requires pilot_review explaining retain/revise and unresolved risks')
        return {'pilot_receipt_sha256': result['receipt_sha256'], 'pilot_review': review}


def native_attempt(dataset, *, allow_external_call):
    """Use the existing audited model transport and audited Isabelle node checker."""
    from regen.preparation.checker_context import load_context
    from regen.preparation.planning_input import PlanningInputQueries
    ctx = load_context(Path(dataset))
    queries = PlanningInputQueries(Path(dataset) / 'repository-planning-input.json')

    def attempt(contract, history, output, remaining, policy):
        from regen.infrastructure.llm_transcript import call_codex
        from regen.infrastructure.llm_transcript import validate_call_artifact
        from regen.planning.planner_io import parse_model_json
        from regen.execution.isabelle_node_checker import IsabelleChecker
        from regen.execution.proof_batches import _safe_text
        from regen.planning.declaration_index import initial_index
        if not allow_external_call:
            raise ValueError('Pilot model calls are not authorized')
        output.mkdir(parents=True, exist_ok=True)
        # Definitions are retrieved by the frozen statement's identifiers, not history.
        from regen.planning.definition_queries import definition_ids, mentioned_identifiers
        ids, _ = definition_ids(queries.database, mentioned_identifiers({'exact_statement': contract['exact_statement']}), 12, 0)
        facts = queries.inspect_parent_premises(ids) if ids else []
        prompt = ('Prove the exact Isabelle contract below using only its context and the legal base environment. '
                  'Return JSON {"proof":"complete proof text"}. No extra declarations, admissions, ML, tools or file access. '
                  'This is a bounded root-theory planning pilot, not architecture certification. '
                  'Repair the prior candidate from checker feedback if present.\n' + json.dumps(
                      {'contract': contract, 'definitions': facts, 'previous_attempts': history}, ensure_ascii=False))
        start = time.monotonic()
        prefix = output / 'model'
        try:
            proof_json = call_codex(prompt, prefix, model=policy['model'],
                reasoning_effort=policy['reasoning_effort'], timeout=max(1, int(min(policy['model_seconds'], remaining - 1))),
                theory='Planning_Pilot', stage='plan-pilot', clean_runtime=True)
            meta = validate_call_artifact(prefix, require_real=True)
            if not meta.get('sealable_generation'):
                raise ValueError('Unsealable model generation')
            value = parse_model_json(proof_json)
            if set(value) != {'proof'} or not isinstance(value['proof'], str):
                raise ValueError('Expected a proof-only JSON object')
            proof = value['proof']; _safe_text(proof)
        except Exception as error:
            return {'status': 'model_error', 'diagnostic': str(error), 'model_artifact': str(prefix)}
        remaining -= time.monotonic() - start
        if remaining < 1:
            return {'status': 'budget_exhausted', 'proof': proof}
        checker = IsabelleChecker(output, output / 'native', image=ctx['image'], architecture=ctx['architecture'],
            timeout=max(1, int(min(policy['check_seconds'], remaining))), generated_dir=output / 'generated',
            heaps_dir=ctx['heaps'], l4v_dir=ctx['l4v'], semantic_workspace=ctx['semantic_workspace'],
            base_session=ctx['base_session'], base_import=ctx['base_import'], base_catalog=ctx['base_catalog'], isabelle_dir=ctx['isabelle'])
        package = {'theory': 'Planning_Pilot', 'file_slot': 'Planning_Pilot.thy', 'contracts': [contract]}
        graph = initial_index(package, run_id='planning-pilot')
        try:
            checked = checker.check_node(graph, {contract['contract_id']: proof}, contract['contract_id'], audit=True)
            status = 'proved' if checked['verdict'] == 'green' else ('check_timeout' if checked['returncode'] == 124 else 'proof_failed')
            write(output / 'check.json', checked)
            return {'status': status, 'proof': proof, 'diagnostic': checked.get('diagnostic', ''),
                    'check_artifact': str(output / 'check.json'), 'model_artifact': str(prefix)}
        except Exception as error:
            return {'status': 'infrastructure_error', 'diagnostic': str(error)}
        finally:
            checker.close_prefix()
    attempt.identity = {'checker_context_id': ctx['checker_context_id'],
                        'planning_input_sha256': hashlib.sha256((Path(dataset)/'repository-planning-input.json').read_bytes()).hexdigest()}
    return attempt
