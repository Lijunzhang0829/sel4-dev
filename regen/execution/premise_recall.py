"""Read-only, on-demand lookup over frozen and currently certified facts."""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from regen.planning.premise_retrieval import _is_public_record

_ATOM = re.compile(r"[A-Za-z][A-Za-z0-9_']*")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_'.]*(?:\(\d+\))?")
_DEF_SUFFIX = re.compile(r"_defs?\d*$")
_SYNTAX_WORDS = {'and', 'or', 'the', 'a', 'an', 'lemma', 'theorem', 'definition',
                 'definitions', 'fact', 'facts', 'with', 'for', 'of', 'at', 'in'}


def _compact(value):
    return value.replace('_', '').casefold()


def _like(value):
    # Underscores in Isabelle identifiers are literal, not SQL wildcards.
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def _upstream(database, terms):
    path = Path(database).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'premise catalog unavailable: {path}')
    conditions, parameters = [], []
    for term in terms:
        conditions.append("(lower(replace(canonical_name, '_', '')) LIKE ? ESCAPE '\\' "
                          "OR lower(replace(exact_statement, '_', '')) LIKE ? ESCAPE '\\' "
                          "OR record_id = ?)")
        pattern = _like(_compact(term))
        parameters.extend((pattern, pattern, term))
    sql = ("SELECT record_id, canonical_name, exact_statement, owner_theory, "
           "owner_session, source_class, attributes_json FROM premises "
           "WHERE source_class IN ('frozen_parent','retained_semantic_export') AND ("
           + ' OR '.join(conditions) + ')')
    # Filter legal sources before reading statements. Score all matching rows;
    # an alphabetical SQL LIMIT must not remove the best candidate beforehand.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(sql, parameters):
            item = dict(row)
            item['attributes'] = json.loads(item.pop('attributes_json'))
            yield item


def _forms(text):
    text = re.sub(r'\\<[^>]+>', ' ', text)
    result = set()
    for atom in _ATOM.findall(text):
        parts = atom.split('_')
        # Whole identifiers or contiguous identifier components, never a bag of
        # generic camel-case words such as get/object/return.
        result.add(atom)
        if len(parts) <= 16:
            for start in range(len(parts)):
                for end in range(start + 1, len(parts) + 1):
                    result.add('_'.join(parts[start:end]))
    return result


def _exact(item, query):
    name = item['canonical_name']
    return (item['record_id'] == query or name == query
            or name.endswith('.' + query))


def _definition(item, symbol):
    namespace, _, constant = symbol.rpartition('.')
    if not constant:
        constant = symbol
    name = item['canonical_name']
    if namespace and not name.startswith(namespace + '.'):
        return False
    base = name.rsplit('.', 1)[-1]
    if not (_DEF_SUFFIX.search(base) or '.simps' in name):
        return False
    # A fact mentioning a constant inside some other operation's body is not
    # that constant's definition. Check its printed left-hand side as well.
    lhs = re.split(r'\\<equiv>|≡|\s=\s', item['exact_statement'], maxsplit=1)[0]
    return (constant in _ATOM.findall(lhs)
            and (base.startswith(constant + '_') or f'.{constant}.simps' in name))


