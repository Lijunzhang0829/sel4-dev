"""Minimal, immutable premise catalog with fail-closed native source validation.

The TSV witness is produced by premise_source_evidence against the sealed context.
Dynamic collections are quarantined: their certificates do not identify origins.
"""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

SCHEMA = '''
PRAGMA user_version=2;
CREATE TABLE premises (
 record_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL,
 declaration_name TEXT NOT NULL, fact_index INTEGER NOT NULL CHECK(fact_index>=0),
 exact_statement TEXT NOT NULL, proposition_digest TEXT NOT NULL,
 owner_theory TEXT NOT NULL, owner_session TEXT NOT NULL,
 source_class TEXT NOT NULL CHECK(source_class IN ('frozen_parent','retained_semantic_export')),
 attributes_json TEXT NOT NULL,
 UNIQUE(declaration_name,fact_index));
CREATE INDEX premise_name ON premises(canonical_name);
CREATE VIRTUAL TABLE premise_search USING fts5(canonical_name, exact_statement,
 content='premises', content_rowid='rowid', tokenize="unicode61 tokenchars '_'");
'''


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_database(path):
    """Validate v2 catalog bytes; legacy databases remain readable for old runs."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'premise catalog unavailable: {path}')
    manifest_path = path.parent / 'parent-premises-manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
    if version == 2 or manifest.get('schema_version') == 2:
        if version != 2 or manifest.get('schema_version') != 2 or digest(path) != manifest.get('database_sha256'):
            raise ValueError('premise database integrity mismatch')
    return version


def read_evidence(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if digest(directory / 'facts.tsv') != manifest['facts_sha256']:
        raise ValueError('native evidence integrity mismatch')
    from regen.preparation.premise_source_evidence import SOURCE
    if (digest(directory / 'Premise_Source_Evidence.thy') != manifest['source_sha256']
            or manifest['source_sha256'] != hashlib.sha256(SOURCE.encode()).hexdigest()):
        raise ValueError('native exporter integrity mismatch')
    theories, facts = {}, {}
    for line in (directory / 'facts.tsv').read_text().splitlines():
        fields = line.split('\t')
        if fields[0] == 'THEORY':
            _, theory, session = fields
            if theory in theories and theories[theory] != session:
                raise ValueError('ambiguous registered theory')
            theories[theory] = session
        elif fields[0] == 'FACT':
            _, name, index, text_hash, theory, dynamic, hyps, typed_hash = fields
            key = (name, int(index))
            if key in facts:
                raise ValueError('duplicate native fact witness')
            facts[key] = (text_hash, theory, dynamic == 'true', int(hyps), typed_hash)
        else:
            raise ValueError('invalid native evidence row')
    return manifest, theories, facts


def validated_records(records, theories, facts, upstream):
    accepted, rejected = [], []
    from regen.preparation.planning_input import _validate_data_only
    for item in records:
        _validate_data_only(item)
        key = (item['declaration_name'], item['fact_index'])
        witness = facts.get(key)
        reason = None
        expected_name = key[0] if key[1] == 0 else f'{key[0]}({key[1] + 1})'
        if item['canonical_name'] != expected_name:
            reason = 'canonical_name_mismatch'
        elif '??.' in item['canonical_name'] or '??.' in item['exact_statement'] or '__internal__' in item['canonical_name']:
            reason = 'private_fact'
        elif witness is None:
            reason = 'unresolved_fact'
        else:
            text_hash, theory, dynamic, hyps, typed_hash = witness
            session = theories.get(theory)
            source = item.get('source_class')
            if dynamic:
                reason = 'dynamic_collection_origin_unresolved'
            elif not session or theory.rsplit('.', 1)[-1] in {'Premise_Catalog_Export', 'Semantic_Root'}:
                reason = 'unregistered_or_wrapper_theory'
            elif item['owner_theory'] not in {theory, theory.rsplit('.', 1)[-1]}:
                reason = 'owner_certificate_mismatch'
            elif hashlib.sha1(item['exact_statement'].encode()).hexdigest() != text_hash:
                reason = 'statement_mismatch'
            elif hyps:
                reason = 'hidden_hypotheses'
            elif not ((source == 'frozen_parent' and session in upstream and session not in {'Refine', 'CRefine'})
                      or (source == 'retained_semantic_export' and session == 'Semantic_Base')):
                reason = 'illegal_source_session'
        if reason:
            rejected.append({'record_id': item['record_id'], 'declaration_name': key[0],
                             'fact_index': key[1], 'reason': reason})
            continue
        accepted.append({'record_id': item['record_id'], 'canonical_name': item['canonical_name'],
                         'declaration_name': key[0], 'fact_index': key[1],
                         'exact_statement': item['exact_statement'],
                         'proposition_digest': 'isabelle-term-yxml-sha1:' + typed_hash,
                         'owner_theory': theory, 'owner_session': session,
                         'source_class': source, 'attributes_json': json.dumps(item.get('attributes', []))})
    return accepted, rejected


def rebuild(dataset, export, evidence, output):
    from regen.preparation.refine_campaign import sha256_json
    dataset, export, output = Path(dataset), Path(export), Path(output)
    planning = json.loads((dataset / 'repository-planning-input.json').read_text())
    witness, theories, facts = read_evidence(evidence)
    from regen.preparation.checker_context import load_context
    context = load_context(dataset)
    if witness['checker_context_id'] != context['checker_context_id']:
        raise ValueError('evidence belongs to another checker context')
    records, quarantine = validated_records(json.loads(export.read_text())['records'], theories, facts,
                                            set(planning['upstream_session_closure']))
    if not records:
        raise ValueError('no trusted premises')
    output.mkdir(parents=True, exist_ok=False)
    path = output / 'parent-premises.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)',
                       [tuple(r.values()) for r in records])
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
        db.commit()
    audit = dict(Counter(r['reason'] for r in quarantine))
    (output / 'quarantine.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in quarantine))
    manifest = {'schema_version': 2, 'artifact_type': 'parent-premises-manifest',
                'record_count': len(records), 'database_sha256': digest(path),
                'source_export_sha256': digest(export), 'native_evidence': witness,
                'source_policy': 'registered-static-sealed-context-v2',
                'quarantine_counts': audit, 'quarantine_sha256': digest(output / 'quarantine.jsonl'),
                'context_profile_ids': [p['context_profile_id'] for p in planning['context_profiles']]}
    planning['parent_premises'].update(record_count=len(records),
        record_ids_sha256=sha256_json(sorted(r['record_id'] for r in records)),
        database_sha256=manifest['database_sha256'], query_interface='proof-state-recall-v2',
        selection_audit={'source_records':len(records)+len(quarantine), 'selected_records':len(records), **audit})
    planning.pop('planning_input_id', None)
    planning['planning_input_id'] = 'refine-planning-input:' + sha256_json(planning)[:20]
    manifest['planning_input_id'] = planning['planning_input_id']
    for name, data in [('repository-planning-input.json', planning), ('parent-premises-manifest.json', manifest)]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'export', 'evidence', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(rebuild(a.dataset, a.export, a.evidence, a.output), indent=2))
