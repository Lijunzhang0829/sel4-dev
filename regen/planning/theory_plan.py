"""Theory-level repository proposal schema. Exact local proof DAGs belong to File Planner."""
import json
from regen.preparation.refine_campaign import sha256_json


def prompt(payload, feedback):
    if payload.get('artifact_type') == 'repository-planning-input':
        return prompt_v4(payload, feedback)
    schema={'schema_version':3,'ordering_rationale':'Explain the overall order and why independent theories are prioritized.',
        'theories':[{'name':'Generated_Operations','purpose':'Results this theory should establish',
            'grouping_reason':'Why these targets/results belong together; consider alternatives to original owners.',
            'contract_ids':['exact frozen contract ID'],
            'auxiliary_intents':[{'id':'shared_bridge','intent':'Describe a useful shared intermediate result, not an exact statement',
                                  'for_contracts':['exact frozen contract ID']}],
            'suggested_predecessors':[{'theory':'Generated_Foundation','reason':'What established results may help this theory'}],
            'premise_ids':['visible legal premise ID']}]}
    return ('Plan a repository of Isabelle proofs at THEORY level. Use only LEGAL INPUT; never read or reconstruct deleted human proofs.\n'
        'Distribute all fixed contracts exactly once, without modifying statements/context/attributes. Original owners are provenance only. '
        'Consider useful grouping, shared results, and cross-theory collaboration across the complete contract set. '
        'Output a theory DAG directly via suggested_predecessors. Each edge needs a concrete reason describing expected assistance. '
        'These are revisable scheduling suggestions, not mandatory premise use, imports, or cross-file helper commitments. '
        'Do not output contract/helper dependency edges, a local proof DAG, exact helper statements, proofs, tactics, or assumptions. '
        'File Planner alone refines exact local auxiliary statements and scheduling prerequisites. '
        'Auxiliary intents are optional coarse descriptions of shared results, linked to the fixed targets they serve; auxiliary-only theories are allowed. '
        'Zero auxiliary intents and zero predecessor edges are allowed when justified by the grouping/ordering rationale; never invent edges to fill a quota. '
        'Unproved intents and contracts are not premises. Failed auxiliaries may be revoked; fixed contracts may not. '
        'Every generated theory must carry contracts or auxiliary intents. Use unique simple theory names and intent IDs. '
        'The theories ARRAY ORDER is your explicit tie-break priority among currently ready theories. Dependencies override this priority. '
        'Explain that priority in ordering_rationale, including priorities when theories are independent. '
        'Only cite visible legal premise IDs; absence from lexical retrieval does not establish absence from the legal environment. '
        'Do not claim mathematical independence, successful proofs or complete semantic analysis from structural validation. '
        'Return JSON matching this schema (all fields required; lists may be empty):\n'+json.dumps(schema,ensure_ascii=False,sort_keys=True)
        +'\nPREVIOUS VALIDATION ERRORS:\n'+json.dumps(feedback or [],ensure_ascii=False)
        +'\nLEGAL INPUT:\n'+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def normalize(payload, proposal):
    if proposal.get("schema_version") == 6:
        from regen.planning.import_contract import normalize_v6
        return normalize_v6(payload, proposal)
    if proposal.get("schema_version") == 5:
        from regen.planning.import_contract import legacy_proposal
        result = normalize_v4(payload, legacy_proposal(proposal))
        result["required_import_edges"] = result.pop("suggested_theory_edges")
        for theory in result["theories"]:
            theory["required_imports"] = theory.pop("suggested_predecessors")
        return result
    if proposal.get('schema_version') == 4:
        return normalize_v4(payload, proposal)
    from regen.planning.repository_planner import NAME, id_list, topological
    if set(proposal)!={'schema_version','theories','ordering_rationale'} or proposal['schema_version']!=3:
        raise ValueError('expected theory-level proposal schema 3; local proof nodes/edges are not accepted')
    def prose(value,label):
        if not isinstance(value,str) or not value.strip(): raise ValueError(f'{label} requires a nonempty rationale')
    prose(proposal['ordering_rationale'],'theory ordering')
    raw=proposal['theories']
    if not isinstance(raw,list) or not raw: raise ValueError('theories must be nonempty')
    contracts={c['contract_id']:c for c in payload['contracts']}
    premises={p['record_id'] for p in payload['legal_premises']}
    names=set();intent_ids=set();assigned=[];theories=[];edges=[]
    for rank,t in enumerate(raw):
        if not isinstance(t,dict) or set(t)!={'name','purpose','grouping_reason','contract_ids','auxiliary_intents','suggested_predecessors','premise_ids'}:
            raise ValueError('theory fields must match schema 3; no local nodes, proofs, or imports')
        name=t['name']
        if not isinstance(name,str) or not NAME.fullmatch(name) or name in names: raise ValueError('invalid/duplicate theory name')
        names.add(name);prose(t['purpose'],name+' purpose');prose(t['grouping_reason'],name+' grouping')
        ids=id_list(t['contract_ids'],name+' contracts')
        if not set(ids)<=contracts.keys(): raise ValueError('unknown contract ID')
        assigned+=ids
        if len({contracts[key]['name'] for key in ids})!=len(ids): raise ValueError('contract declaration name collision')
        cited=id_list(t['premise_ids'],name+' premises')
        if not set(cited)<=premises: raise ValueError('premise not visible in legal input')
        intents=t['auxiliary_intents']
        if not isinstance(intents,list): raise ValueError('auxiliary_intents must be a list')
        for intent in intents:
            if not isinstance(intent,dict) or set(intent)!={'id','intent','for_contracts'}: raise ValueError('auxiliary intent cannot contain a proof DAG or exact statement')
            key=intent['id']
            if not isinstance(key,str) or not NAME.fullmatch(key) or key in intent_ids: raise ValueError('invalid/duplicate auxiliary intent ID')
            intent_ids.add(key);prose(intent['intent'],'auxiliary intent')
            served=id_list(intent['for_contracts'],'intent targets')
            if not served or not set(served)<=contracts.keys(): raise ValueError('auxiliary intent must serve known fixed targets')
        if not ids and not intents: raise ValueError('theory has no targets or auxiliary intents')
        predecessors=t['suggested_predecessors']
        if not isinstance(predecessors,list): raise ValueError('theory predecessors must be a list')
        seen=set()
        for pred in predecessors:
            if not isinstance(pred,dict) or set(pred)!={'theory','reason'}: raise ValueError('theory edge requires provider and reason')
            provider=pred['theory']
            if not isinstance(provider,str) or provider==name or provider in seen: raise ValueError('self/duplicate/invalid theory predecessor')
            seen.add(provider);prose(pred['reason'],'theory edge')
            edges.append({'provider':provider,'consumer':name,'reason':pred['reason']})
        theories.append({**t,'file_slot':name+'.thy','priority_rank':rank,
                         'contracts':[contracts[key] for key in ids]})
    if sorted(assigned)!=sorted(contracts): raise ValueError('every fixed contract must occur exactly once')
    if any(e['provider'] not in names for e in edges): raise ValueError('unknown predecessor theory')
    priority={t['name']:t['priority_rank'] for t in theories}
    order,depths=topological(list(priority),[(e['provider'],e['consumer']) for e in edges],priority=priority.__getitem__)
    by_name={t['name']:t for t in theories}
    return {'theories':[by_name[name] for name in order], 'theory_order':order,'theory_depths':depths,
            'priority_order':[t['name'] for t in theories],'ordering_rationale':proposal['ordering_rationale'],
            'suggested_theory_edges':edges}


def build_bundle(payload,proposal):
    if proposal.get("schema_version") == 6:
        from regen.planning.import_contract import build_bundle_v6
        return build_bundle_v6(payload, proposal)
    if proposal.get("schema_version") == 5:
        from regen.planning.import_contract import build_bundle_v5
        return build_bundle_v5(payload, proposal)
    if proposal.get('schema_version') == 4:
        return build_bundle_v4(payload, proposal)
    normalized=normalize(payload,proposal)
    body={'schema_version':3,'artifact_type':'repository-proof-plan','campaign_id':payload['campaign_id'],
        'environment_gate_id':payload['environment_gate_id'],'environment_dataflow_id':payload['environment_dataflow_id'],
        'planner_input_sha256':sha256_json(payload),'proposal_sha256':sha256_json(proposal),'policy_sha256':payload['policy_sha256'],
        'status':'proposed',**normalized,'semantics':{'local_proof_graph_included':False,'node_statements_certified':False,
            'suggested_edges_are_proof_dependencies':False,'generated_facts_published':False,'rationale_semantically_verified':False}}
    plan={**body,'plan_id':'repository-plan:'+sha256_json(body)[:20]}
    artifacts={'repository-plan.json':plan};files=[];by_name={t['name']:t for t in plan['theories']}
    premises={p['record_id']:p for p in payload['legal_premises']}
    for t in plan['theories']:
        projection={'schema_version':3,'artifact_type':'repository-file-projection','plan_id':plan['plan_id'],
            'environment_gate_id':plan['environment_gate_id'],'environment_dataflow_id':plan['environment_dataflow_id'],
            'theory':t['name'],'file_slot':t['file_slot'],'purpose':t['purpose'],'grouping_reason':t['grouping_reason'],
            'contracts':t['contracts'],'auxiliary_intents':t['auxiliary_intents'],'suggested_predecessors':t['suggested_predecessors'],
            'priority_rank':t['priority_rank'],'ordering_rationale':plan['ordering_rationale'],
            'suggested_providers':[{'theory':p['theory'],'purpose':by_name[p['theory']]['purpose'],
                'contract_ids':by_name[p['theory']]['contract_ids'],'auxiliary_intents':by_name[p['theory']]['auxiliary_intents'],
                'reason':p['reason'],'usable_as_premise':False} for p in t['suggested_predecessors']],
            'legal_premises':[premises[key] for key in t['premise_ids']], 'status':'requires-file-planning-and-Isabelle-checks'}
        filename='files/'+t['name']+'.json';artifacts[filename]=projection
        files.append({'theory':t['name'],'file_slot':t['file_slot'],'contract_ids':t['contract_ids'],
            'projection':filename,'projection_sha256':sha256_json(projection)})
    artifacts['file-generation-list.json']={'schema_version':4,'artifact_type':'repository-file-generation-list',
        'plan_id':plan['plan_id'],'environment_gate_id':plan['environment_gate_id'],'environment_dataflow_id':plan['environment_dataflow_id'],
        'files':files,'serial_fallback':plan['theory_order'],'priority_order':plan['priority_order'],
        'contract_count':len(payload['contracts']),'file_count':len(files),'next_module':'file-planning','execution_ready':False}
    return artifacts


def compact_input(payload):
    """Losslessly tabulate repeated planner metadata; retain every candidate."""
    cards = payload['contract_cards']
    indexes = {card['contract_id']: index for index, card in enumerate(cards)}
    symbols = sorted({symbol for card in cards for symbol in card['principal_symbols']}
                     | {symbol for item in payload['contract_relation_candidates']
                        for symbol in item['evidence']['shared_symbols']})
    symbol_indexes = {symbol: index for index, symbol in enumerate(symbols)}
    blind = payload.get('contract_visibility') == 'owner-blind-v1'
    sanitized = payload.get('contract_visibility') in {'owner-blind-v1','source-theory-visible-v1'}
    card_columns = ['contract_id', 'name'] + ([] if blind else ['owner_theory']) + [
        'principal_symbol_indexes', 'logical_shape']
    metadata = ('benchmark', 'context_profiles', 'parent_premises') if sanitized else (
        'benchmark', 'owner_groups', 'session_graph', 'theory_import_graph',
        'context_profiles', 'parent_premises')
    compact = {key: payload[key] for key in metadata}
    if sanitized:
        compact['contract_visibility'] = payload['contract_visibility']
    compact['symbols'] = symbols
    compact['symbol_encoding'] = 'zero-based indexes in symbols; exact inspected statements are unchanged'
    compact['contract_cards'] = {
        'columns': card_columns,
        'rows': [[card['contract_id'], card['name']] + ([] if blind else [card['owner_theory']]) + [
                  [symbol_indexes[symbol] for symbol in card['principal_symbols']],
                  card['logical_shape']] for card in cards],
    }
    compact['contract_relation_candidates'] = {
        'endpoint_encoding': 'zero-based row indexes in contract_cards.rows',
        'columns': ['relation_id', 'left_contract_index', 'right_contract_index',
                    'candidate_relation', 'shared_symbol_indexes'] + ([] if sanitized else ['same_owner_theory']),
        'rows': [[item['relation_id'], indexes[item['left_contract_id']],
                  indexes[item['right_contract_id']], item['candidate_relation'],
                  [symbol_indexes[symbol] for symbol in item['evidence']['shared_symbols']]] +
                  ([] if sanitized else [item['evidence']['same_owner_theory']])
                 for item in payload['contract_relation_candidates']],
    }
    return compact


def compact_evidence(evidence):
    result = dict(evidence)
    # Integrity hashes stay in the frozen files; the model needs exact syntax.
    result['inspected_contracts'] = [
        {key: value for key, value in contract.items() if not key.endswith('_sha256')}
        for contract in evidence.get('inspected_contracts', [])
    ]
    return result


def prompt_v4(payload, feedback):
    """Compact Refine prompt; exact records arrive only through bounded inspection."""
    schema = {
        'schema_version': 4,
        'ordering_rationale': 'Explain the repository decomposition, major alternatives, shared-result strategy, DAG order, ready priority, and remaining uncertainties.',
        'contract_relations': [{
            'left_contract_id': 'contract ID',
            'right_contract_id': 'contract ID',
            'relation': 'shared-invariant|semantic-chain|common-premise|other',
            'evidence_ids': ['returned relation or parent-premise ID'],
        }],
        'theories': [{
            'name': 'Generated_SemanticRole',
            'purpose': 'semantic responsibility and results this theory will provide to its consumers',
            'grouping_reason': 'why these contracts belong together and are separate from neighboring theories; cite inspected semantics and context requirements',
            'contract_ids': ['contract ID'],
            'context_profile_ids': ['allowed context profile ID'],
            'helper_decisions': [{
                'id': 'decision ID',
                'kind': 'reuse_existing_fact|propose_auxiliary_intent',
                'intent': 'the reasoning gap this closes',
                'for_contracts': ['contract ID'],
                'premise_ids': ['inspected premise ID; only for reuse'],
            }],
            'suggested_predecessors': [{
                'theory': 'provider theory',
                'reason': 'expected established result',
            }],
        }],
        'unresolved_retrieval_questions': ['bounded unresolved question'],
    }
    compact = compact_input(payload)
    compact['planner_evidence'] = compact_evidence(payload.get('planner_evidence', {}))
    return (
        'You are responsible for designing the complete repository architecture '
        'for the prepared seL4 Refine benchmark at THEORY level. '
        'Explicitly allocate the supplied contracts to DIFFERENT generated theories: '
        'design a coherent multi-theory decomposition, choosing the number, names, '
        'boundaries, and allocation yourself. A single catch-all theory for this '
        'repository does not fulfill the planning task. Do not split arbitrarily '
        'or add empty theories merely to make the result plural. A singleton '
        'contract input naturally needs only one contract-bearing theory. '
        'Review the entire contract catalog, including contracts outside your '
        'current working group, and inspect full statements and owned contexts '
        'before relying on their meaning or context compatibility. Shared context '
        'or a common correspondence predicate alone does not justify merging '
        'different semantic responsibilities. '
        'For each theory, explain its responsibility, the cohesion of its assigned '
        'contracts, its boundary with neighboring theories, and the results it '
        'could provide to consumers. Evaluate alternative splits and merges, '
        'shared invariant/result opportunities, manageable local proof-planning '
        'scope, and avoidable coupling. Record these decisions in purpose, '
        'grouping_reason, and the global ordering_rationale. '
        'Plan cross-theory collaboration: identify useful existing helpers through '
        'explicit retrieval where needed, and describe new shared-result intentions '
        'with their intended consumers in helper_decisions. Explain the shared-result '
        'strategy even when no helpers are proposed. Use suggested_predecessors '
        'only for justified provider/consumer or scheduling relationships, describe '
        'the expected assistance, and explain both the DAG and ready-theory '
        'tie-break order. Absence of known dependencies is not a reason to collapse '
        'all contracts into one theory; independent theories can remain separate. '
        'Before submitting, review global coverage, boundaries, context requirements, '
        'shared results, ordering, and unresolved questions as a handoff to File '
        'Planner. Structural validation checks format and consistency, not the '
        'quality or completeness of your architectural reasoning. Continue revising '
        'when the architecture is inadequate even if validation passes. '
        'Refine and '
        'CRefine are independent experiments; introduce no CRefine or '
        'cross-experiment inputs. Assign every fixed contract exactly once. '
        'Use only the supplied legal input and any explicit harness operations '
        'offered by the enclosing interaction. Do not read files, history, '
        'removed proofs, or external sources. Choose theory grouping and scheduling '
        'from contract semantics and inspected legal facts. No original contract '
        'theory grouping is supplied in owner-blind mode. Consider shared results without '
        'inventing dependencies or auxiliaries to fill a quota. An unproved '
        'contract or intent is never an available premise. Inspected premises '
        'come from registered static frozen-parent or retained-semantic sources; '
        'retrieval similarity does not certify applicability. Cite an inspected '
        'premise for reuse_existing_fact; otherwise '
        'emit only an auxiliary intent. Do not output exact new helper '
        'statements, tactics, proofs, local proof edges, imports, commands, or '
        'source paths. suggested_predecessors select planned providers: downstream '
        'execution waits for their seals and freezes their imports. These edges '
        'constrain availability but are not mandatory proof citations. Do not add '
        'provider edges merely to prioritize work. The theories array separately '
        'defines the ready-theory tie-break priority. '
        'Return JSON only matching this schema (all fields required):\n'
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
        + '\nPREVIOUS VALIDATION ERRORS:\n'
        + json.dumps(feedback or [], ensure_ascii=False)
        + '\nCOMPACT LEGAL INPUT:\n'
        + json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )


def normalize_v4(payload, proposal):
    from regen.planning.repository_planner import NAME, id_list, topological
    expected = {
        'schema_version', 'ordering_rationale', 'contract_relations', 'theories',
        'unresolved_retrieval_questions',
    }
    if set(proposal) != expected or proposal['schema_version'] != 4:
        raise ValueError('expected direct repository proposal schema 4')

    def prose(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{label} requires nonempty text')

    prose(proposal['ordering_rationale'], 'ordering rationale')
    contracts = {item['contract_id']: item for item in payload['contracts']}
    profiles = {item['context_profile_id'] for item in payload['context_profiles']}
    deterministic = {
        item['relation_id'] for item in payload['contract_relation_candidates']
    }
    inspected_records = payload.get('planner_evidence', {}).get(
        'inspected_parent_premises', []
    )
    inspected = {item['record_id'] for item in inspected_records}
    allowed_evidence = deterministic | inspected

    relations = proposal['contract_relations']
    if not isinstance(relations, list):
        raise ValueError('contract_relations must be a list')
    normalized_relations = []
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {
            'left_contract_id', 'right_contract_id', 'relation', 'evidence_ids',
        }:
            raise ValueError('invalid contract relation')
        left, right = relation['left_contract_id'], relation['right_contract_id']
        if left == right or left not in contracts or right not in contracts:
            raise ValueError('contract relation references invalid endpoints')
        prose(relation['relation'], 'contract relation')
        evidence = id_list(relation['evidence_ids'], 'contract relation evidence')
        if not evidence or not set(evidence) <= allowed_evidence:
            raise ValueError('contract relation lacks inspected evidence')
        normalized_relations.append({**relation, 'evidence_ids': sorted(evidence)})

    questions = proposal['unresolved_retrieval_questions']
    if (
        not isinstance(questions, list) or len(questions) > 50
        or not all(isinstance(item, str) and item.strip() for item in questions)
    ):
        raise ValueError('invalid unresolved retrieval questions')
    raw = proposal['theories']
    if not isinstance(raw, list) or not raw:
        raise ValueError('theories must be nonempty')

    names, decisions = set(), set()
    assigned, theories, edges = [], [], []
    for rank, theory in enumerate(raw):
        fields = {
            'name', 'purpose', 'grouping_reason', 'contract_ids',
            'context_profile_ids', 'helper_decisions', 'suggested_predecessors',
        }
        if not isinstance(theory, dict) or set(theory) != fields:
            raise ValueError('schema 4 theory contains invalid fields')
        name = theory['name']
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError('invalid/duplicate theory name')
        names.add(name)
        prose(theory['purpose'], name + ' purpose')
        prose(theory['grouping_reason'], name + ' grouping')
        contract_ids = id_list(theory['contract_ids'], name + ' contracts')
        if not set(contract_ids) <= contracts.keys():
            raise ValueError('unknown contract ID')
        assigned.extend(contract_ids)
        context_ids = id_list(
            theory['context_profile_ids'], name + ' context profiles'
        )
        if not context_ids or not set(context_ids) <= profiles:
            raise ValueError('unknown or empty context profile selection')

        helper_decisions = theory['helper_decisions']
        if not isinstance(helper_decisions, list):
            raise ValueError('helper_decisions must be a list')
        normalized_decisions = []
        for decision in helper_decisions:
            if not isinstance(decision, dict) or set(decision) != {
                'id', 'kind', 'intent', 'for_contracts', 'premise_ids',
            }:
                raise ValueError('invalid helper decision')
            decision_id = decision['id']
            if (
                not isinstance(decision_id, str)
                or not NAME.fullmatch(decision_id) or decision_id in decisions
            ):
                raise ValueError('invalid/duplicate helper decision ID')
            decisions.add(decision_id)
            prose(decision['intent'], decision_id + ' intent')
            served = id_list(decision['for_contracts'], decision_id + ' targets')
            if not served or not set(served) <= contracts.keys():
                raise ValueError('helper decision serves unknown contracts')
            premise_ids = id_list(
                decision['premise_ids'], decision_id + ' premises'
            )
            if decision['kind'] == 'reuse_existing_fact':
                if not premise_ids or not set(premise_ids) <= inspected:
                    raise ValueError('reuse decision lacks inspected parent premise')
            elif decision['kind'] == 'propose_auxiliary_intent':
                if premise_ids:
                    raise ValueError('auxiliary intent cannot claim a premise')
            else:
                raise ValueError('unknown helper decision kind')
            normalized_decisions.append({
                **decision, 'for_contracts': sorted(served),
                'premise_ids': sorted(premise_ids),
            })
        if not contract_ids and not normalized_decisions:
            raise ValueError('theory has no contracts or helper decisions')

        predecessors = theory['suggested_predecessors']
        if not isinstance(predecessors, list):
            raise ValueError('theory predecessors must be a list')
        seen = set()
        for predecessor in predecessors:
            if not isinstance(predecessor, dict) or set(predecessor) != {
                'theory', 'reason',
            }:
                raise ValueError('invalid theory predecessor')
            provider = predecessor['theory']
            if provider == name or provider in seen:
                raise ValueError('self/duplicate theory predecessor')
            seen.add(provider)
            prose(predecessor['reason'], 'theory edge')
            edges.append({
                'provider': provider, 'consumer': name,
                'reason': predecessor['reason'],
            })
        theories.append({
            **theory,
            'helper_decisions': normalized_decisions,
            'file_slot': name + '.thy',
            'priority_rank': rank,
            'contracts': [contracts[key] for key in contract_ids],
        })

    if sorted(assigned) != sorted(contracts):
        raise ValueError('every fixed contract must occur exactly once')
    if any(edge['provider'] not in names for edge in edges):
        raise ValueError('unknown predecessor theory')
    priority = {item['name']: item['priority_rank'] for item in theories}
    order, depths = topological(
        list(priority), [(edge['provider'], edge['consumer']) for edge in edges],
        priority=priority.__getitem__,
    )
    by_name = {item['name']: item for item in theories}
    return {
        'theories': [by_name[name] for name in order],
        'theory_order': order,
        'theory_depths': depths,
        'priority_order': [item['name'] for item in theories],
        'ordering_rationale': proposal['ordering_rationale'],
        'contract_relations': normalized_relations,
        'suggested_theory_edges': edges,
        'unresolved_retrieval_questions': questions,
    }


def build_bundle_v4(payload, proposal):
    normalized = normalize_v4(payload, proposal)
    body = {
        'schema_version': 4,
        'artifact_type': 'repository-proof-plan',
        'benchmark': payload['benchmark'],
        'planning_input_id': payload['planning_input_id'],
        'planner_input_sha256': sha256_json(payload),
        'status': 'proposed',
        **normalized,
        'semantics': {
            'local_proof_graph_included': False,
            'node_statements_certified': False,
            'suggested_edges_are_proof_dependencies': False,
            'generated_facts_published': False,
        },
    }
    artifacts = {'repository-plan.json': body}
    premises = {
        item['record_id']: item
        for item in payload.get('planner_evidence', {}).get(
            'inspected_parent_premises', []
        )
    }
    by_name = {item['name']: item for item in body['theories']}
    files = []
    for theory in body['theories']:
        reused = sorted({
            premise_id
            for decision in theory['helper_decisions']
            for premise_id in decision['premise_ids']
        })
        auxiliaries = [
            decision for decision in theory['helper_decisions']
            if decision['kind'] == 'propose_auxiliary_intent'
        ]
        projection = {
            'schema_version': 4,
            'artifact_type': 'repository-file-projection',
            'planning_input_id': payload['planning_input_id'],
            'theory': theory['name'],
            'file_slot': theory['file_slot'],
            'purpose': theory['purpose'],
            'grouping_reason': theory['grouping_reason'],
            'contracts': theory['contracts'],
            'contract_ids': theory['contract_ids'],
            'contract_relations': [
                relation for relation in body['contract_relations']
                if relation['left_contract_id'] in theory['contract_ids']
                or relation['right_contract_id'] in theory['contract_ids']
            ],
            'context_profile_ids': theory['context_profile_ids'],
            'helper_decisions': theory['helper_decisions'],
            'auxiliary_intents': auxiliaries,
            'reused_parent_premise_ids': reused,
            'legal_premises': [premises[key] for key in reused],
            'suggested_predecessors': theory['suggested_predecessors'],
            'suggested_providers': [{
                'theory': predecessor['theory'],
                'purpose': by_name[predecessor['theory']]['purpose'],
                'reason': predecessor['reason'],
                'usable_as_premise': False,
            } for predecessor in theory['suggested_predecessors']],
            'priority_rank': theory['priority_rank'],
            'status': 'requires-file-planning-and-Isabelle-checks',
        }
        filename = 'files/' + theory['name'] + '.json'
        artifacts[filename] = projection
        files.append({
            'theory': theory['name'],
            'file_slot': theory['file_slot'],
            'contract_ids': theory['contract_ids'],
            'context_profile_ids': theory['context_profile_ids'],
            'provider_theories': [
                item['theory'] for item in theory['suggested_predecessors']
            ],
            'projection': filename,
            'projection_sha256': sha256_json(projection),
        })
    artifacts['file-generation-list.json'] = {
        'schema_version': 5,
        'artifact_type': 'repository-file-generation-list',
        'planning_input_id': payload['planning_input_id'],
        'files': files,
        'serial_fallback': body['theory_order'],
        'priority_order': body['priority_order'],
        'suggested_theory_edges': body['suggested_theory_edges'],
        'contract_count': len(payload['contracts']),
        'file_count': len(files),
        'next_module': 'file-planning',
        'execution_ready': False,
    }
    return artifacts
