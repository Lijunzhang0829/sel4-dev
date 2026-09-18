"""Owner-independent contract presentation; source identity stays in the harness."""
from collections import defaultdict
from copy import deepcopy
import json
import re

from regen.preparation.refine_campaign import sha256_json

CONTRACT_FIELDS = ('name', 'kind', 'exact_statement', 'contract_owned_context',
                   'contract_owned_attributes', 'contract_owned_attributes_raw')


class OwnerBlindView:
    def __init__(self, source):
        self.source = source
        self.canonical_payload = deepcopy(source)
        self.to_original = {}
        self.to_public = {}
        by_semantics = defaultdict(list)
        for item in source['contracts']:
            public = {k: deepcopy(item[k]) for k in CONTRACT_FIELDS if k in item}
            by_semantics[sha256_json(public)].append((item['contract_id'], public))
        contracts = []
        for semantic_hash, items in sorted(by_semantics.items()):
            for index, (original, public) in enumerate(sorted(items, key=lambda x:x[0])):
                alias = 'contract:' + semantic_hash[:20] + (f':{index+1}' if len(items)>1 else '')
                if alias in self.to_original:
                    raise ValueError('ambiguous public contract identifier')
                self.to_original[alias] = original
                self.to_public[original] = alias
                contracts.append({'contract_id':alias, **public})
        source_cards = {c['contract_id']:c for c in source['contract_cards']}
        cards = []
        for contract in contracts:
            old = source_cards[self.to_original[contract['contract_id']]]
            cards.append({'contract_id':contract['contract_id'], 'name':contract['name'],
                          'principal_symbols':deepcopy(old['principal_symbols']),
                          'logical_shape':deepcopy(old['logical_shape'])})
        relations = []
        for index,left in enumerate(cards):
            for right in cards[index+1:]:
                shared = sorted(set(left['principal_symbols']) & set(right['principal_symbols']))
                if not shared: continue
                item = {'left_contract_id':left['contract_id'], 'right_contract_id':right['contract_id'],
                        'candidate_relation':'shared-symbols', 'evidence':{'shared_symbols':shared[:16]}}
                relations.append({'relation_id':'relation:'+sha256_json(item)[:16], **item})
        relations.sort(key=lambda x:(-len(x['evidence']['shared_symbols']), x['left_contract_id'],x['right_contract_id']))
        relations = relations[:2000]
        self.payload = {k:deepcopy(source[k]) for k in ('schema_version','artifact_type','benchmark',
            'context_profiles','parent_premises','planner_interfaces','planning_input_id')}
        self.payload.update(contract_visibility='owner-blind-v1', contracts=contracts,
                            contract_cards=cards, contract_relation_candidates=relations)
        # This payload is private and supports exact canonical file projections.
        self.canonical_payload['contract_relation_candidates'] = self.decode(relations)
        self.public_contracts = {c['contract_id']:c for c in contracts}
        labels = {x['owner_theory'] for x in source['contracts']}
        # The benchmark/session name is public; its occurrence is not a mapping
        # of a contract to the same-named original top-level theory.
        target = source['benchmark']['target_session']
        labels.update(x['owner_theory'].rsplit('.',1)[-1] for x in source['contracts']
                      if x['owner_theory'].rsplit('.',1)[-1] != target)
        self._owner_pattern = re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(
            re.escape(v) for v in sorted(labels,key=len,reverse=True)) + r')(?![A-Za-z0-9_])')
        self.assert_no_owner_disclosure(json.dumps(self.payload,ensure_ascii=False))

    def decode(self, value):
        if isinstance(value, str): return self.to_original.get(value,value)
        if isinstance(value, list): return [self.decode(v) for v in value]
        if isinstance(value, dict): return {k:self.decode(v) for k,v in value.items()}
        return value

    def assert_no_owner_disclosure(self, text):
        if self._owner_pattern.search(text) or any(old in text for old in self.to_public):
            raise ValueError('original contract theory attribution detected in planner output boundary')

    def queries(self, base):
        view = self
        class Queries:
            def __getattr__(self, name): return getattr(base,name)
            def inspect_contracts(self, contract_ids):
                maximum = view.payload['planner_interfaces']['inspect_contracts']['max_ids']
                if (not isinstance(contract_ids,list) or not contract_ids or len(contract_ids)>maximum
                        or not all(isinstance(cid,str) for cid in contract_ids) or len(set(contract_ids))!=len(contract_ids)):
                    raise ValueError('invalid contract inspection batch')
                if not set(contract_ids) <= set(view.public_contracts):
                    raise ValueError('unknown public contract ID')
                return [deepcopy(view.public_contracts[cid]) for cid in contract_ids]
        return Queries()

    def canonical_evidence(self, evidence):
        result = deepcopy(evidence)
        originals = {c['contract_id']:c for c in self.source['contracts']}
        result['inspected_contracts'] = [deepcopy(originals[self.to_original[c['contract_id']]])
                                         for c in evidence['inspected_contracts']]
        return result
