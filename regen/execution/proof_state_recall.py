"""Explicit proof-state retrieval over a verified catalog, with optional local vectors.

Like stepwise's state-driven retrieval, the query is the current goal and hypotheses.
This implementation ranks catalog candidates; Isabelle remains the availability and
proof authority. Hash vectors are lexical features, not learned semantic embeddings.
"""
import argparse
import hashlib
import heapq
import json
import math
import re
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

from regen.preparation.premise_database import digest, verify_database

FIELDS = ('canonical_name', 'exact_statement')


def tokens(text):
    # Preserve Isabelle identifiers, and also expose underscore components.
    text = re.sub(r'\\<([^>]+)>', r' \1 ', text)
    atoms = re.findall(r"[A-Za-z][A-Za-z0-9_']*", text.lower())
    return [t for atom in atoms for t in dict.fromkeys([atom, *atom.split('_')]) if len(t) > 1]


def vector(text, dimensions):
    counts = {}
    for token in tokens(text):
        key = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], 'little') % dimensions
        counts[key] = counts.get(key, 0) + 1
    counts = {str(k): 1 + math.log(v) for k, v in counts.items()}
    norm = math.sqrt(sum(v*v for v in counts.values())) or 1
    return {k: v/norm for k, v in counts.items()}


def build_vectors(database, output, *, fields=FIELDS, dimensions=65536):
    if not fields or len(set(fields)) != len(fields) or not set(fields) <= set(FIELDS):
        raise ValueError('vector fields must be canonical_name and/or exact_statement')
    if not isinstance(dimensions, int) or not 256 <= dimensions <= 1048576:
        raise ValueError('invalid vector dimensions')
    database, output = Path(database).resolve(), Path(output)
    if verify_database(database) != 2:
        raise ValueError('vectors require a verified v2 database')
    if output.exists():
        raise ValueError('refusing to overwrite frozen vector index')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(output)) as dst:
        dst.executescript('CREATE TABLE metadata(value TEXT NOT NULL); CREATE TABLE vectors(record_id TEXT PRIMARY KEY, vector_blob BLOB NOT NULL);')
        metadata = {'encoder':'sha256-feature-hashing-logtf-l2-v1', 'dimensions':dimensions,
                    'fields':list(fields), 'database_sha256':digest(database)}
        dst.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata),))
        dst.executemany('INSERT INTO vectors VALUES (?,?)',
            ((r[0],b''.join(struct.pack('<If',int(k),v) for k,v in vector(' '.join(r[1:]),dimensions).items()))
             for r in src.execute('SELECT record_id,'+','.join(fields)+' FROM premises')))
        dst.commit()
    output.with_suffix(output.suffix+'.sha256').write_text(digest(output)+'\n')
    return metadata


def recall_for_proof_state(database, proof_state, *, limit=5, vector_index=None, attributes=()):
    if not isinstance(proof_state, str) or not proof_state.strip() or len(proof_state) > 200000:
        raise ValueError('a nonempty proof state of at most 200000 characters is required')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError('recall limit must be 1..50')
    database = Path(database).resolve()
    if verify_database(database) != 2:
        raise ValueError('proof-state recall requires a verified v2 catalog')
    terms = sorted(set(tokens(proof_state)))
    scores = {}
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        # Batch long states instead of truncating propositions or identifiers.
        for start in range(0, len(terms), 64):
            query = ' OR '.join('"'+t.replace('"','""')+'"' for t in terms[start:start+64])
            rows = db.execute('SELECT p.record_id, bm25(premise_search) AS rank FROM premise_search '
                              'JOIN premises p ON p.rowid=premise_search.rowid WHERE premise_search MATCH ? '
                              "AND p.source_class IN ('frozen_parent','retained_semantic_export') ORDER BY rank, p.record_id LIMIT 500", (query,))
            for rank, row in enumerate(rows):
                scores[row['record_id']] = scores.get(row['record_id'],0) + 1/(60+rank)
        if vector_index:
            path = Path(vector_index).resolve()
            if digest(path) != path.with_suffix(path.suffix+'.sha256').read_text().strip():
                raise ValueError('vector index integrity mismatch')
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as vectors:
                meta = json.loads(vectors.execute('SELECT value FROM metadata').fetchone()[0])
                if meta['database_sha256'] != digest(database) or meta['encoder'] != 'sha256-feature-hashing-logtf-l2-v1':
                    raise ValueError('incompatible or stale vector index')
                q = vector(proof_state, meta['dimensions'])
                ranked = heapq.nlargest(500, ((sum(q.get(str(k),0)*v for k,v in struct.iter_unpack('<If',raw)), rid)
                    for rid,raw in vectors.execute('SELECT record_id,vector_blob FROM vectors')))
                for rank,(score,rid) in enumerate(ranked):
                    if score > 0:
                        scores[rid] = scores.get(rid,0) + 1/(60+rank)
        selected=[]
        for rid,score in sorted(scores.items(),key=lambda kv:(-kv[1],kv[0])):
            row=db.execute("SELECT * FROM premises WHERE record_id=? AND source_class IN ('frozen_parent','retained_semantic_export')",(rid,)).fetchone()
            if row is None: continue
            item=dict(row)
            item['attributes']=json.loads(item.pop('attributes_json'))
            if not set(attributes)<=set(item['attributes']): continue
            selected.append({**item,'score':score,'match_kind':'proof_state',
                             'availability':'sealed_base_candidate_requires_current_context_check'})
            if len(selected)==limit: break
    return {'query':proof_state, 'scope':'upstream', 'mode':'proof_state', 'effective_mode':'proof_state',
            'status':'matched' if selected else 'no_match', 'records':selected,
            'matched_candidates':len(scores), 'ranking':'fts5-rrf'+('+hash-vector-rrf' if vector_index else ''),
            'proof_state_sha256':hashlib.sha256(proof_state.encode()).hexdigest()}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--proof-state',type=Path)
    p.add_argument('--vector-index',type=Path)
    p.add_argument('--build-vectors',type=Path)
    p.add_argument('--fields',nargs='+',default=list(FIELDS))
    a=p.parse_args()
    if a.build_vectors:
        result=build_vectors(a.database,a.build_vectors,fields=a.fields)
    elif a.proof_state:
        result=recall_for_proof_state(a.database,a.proof_state.read_text(),vector_index=a.vector_index)
    else: p.error('provide --proof-state or --build-vectors')
    print(json.dumps(result,ensure_ascii=False,indent=2))
