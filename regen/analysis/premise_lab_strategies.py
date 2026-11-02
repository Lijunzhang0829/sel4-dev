"""Isolated, synthetic retrieval ablations; never a source of proof authority.

The baseline invokes the production readers. Experimental strategies live here,
not in the proof harness. Labels must remain outside the catalog and requests.
The ungated search deliberately mirrors production's private identifier helpers;
its only ranking change is retaining body-only matches. Native applicability,
generated-fact publication and proof success are outside this module's scope.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from regen.execution import premise_recall, proof_state_recall
from regen.execution.proof_state_recall import recall_for_proof_state, tokens
from regen.planning.premise_retrieval import _is_public_record
from regen.preparation.premise_database import SCHEMA, digest, verify_database


VARIANTS = ('baseline', 'body_candidates', 'normalized_fts', 'digest_groups', 'combined')
NORMALIZER = 'proof-state-tokens-utf8hex-v1'
NOTICE = ('Synthetic retrieval laboratory only: byte integrity is checked, but '
          'records have no native provenance or applicability certificate.')


def _positive(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be an integer in 1..{maximum}')


def _variant(variant):
    if variant not in VARIANTS:
        raise ValueError(f'unknown retrieval lab variant: {variant}')


def build_catalog(directory, records):
    """Create a fresh synthetic v2 database. Never overwrite an existing run.

    The manifest satisfies the existing readers' byte-integrity gate, not the
    native preparation protocol; both manifest and catalog are explicitly marked
    synthetic. Only the ten ordinary premise fields enter the premises table.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / 'parent-premises.sqlite'
    rows = []
    for record in records:
        statement = record['exact_statement']
        rows.append((record['record_id'], record['canonical_name'],
                     record.get('declaration_name', record['canonical_name']),
                     record.get('fact_index', 0), statement,
                     record.get('proposition_digest', 'synthetic-text-sha256:' +
                                hashlib.sha256(statement.encode()).hexdigest()),
                     record.get('owner_theory', 'Synthetic'),
                     record.get('owner_session', 'Synthetic_Lab'),
                     record.get('source_class', 'frozen_parent'),
                     json.dumps(record.get('attributes', []))))
    with closing(sqlite3.connect(database)) as db:
        db.executescript(SCHEMA)
        db.execute('CREATE TABLE laboratory_metadata (value TEXT NOT NULL)')
        db.execute('INSERT INTO laboratory_metadata VALUES (?)',
                   (json.dumps({'synthetic': True, 'native_validated': False, 'notice': NOTICE}),))
        db.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
        db.commit()
    manifest = {'schema_version': 2, 'database_sha256': digest(database),
                'synthetic': True, 'native_validated': False, 'record_count': len(rows),
                'notice': NOTICE}
    (directory / 'parent-premises-manifest.json').write_text(
        json.dumps(manifest, indent=2) + '\n')
    return database


def _encoded_tokens(text):
    # Encode each token as a single ASCII word, so FTS does not tokenize it again
    # (notably apostrophes and underscores). This preserves tokens() equality.
    return ['t' + token.encode('utf-8').hex() for token in tokens(text)]


def prepare_variant(database, variant, directory):
    """Build a version-bound side index only for the normalized FTS variants."""
    _variant(variant)
    database = Path(database).resolve()
    if verify_database(database) != 2:
        raise ValueError('lab strategies require a v2 catalog')
    prepared = {'variant': variant, 'database_sha256': digest(database),
                'synthetic_experiment': True, 'normalizer': None,
                'source_sha256': {
                    'lab_strategies': digest(Path(__file__)),
                    'premise_recall': digest(Path(premise_recall.__file__)),
                    'proof_state_recall': digest(Path(proof_state_recall.__file__))}}
    if variant not in {'normalized_fts', 'combined'}:
        return prepared
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / 'normalized-fts.sqlite'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(path)) as dest:
        dest.execute('CREATE VIRTUAL TABLE normalized_search USING fts5('
                     'record_id UNINDEXED, name_tokens, statement_tokens)')
        dest.executemany('INSERT INTO normalized_search VALUES (?,?,?)',
                         ((rid, ' '.join(_encoded_tokens(name)), ' '.join(_encoded_tokens(statement)))
                          for rid, name, statement in source.execute(
                              'SELECT record_id, canonical_name, exact_statement FROM premises '
                              "WHERE source_class IN ('frozen_parent','retained_semantic_export')")))
        dest.execute('CREATE TABLE metadata(value TEXT NOT NULL)')
        dest.execute('INSERT INTO metadata VALUES (?)', (json.dumps({
            'database_sha256': prepared['database_sha256'], 'normalizer': NORMALIZER}),))
        dest.commit()
    return {**prepared, 'normalizer': NORMALIZER, 'index_path': str(path),
            'index_sha256': digest(path)}