def recall_premises(database, query, *, generated_records=None, local_facts=(),
                    scope='all', offset=0, page_size=5, mode='auto'):
    if scope not in {'all','upstream','sealed','local'}:
        raise ValueError('premise scope must be all/upstream/sealed/local')
    if mode not in {'auto', 'exact', 'definition', 'search'}:
        raise ValueError('premise mode must be auto/exact/definition/search')
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError('invalid premise query')
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('invalid premise offset')
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 5:
        raise ValueError('premise page_size must be between 1 and 5')
    query = query.strip()
    single = bool(_NAME.fullmatch(query)) or query.startswith('premise:') and ' ' not in query
    if mode in {'exact', 'definition'} and not single:
        raise ValueError('exact/definition lookup requires one identifier')
    symbol = _DEF_SUFFIX.sub('', query) if single else None
    definition_names = (query.split() if mode == 'auto' and not single
        and all(_NAME.fullmatch(t) and _DEF_SUFFIX.search(t) for t in query.split()) else [])
    terms = list(dict.fromkeys(_ATOM.findall(re.sub(r'\\<[^>]+>', ' ', query))))
    # Keep qualified identifiers intact for exact/definition SQL candidates.
    sql_terms = ([query, symbol] if single else
                 [v for t in definition_names for v in (t, _DEF_SUFFIX.sub('', t))]
                 if definition_names else terms)
    sql_terms = list(dict.fromkeys(t for t in sql_terms if t))
    terms = list({t:t for t in terms if t.casefold() not in _SYNTAX_WORDS}.values())
    if len(terms) > 24:
        raise ValueError('premise search accepts at most 24 identifier terms')
    candidates = []
    if sql_terms and scope in {'all', 'upstream'}:
        candidates.extend(_upstream(database, sql_terms))
    if scope in {'all','sealed'} and generated_records and Path(generated_records).is_file():
        candidates.extend(x for x in json.loads(Path(generated_records).read_text()).get('records', [])
                          if x.get('source_class') == 'generated_theorem' and x.get('theory_seal_id'))
    if scope in {'all','local'}:
        candidates.extend({'record_id':x['id'], 'canonical_name':x['name'],
                           'exact_statement':x['statement'], 'context':x.get('context', []),
                           'owner_theory':'current', 'source_class':'same_theory_green',
                           'proof_sha256':x.get('proof_sha256'), 'attributes':[]}
                          for x in local_facts)
    unique = {x['record_id']: x for x in candidates if _is_public_record(x)}
    exact = [x for x in unique.values() if single and _exact(x, query)]
    definitions = [x for x in unique.values() if single and _definition(x, symbol)]
    effective = mode
    if mode == 'auto':
        if definition_names:
            effective = 'definition_batch'
        elif exact:
            effective = 'exact'
        elif definitions or single and ('.' in query or _DEF_SUFFIX.search(query)):
            effective = 'definition'
        else:
            effective = 'search'
    ranked = []
    omitted_reference_matches = 0
    lookup_results = []
    if effective == 'definition_batch':
        selected_by_id = {}
        for name in definition_names:
            matches = [x for x in unique.values() if _exact(x, name)]
            kind = 'exact' if matches else 'definition'
            if not matches:
                matches = [x for x in unique.values() if _definition(x, _DEF_SUFFIX.sub('', name))]
            lookup_results.append({'query':name, 'match_kind':kind, 'matched_candidates':len(matches)})
            for x in matches:
                selected_by_id[x['record_id']] = {**x, 'match_kind':kind, 'score':1.0}
        ranked = list(selected_by_id.values())
    elif effective in {'exact', 'definition'}:
        ranked = [{**x, 'match_kind':effective, 'score':1.0}
                  for x in (exact if effective == 'exact' else definitions)]
    elif terms:
        for item in unique.values():
            names, statement = _forms(item['canonical_name']), _forms(item['exact_statement'])
            matched = [t for t in terms if t in names | statement]
            if len(matched) != len(terms):
                continue
            name_hits = sum(t in names for t in matched)
            score = 100 * len(matched) / len(terms) + 10 * name_hits / len(terms)
            ranked.append({**item, 'match_kind':'search', 'matched_terms':matched,
                           'name_match':bool(name_hits), 'score':score})
        named = [x for x in ranked if x['name_match']]
        if named:
            omitted_reference_matches = len(ranked) - len(named)
            ranked = named
    ranked.sort(key=lambda x:(-x['score'], x['canonical_name'], x['record_id']))
    selected = ranked[offset:offset + page_size]
    return {'query':query, 'scope':scope, 'mode':mode, 'effective_mode':effective,
            'offset':offset, 'records':selected,
            'next_offset':offset + len(selected) if offset + len(selected) < len(ranked) else None,
            'matched_candidates':len(ranked), 'exact_match_found':bool(exact) if single else None,
            'lookup_results':lookup_results,
            'status':'matched' if ranked else 'no_match',
            'search_policy':'case-and-underscore-sensitive identifiers; prefer name matches over body-only references',
            'omitted_reference_matches':omitted_reference_matches,
            'notice':('Complete statements; exact lookup is case-sensitive and does not fill with fuzzy facts. '
                      'Search preserves case and underscores; submit separate queries for independent definitions. '
                      'Name matches take priority over body-only references. '
                      'Search matches are lexical candidates, not interchangeable theorems. '
                      'No match is scoped to this catalog/query, not proof that no useful fact exists. '
                      'Use mode=definition with a constant or mode=search with focused identifiers.')}
