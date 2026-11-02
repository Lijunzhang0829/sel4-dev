"""Snapshot-bound candidate indexes and bounded reordering for fact retrieval.

Indexes establish neither theorem validity nor current visibility. The generated
source supplied by the caller must already be the allowed sealed projection.
No query-result cache or learned semantic model is used here.
"""
from contextlib import closing
import errno
import json
from pathlib import Path
import sqlite3
import tempfile

from regen.execution.proof_commits import digest as value_digest
from regen.preparation.premise_database import digest
from regen.planning.premise_retrieval import _is_public_record


POLICY = 'indexed-v1'
LEGACY_POLICY = 'legacy-v1'
DEFAULT_CANDIDATE_BUDGET = 100
MAX_CANDIDATE_BUDGET = 500
GENERATED_INDEX_VERSION = 'sealed-normalized-fts-v1'
UPSTREAM_INDEX_VERSION = 'upstream-normalized-fts-v1'


def validate_policy(policy, candidate_budget):
    if policy not in {LEGACY_POLICY, POLICY}:
        raise ValueError('retrieval_policy must be legacy-v1 or indexed-v1')
    if type(candidate_budget) is not int or not 1 <= candidate_budget <= MAX_CANDIDATE_BUDGET:
        raise ValueError('candidate_budget must be an integer in 1..500')


def encoded_tokens(text):
    from regen.execution.proof_state_recall import tokens
    # FTS sees complete tokens, including primes, instead of tokenizing twice.
    return ['t' + token.encode().hex() for token in tokens(text)]


def _immutable_index_uri(index):
    """Read only the hashed index file, never unbound SQLite sidecar pages."""
    index = Path(index).resolve()
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(index) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('candidate index has an unhashed SQLite sidecar')
    # immutable also closes the race where a sidecar appears after the check.
    # Source databases retain their existing read/verification semantics.
    return index.as_uri() + '?mode=ro&immutable=1'


def certified_generated_records(source):
    for raw in json.loads(Path(source).read_text()).get('records', []):
        if (raw.get('source_class') != 'generated_theorem' or not raw.get('theory_seal_id')
                or raw.get('status') != 'sealed' or not _is_public_record(raw)):
            continue
        item = dict(raw)
        body = item.get('proof_body')
        certified = (isinstance(body, str) and bool(body.strip()) and bool(item.get('proof_commit_id'))
                     and value_digest(body) == item.get('proof_sha256'))
        if not certified:
            item.pop('proof_body', None)
        yield item, certified