def _catalog_rows(database, ids):
    if not ids:
        return {}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        result = {}
        for row in db.execute('SELECT * FROM premises WHERE record_id IN (' +
                              ','.join('?' for _ in ids) + ')', list(ids)):
            item = dict(row)
            item['attributes'] = json.loads(item.pop('attributes_json'))
            result[item['record_id']] = item
        return result


def _baseline(database, request, budget):
    if request['mode'] == 'goal':
        page = recall_for_proof_state(database, request['query'], limit=budget)
        return page['records'], page['matched_candidates'], page['ranking']
    results, offset, total = [], 0, 0
    while len(results) < budget:
        page = premise_recall.recall_premises(
            database, request['query'], mode=request['mode'], scope='upstream',
            offset=offset, page_size=min(5, budget - len(results)))
        results.extend(page['records'])
        total = page['matched_candidates']
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    return results, total, 'production-' + request['mode']


def _ungated_search(database, query, budget):
    # Reuse production validation and exactly its candidates/forms/score. There
    # is no new OR matching or field weighting in this single-factor ablation.
    premise_recall.recall_premises(database, query, mode='search', scope='upstream')
    query = query.strip()
    single = bool(premise_recall._NAME.fullmatch(query)) or query.startswith('premise:') and ' ' not in query
    symbol = premise_recall._DEF_SUFFIX.sub('', query) if single else None
    terms = list(dict.fromkeys(premise_recall._ATOM.findall(re.sub(r'\\<[^>]+>', ' ', query))))
    sql_terms = list(dict.fromkeys(t for t in ([query, symbol] if single else terms) if t))
    terms = [t for t in terms if t.casefold() not in premise_recall._SYNTAX_WORDS]
    ranked = []
    if sql_terms and terms:
        for item in premise_recall._upstream(database, sql_terms):
            if not _is_public_record(item):
                continue
            names = premise_recall._forms(item['canonical_name'])
            statement = premise_recall._forms(item['exact_statement'])
            matched = [t for t in terms if t in names | statement]
            if len(matched) != len(terms):
                continue
            name_hits = sum(t in names for t in matched)
            ranked.append({**item, 'match_kind': 'search', 'matched_terms': matched,
                           'name_match': bool(name_hits),
                           'score': 100 * len(matched) / len(terms) + 10 * name_hits / len(terms)})
    ranked.sort(key=lambda item: (-item['score'], item['canonical_name'], item['record_id']))
    return ranked[:budget], len(ranked), 'production-search-with-body-only-candidates'


