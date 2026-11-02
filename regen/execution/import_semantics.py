"""Native, proof-free comparison of fixed targets across an import revision."""
from copy import deepcopy
import hashlib
import json
import re

from regen.execution.proof_commits import freeze_evidence

MARKER = 'REGEN_IMPORT_TARGET '
POLICY = 'typed-fixed-targets-v1'


def observation_source(package, imports):
    from regen.execution.isabelle_node_checker import wrap_context
    blocks = []
    for contract in package['contracts']:
        key = contract['contract_id'].encode().hex()
        # Only trusted code is executable ML. Goal text is the frozen statement.
        observe = r'''ML_val \<open>
          val {context = ctxt, goal, ...} = @{Isar.goal};
          val payload = XML.Encode.pair
            (XML.Encode.list Term_XML.Encode.term_raw)
            (XML.Encode.list Term_XML.Encode.term_raw)
            (Thm.prems_of goal, map Thm.term_of (Assumption.all_assms_of ctxt));
          val identity = SHA1.rep (SHA1.digest (YXML.string_of_body payload));
          val _ = writeln (PREFIX ^ identity);
        \<close>'''.replace('PREFIX', json.dumps(MARKER + key + ' '))
        blocks.append(wrap_context(contract.get('contract_owned_context', []),
            contract['exact_statement'] + '\n' + observe + '\noops'))
    return ('theory Import_Semantics\nimports ' + ' '.join(json.dumps(i) for i in imports)
            + '\nbegin\nunbundle l4v_word_context\n\n' + '\n\n'.join(blocks) + '\nend\n')


def records_from_log(log, package):
    rows = {}
    for key, identity in re.findall(r'^' + MARKER + r'([0-9a-f]+) ([0-9a-f]{40})\s*$', log, re.M):
        key = bytes.fromhex(key).decode()
        if key in rows:
            raise ValueError('duplicate import target observation')
        rows[key] = identity
    if set(rows) != {c['contract_id'] for c in package['contracts']}:
        raise ValueError('import target observation coverage mismatch')
    return rows


def _observe(checker, package):
    imports = list(checker.imports())
    source = observation_source(package, imports)
    check = freeze_evidence(checker._check(source, 'Import_Semantics'))
    result = {'imports': imports, 'check': check}
    if check.get('verdict') == 'green' and check.get('returncode') == 0:
        result['records'] = records_from_log(check.get('log_content', ''), package)
    return result


def check_import_semantics(before_checker, after_checker, package):
    before = _observe(before_checker, package)
    if 'records' not in before:
        return {'policy': POLICY, 'valid': False, 'stage': 'previous-context', 'before': before}
    after = _observe(after_checker, package)
    valid = after.get('records') == before['records']
    result = {'policy': POLICY, 'valid': valid, 'before': before, 'after': after}
    if not valid:
        result['stage'] = 'changed-target-meaning' if 'records' in after else 'candidate-context'
        result['changed_contract_ids'] = [key for key, value in before['records'].items()
            if after.get('records', {}).get(key) != value]
    return result


def validate_import_semantics(evidence, package, before_imports, after_imports):
    if evidence.get('policy') != POLICY or evidence.get('valid') is not True:
        raise ValueError('imports require native fixed-target identity evidence')
    rows = []
    for field, imports in (('before', before_imports), ('after', after_imports)):
        item = evidence.get(field, {})
        check = item.get('check', {})
        if (item.get('imports') != list(imports) or check.get('verdict') != 'green'
                or type(check.get('returncode')) is not int or check['returncode'] != 0
                or check.get('source_content') != observation_source(package, imports)):
            raise ValueError('import semantic observation context/source mismatch')
        for name in ('source', 'log'):
            content = check.get(name + '_content')
            if (not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest()
                    != check.get(name + '_sha256')):
                raise ValueError('import semantic observation bytes mismatch')
        records = records_from_log(check['log_content'], package)
        if item.get('records') != records:
            raise ValueError('import semantic observation records mismatch')
        rows.append(records)
    if rows[0] != rows[1]:
        raise ValueError('import revision changes fixed target meanings')
    return deepcopy(rows[0])