def build_generated_index(source, output):
    """Build an immutable index over one caller-provided sealed projection."""
    source, output = Path(source).resolve(), Path(output).resolve()
    source_hash = digest(source)
    output.mkdir(parents=True, exist_ok=False)
    path = output / 'generated.sqlite'
    count = examples = 0
    with closing(sqlite3.connect(path)) as db:
        db.executescript('CREATE TABLE metadata(value TEXT NOT NULL); '
                         'CREATE TABLE records(record_id TEXT PRIMARY KEY, record_json TEXT NOT NULL); '
                         'CREATE VIRTUAL TABLE helper_search USING fts5(record_id UNINDEXED, name, statement, context); '
                         'CREATE VIRTUAL TABLE example_search USING fts5(record_id UNINDEXED, name, statement, context, proof);')
        for item, certified in certified_generated_records(source):
            fields = (item['record_id'], ' '.join(encoded_tokens(item['canonical_name'])),
                      ' '.join(encoded_tokens(item['exact_statement'])),
                      ' '.join(encoded_tokens('\n'.join(item.get('context', [])))))
            db.execute('INSERT INTO records VALUES (?,?)', (item['record_id'], json.dumps(item)))
            db.execute('INSERT INTO helper_search VALUES (?,?,?,?)', fields)
            if certified:
                db.execute('INSERT INTO example_search VALUES (?,?,?,?,?)',
                           (*fields, ' '.join(encoded_tokens(item['proof_body']))))
                examples += 1
            count += 1
        metadata = {'version': GENERATED_INDEX_VERSION, 'source_sha256': source_hash,
                    'record_count': count, 'certified_examples': examples}
        db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata),))
        db.commit()
    if digest(source) != source_hash:
        raise ValueError('generated projection changed during index construction')
    manifest = {**metadata, 'index_sha256': digest(path)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return path


def verify_generated_index(source, index):
    source, index = Path(source).resolve(), Path(index).resolve()
    uri = _immutable_index_uri(index)
    manifest = json.loads((index.parent / 'manifest.json').read_text())
    if (manifest.get('version') != GENERATED_INDEX_VERSION
            or manifest.get('source_sha256') != digest(source)
            or manifest.get('index_sha256') != digest(index)):
        raise ValueError('stale or incompatible generated candidate index')
    with closing(sqlite3.connect(uri, uri=True)) as db:
        metadata = json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
    if metadata != {k: v for k, v in manifest.items() if k != 'index_sha256'}:
        raise ValueError('generated index metadata mismatch')
    return manifest


def generated_index_for(source, directory):
    """Materialize the database index of a source version, never query results."""
    source = Path(source).resolve()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / digest(source)
    index = output / 'generated.sqlite'
    if not output.exists():
        # Concurrent queries may need the same version. Publish only a complete
        # index directory; no reader can observe the builder's partial database.
        with tempfile.TemporaryDirectory(prefix='.generated-index-', dir=directory) as staging:
            candidate = Path(staging) / 'complete'
            build_generated_index(source, candidate)
            try:
                candidate.rename(output)
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
    verify_generated_index(source, index)
    return index


def build_upstream_index(database, output):
    """Preserve full identifiers and components consistently on both sides."""
    from regen.preparation.premise_database import verify_database
    database, output = Path(database).resolve(), Path(output).resolve()
    if verify_database(database) != 2:
        raise ValueError('normalized candidate index requires a verified v2 catalog')
    source_hash = digest(database)
    output.mkdir(parents=True, exist_ok=False)
    path = output / 'upstream.sqlite'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(path)) as dest:
        dest.execute('CREATE TABLE metadata(value TEXT NOT NULL)')
        dest.execute('CREATE VIRTUAL TABLE candidate_search USING fts5(record_id UNINDEXED, name, statement)')
        count = 0
        for rid, name, statement in src.execute(
                "SELECT record_id, canonical_name, exact_statement FROM premises "
                "WHERE source_class IN ('frozen_parent','retained_semantic_export')"):
            dest.execute('INSERT INTO candidate_search VALUES (?,?,?)',
                         (rid, ' '.join(encoded_tokens(name)), ' '.join(encoded_tokens(statement))))
            count += 1
        metadata = {'version': UPSTREAM_INDEX_VERSION, 'source_sha256': source_hash,
                    'record_count': count}
        dest.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata),))
        dest.commit()
    if digest(database) != source_hash:
        raise ValueError('upstream catalog changed during candidate index construction')
    (output / 'manifest.json').write_text(json.dumps({**metadata, 'index_sha256': digest(path)}, indent=2) + '\n')
    return path


def verify_upstream_index(database, index):
    from regen.preparation.premise_database import verify_database
    database, index = Path(database).resolve(), Path(index).resolve()
    uri = _immutable_index_uri(index)
    verify_database(database)
    manifest = json.loads((index.parent / 'manifest.json').read_text())
    if (manifest.get('version') != UPSTREAM_INDEX_VERSION
            or manifest.get('source_sha256') != digest(database)
            or manifest.get('index_sha256') != digest(index)):
        raise ValueError('stale or incompatible upstream candidate index')
    with closing(sqlite3.connect(uri, uri=True)) as db:
        metadata = json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
    if metadata != {key: value for key, value in manifest.items() if key != 'index_sha256'}:
        raise ValueError('upstream index metadata mismatch')
    return manifest