def _normalized_goal(database, query, prepared, budget):
    if not isinstance(query, str) or not query.strip() or len(query) > 200000:
        raise ValueError('a nonempty proof state of at most 200000 characters is required')
    if (not prepared or prepared.get('database_sha256') != digest(database)
            or prepared.get('normalizer') != NORMALIZER):
        raise ValueError('missing or stale normalized FTS preparation')
    path = Path(prepared['index_path']).resolve()
    if digest(path) != prepared['index_sha256']:
        raise ValueError('normalized FTS integrity mismatch')
    terms = sorted(set(tokens(query)))
    scores = {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        metadata = json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
        if (metadata.get('database_sha256') != prepared['database_sha256']
                or metadata.get('normalizer') != NORMALIZER):
            raise ValueError('normalized FTS metadata belongs to another catalog or encoder')
        for start in range(0, len(terms), 64):
            expression = ' OR '.join('"t' + token.encode('utf-8').hex() + '"'
                                     for token in terms[start:start + 64])
            rows = db.execute('SELECT record_id, bm25(normalized_search) AS rank '
                              'FROM normalized_search WHERE normalized_search MATCH ? '
                              'ORDER BY rank, record_id LIMIT 500', (expression,))
            for rank, row in enumerate(rows):
                scores[row['record_id']] = scores.get(row['record_id'], 0) + 1 / (60 + rank)
    ranked = sorted(scores, key=lambda rid: (-scores[rid], rid))[:budget]
    rows = _catalog_rows(database, ranked)
    return [{**rows[rid], 'score': scores[rid], 'match_kind': 'proof_state',
             'availability': 'synthetic_candidate_not_native_validated'} for rid in ranked], \
        len(scores), 'normalized-token-fts5-rrf'


def _group_candidates(records):
    """Collapse identical encoded propositions while retaining distinct origins."""
    groups = {}
    for item in records:
        # Empty/missing digests never cause unrelated records to collapse.
        key = item.get('proposition_digest') or ('record', item['record_id'])
        alias = {k: item[k] for k in ('record_id', 'canonical_name', 'owner_theory',
                                     'owner_session', 'source_class', 'attributes') if k in item}
        if key not in groups:
            groups[key] = {**item, 'alias_ids': [], 'aliases': []}
        groups[key]['alias_ids'].append(item['record_id'])
        groups[key]['aliases'].append(alias)
    return list(groups.values())


def retrieve(database, request, *, variant='baseline', prepared=None,
             limit=5, offset=0, candidate_budget=50):
    """Return a stable page within a common, explicit candidate horizon.

    `candidate_count` is the ranker's reported candidate count, *not* an exact
    total over the entire corpus for goal queries (FTS takes 500 per token chunk).
    `truncated` reports whether this call's candidate horizon has more candidates.
    A null next_offset means exhaustion of that horizon, not of all possible facts.
    """
    _variant(variant)
    _positive(limit, 'limit', 50)
    _positive(candidate_budget, 'candidate_budget', 50)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('offset must be a nonnegative integer')
    if not isinstance(request, dict) or request.get('mode') not in {'exact', 'definition', 'search', 'goal'}:
        raise ValueError('request mode must be exact/definition/search/goal')
    if set(request) - {'mode', 'query'}:
        raise ValueError('request accepts only mode and query; evaluation labels are separate')
    query = request.get('query')
    if not isinstance(query, str) or not query.strip():
        raise ValueError('request query must be a nonempty string')
    database = Path(database).resolve()
    verify_database(database)
    database_sha256 = digest(database)
    if prepared and (prepared.get('database_sha256') != database_sha256
                     or prepared.get('variant') != variant):
        raise ValueError('preparation belongs to a different catalog snapshot or variant')
    mode = request['mode']
    if mode == 'search' and variant in {'body_candidates', 'combined'}:
        records, count, ranking = _ungated_search(database, query, candidate_budget)
    elif mode == 'goal' and variant in {'normalized_fts', 'combined'}:
        records, count, ranking = _normalized_goal(database, query, prepared, candidate_budget)
    else:
        records, count, ranking = _baseline(database, request, candidate_budget)
    catalog = _catalog_rows(database, [item['record_id'] for item in records])
    records = [{**catalog[item['record_id']], **item} for item in records]
    raw_count = len(records)
    if mode in {'search', 'goal'} and variant in {'digest_groups', 'combined'}:
        records = _group_candidates(records)
        ranking += '+digest-groups'
    selected = records[offset:offset + limit]
    next_offset = offset + len(selected) if offset + len(selected) < len(records) else None
    return {'variant': variant, 'request': dict(request), 'records': selected,
            'offset': offset, 'next_offset': next_offset, 'candidate_count': count,
            'candidate_budget': candidate_budget, 'raw_candidates_considered': raw_count,
            'raw_candidate_count': raw_count,
            'groups_within_budget': len(records), 'truncated': count > raw_count,
            'ranking': ranking, 'database_sha256': database_sha256,
            'is_proof_authority': False, 'notice': NOTICE,
            'candidate_count_semantics': ('union-of-top-500-per-token-chunk' if mode == 'goal'
                                          else 'exact-matching-count'),
            'pagination_scope': 'fixed-catalog-and-candidate-budget'}
