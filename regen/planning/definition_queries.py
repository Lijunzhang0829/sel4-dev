"""Paged definition candidates from the already verified premise database.

Names identify candidates, not certified defining equations. Lexical identifiers
in a proposition are navigation aids, never a function-call or proof graph.
"""
import re
import sqlite3


def _literal_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def definition_ids(database, terms, limit, offset):
    clauses = ['(canonical_name LIKE ? ESCAPE \'\\\' OR declaration_name LIKE ? ESCAPE \'\\\')'] * len(terms)
    params = [x for term in terms for x in ('%' + _literal_like(term) + '%',) * 2]
    # Public definition/equation names only. Private __internal__ rows have
    # already been quarantined by preparation and cannot be reintroduced here.
    equation = "(canonical_name LIKE '%\\_def%' ESCAPE '\\' OR canonical_name LIKE '%.simps%' OR canonical_name LIKE '%.equations%')"
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        rows = db.execute('SELECT record_id FROM premises WHERE (' + ' OR '.join(clauses) +
                          ') AND ' + equation + ' ORDER BY canonical_name, record_id LIMIT ? OFFSET ?',
                          [*params, limit + 1, offset]).fetchall()
    return [r[0] for r in rows[:limit]], offset + limit if len(rows) > limit else None


def mentioned_identifiers(record):
    # Avoid turning Isabelle symbol escapes (e.g. \<Rightarrow>) into queries.
    text = re.sub(r'\\<[^>]+>', ' ', record['exact_statement'])
    return sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_'.]*", text)))