def upstream_index_for(database, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / digest(database)
    index = output / 'upstream.sqlite'
    if not output.exists():
        with tempfile.TemporaryDirectory(prefix='.upstream-index-', dir=directory) as staging:
            candidate = Path(staging) / 'complete'
            build_upstream_index(database, candidate)
            try:
                candidate.rename(output)
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
    verify_upstream_index(database, index)
    return index


def upstream_candidate_scores(database, index, query):
    verify_upstream_index(database, index)
    terms = sorted(set(encoded_tokens(query)))
    scores = {}
    with closing(sqlite3.connect(_immutable_index_uri(index), uri=True)) as db:
        for start in range(0, len(terms), 64):
            expression = ' OR '.join('"' + term + '"' for term in terms[start:start + 64])
            rows = db.execute('SELECT record_id, bm25(candidate_search) AS rank FROM candidate_search '
                              'WHERE candidate_search MATCH ? ORDER BY rank, record_id LIMIT 500', (expression,))
            for rank, (record_id, _) in enumerate(rows):
                scores[record_id] = scores.get(record_id, 0) + 1 / (60 + rank)
    return scores


def generated_candidates(source, index, query, *, purpose='helper', candidate_budget=100):
    validate_policy(POLICY, candidate_budget)
    if purpose not in {'helper', 'proof_example'}:
        raise ValueError('generated candidate purpose must be helper or proof_example')
    if not isinstance(query, str) or not query.strip():
        raise ValueError('generated candidate query must be nonempty')
    manifest = verify_generated_index(source, index)
    table = 'helper_search' if purpose == 'helper' else 'example_search'
    terms = sorted(set(encoded_tokens(query)))
    scores = {}
    with closing(sqlite3.connect(_immutable_index_uri(index), uri=True)) as db:
        for start in range(0, len(terms), 64):
            expression = ' OR '.join('"' + token + '"' for token in terms[start:start + 64])
            rows = db.execute(f'SELECT record_id, bm25({table}) AS rank FROM {table} '
                              f'WHERE {table} MATCH ? ORDER BY rank, record_id LIMIT 500', (expression,))
            for rank, (record_id, _) in enumerate(rows):
                scores[record_id] = scores.get(record_id, 0) + 1 / (60 + rank)
        ids = sorted(scores, key=lambda key: (-scores[key], key))[:candidate_budget]
        records = [json.loads(db.execute('SELECT record_json FROM records WHERE record_id=?', (key,)).fetchone()[0])
                   for key in ids]
    return {'records': records, 'source_sha256': manifest['source_sha256'],
            'index_version': GENERATED_INDEX_VERSION, 'candidate_budget': candidate_budget,
            'indexed_records': manifest['record_count'], 'matched_candidates': len(scores),
            'selected_candidates': len(records), 'candidate_truncated': len(scores) > len(records),
            'ranking': 'normalized-fts5-rrf', 'candidate_count_semantics': 'union-of-top-500-per-token-chunk'}


def reorder_candidates(records, reranker, context):
    """Accept only a permutation of existing candidates, with separate evidence.

Callback signature: reranker(context, records) -> {ordered_record_ids, evidence}.
Evidence is explanatory only; native checking remains proof authority. A callback
cannot create candidates, alter statements, or turn ranking into applicability.
"""
    if reranker is None:
        return list(records), None
    from copy import deepcopy
    result = reranker(deepcopy(context), deepcopy(records))
    if not isinstance(result, dict) or set(result) - {'ordered_record_ids', 'evidence'}:
        raise ValueError('invalid candidate reranker result')
    ids = result.get('ordered_record_ids')
    by_id = {record['record_id']: record for record in records}
    if (len(by_id) != len(records) or not isinstance(ids, list) or len(ids) != len(records)
            or len(set(ids)) != len(ids) or set(ids) != set(by_id)):
        raise ValueError('candidate reranker must return an exact permutation of bounded candidates')
    evidence = result.get('evidence', {})
    if not isinstance(evidence, dict) or not set(evidence) <= set(by_id):
        raise ValueError('candidate reranker evidence references unknown candidates')
    return [dict(by_id[key], **({'rerank_evidence': deepcopy(evidence[key])} if key in evidence else {}))
            for key in ids], {'policy': 'bounded-candidate-permutation-v1',
                             'examined_candidates': len(records), 'evidence': deepcopy(evidence),
                             'is_proof_authority': False}
