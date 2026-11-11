"""Real Calg sketch isolation controls, using synthetic statements and no model.

The actual renderer, persistent worker and oracle audits are exercised. Parent
heaps are mounted read-only. This tool never writes benchmark proofs or seals.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import digest, durable, freeze_evidence
from regen.planning.proof_graph import build_graph
from regen.planning.sketch_check import render_check, classify_conditional_failure
from regen.preparation.checker_context import load_context
from regen.tools.document_acceptance_validation import readonly_heaps, require


def run(dataset, output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'pass': False, 'model_calls': 0, 'benchmark_contracts_proved': 0,
              'scope': 'real native sketch permission and discarded-state controls',
              'substitutions': ['synthetic statements only'], 'cases': []}
    resources = NativeResources(1, 4, 1, cpus=4, memory_mb=16384)
    token = CURRENT_RESOURCES.set(resources)
    checker = None
    try:
        context = load_context(Path(dataset))
        require(context['base_session'] == 'Calg_Project_Base', 'Calg context required')
        report['checker_context_id'] = context['checker_context_id']
        report['resource_policy'] = resources.policy
        checker = IsabelleChecker(root, root/'checks', image=context['image'],
            architecture=context['architecture'], timeout=120,
            generated_dir=root/'generated', heaps_dir=context['heaps'],
            l4v_dir=context['l4v'], semantic_workspace=context['semantic_workspace'],
            base_session=context['base_session'], base_import=context['base_import'],
            base_catalog=context['base_catalog'], isabelle_dir=context['isabelle'])
        original_command = checker._command
        checker._command = lambda *a, **kw: readonly_heaps(original_command(*a, **kw))
        checker.batch_candidate_timeout = 30
        package = {'theory': 'Sketch_Isolation_Control',
                   'file_slot': 'Sketch_Isolation_Control.thy', 'contracts': [{
                       'contract_id': 'parent', 'name': 'synthetic_parent',
                       'exact_statement': 'lemma synthetic_parent: "True"',
                       'contract_owned_context': []}]}
        graph = build_graph(package, {'generated_nodes': [{
            'id': 'helper', 'name': 'synthetic_helper', 'role': 'helper',
            'statement': 'lemma synthetic_helper: "True & True"', 'context': [],
            'dependencies': [], 'strategy': ''}], 'contract_dependencies': {'parent': ['helper']}},
            run_id='synthetic-admission-control', revision=0)
        state = {'graph': graph, 'proofs': {}, 'accepted_order': []}
        nodes = {n['id']: n for n in graph['nodes']}
        group = {'target_entry_id': 'parent', 'helper_ids': ['helper']}
        def sketch(case, *, helper=None, proof='using synthetic_helper by simp',
                   owned=None, inject_consumer_sorry=False):
            selected = deepcopy(nodes)
            if helper:
                selected['helper']['statement'] = helper
            if owned is not None:
                for node in selected.values():
                    node['context'] = owned
            signature = digest({'case': case, 'nodes': selected, 'proof': proof})
            source, prefix, admitted = render_check(state, selected,
                {'parent': proof}, group, checker.imports(), signature)
            if inject_consumer_sorry:
                source = source.replace('\n'+proof+'\n', '\nsorry\n')
                require(source.count('\nsorry') == 2, 'consumer injection missed')
            result = classify_conditional_failure(freeze_evidence(
                checker.check_sketch_source(source, prefix, admitted, timeout=30)), group, signature)
            durable(root/(case+'.json'), result, immutable=True)
            return result, prefix

        def passed(name, **details):
            report['cases'].append({'name': name, 'pass': True, **details})
            durable(root/'report.json', report)
            print(json.dumps({'case': name, 'pass': True}), flush=True)

        good, prefix = sketch('conditional')
        require(good['verdict'] == 'green' and good['publishable'] is False,
                'trusted admission did not conditionally close')
        worker = checker._batch_worker
        require(worker.pending is None, 'conditional check published pending state')
        passed('trusted_admission_only_conditionally_closes')

        def normal(case, statement, proof):
            source = prefix[:-len('end\n')] + statement+'\n'+proof+'\nend\n'
            with resources.lease(checker):
                result = worker.check_batch(source, ['ordinary_control'])
            durable(root/(case+'.json'), result, immutable=True)
            return result

        leaked = normal('leaked_fact', 'lemma ordinary_control: "True"',
                        'using synthetic_helper by simp')
        require(leaked['verdict'] == 'red', 'temporary helper leaked into formal context')
        passed('temporary_fact_not_visible_to_formal_check')
        forbidden = normal('ordinary_sorry', 'lemma ordinary_control: "False"', 'sorry')
        require(forbidden['verdict'] == 'red', 'formal proof admission accepted')
        passed('formal_sorry_rejected_after_successful_sketch')

        bad, _ = sketch('bad_helper_type',
            helper='lemma synthetic_helper: "(True::nat) = 0"')
        require(bad['verdict'] == 'red' and bad.get('failure_stage') == 'helper_declaration'
                and bad.get('failed_entry_id') == 'helper', 'helper error misattributed')
        passed('helper_type_error_owned_by_helper', stage=bad['failure_stage'])

        failed, _ = sketch('consumer_failure', proof='by fail')
        require(failed['verdict'] == 'red' and failed.get('failure_stage') == 'consumer_proof',
                'consumer failure misattributed')
        passed('consumer_failure_owned_by_parent', stage=failed['failure_stage'])
        forbidden = normal('ordinary_sorry_after_exception',
                           'lemma ordinary_control: "False"', 'sorry')
        require(forbidden['verdict'] == 'red', 'admission permission leaked on exception')
        passed('permission_restored_after_failed_consumer')
        consumer_sorry, _ = sketch('extra_consumer_admission', inject_consumer_sorry=True)
        require(consumer_sorry['verdict'] == 'red', 'consumer consumed extra admission')
        passed('consumer_cannot_admit_its_own_goal')

        payload = json.loads((Path(dataset)/'repository-planning-input.json').read_text())
        contexts = []
        for contract in payload['contracts']:
            owned = contract['contract_owned_context']
            if owned not in contexts:
                contexts.append(owned)
        for index, owned in enumerate(contexts):
            contextual, _ = sketch('context-'+str(index), owned=owned)
            require(contextual['verdict'] == 'green', 'contextual admission failed: '+str(owned))
        passed('every_dataset_context_form_closes', context_forms=len(contexts))

        # Native audit must remain clean after all failed and successful trials.
        ordinary = normal('ordinary_green', 'lemma ordinary_control: "True"', 'by simp')
        require(ordinary['verdict'] == 'green', 'formal control no longer verifies')
        passed('ordinary_green_remains_oracle_clean')
        report['pass'] = True
    except BaseException as error:
        report['failure'] = {'error_type': type(error).__name__, 'diagnostic': str(error)}
        raise
    finally:
        try:
            if checker is not None:
                checker.close_prefix()
            resources.close()
        except BaseException as error:
            report['pass'] = False
            report['cleanup_error'] = str(error)
            raise
        finally:
            CURRENT_RESOURCES.reset(token)
            report['wall_seconds'] = time.monotonic()-started
            durable(root/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.dataset, args.output)))
