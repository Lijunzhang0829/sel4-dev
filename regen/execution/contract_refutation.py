"""Isolated, kernel-audited refutation of a fixed HOL contract.

A refutation is not a positive GREEN proof or a seal.  Only the frozen parent's
and already-sealed imports enter the checker; neither the current draft nor
unsealed helper proofs are used. Native goal construction (not text rewriting)
negates the universally closed statement including its own assumptions.

Owned contexts are used only to elaborate and export the complete target. The
negative proof runs after leaving them, without their local assumptions/facts.
Legacy context-free certificates retain their original renderer and support.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
import xml.etree.ElementTree as ET

from regen.execution.check_tasks import check_cancelled
from regen.execution.native_check_receipt import checker_binding
from regen.execution.proof_batches import _safe_text, classify_native_failure
from regen.execution.proof_commits import digest, durable

SCHEMA = 'contract-refutation-v2'
LEGACY_SCHEMA = 'contract-refutation-v1'
SUPPORT = Path(__file__).with_suffix('.ML')
LEGACY_SUPPORT = Path(__file__).with_name('contract_refutation_v1.ML')
DECLARATION = re.compile(r"\A\s*(?:lemma|theorem|corollary|proposition)\s+([A-Za-z][A-Za-z0-9_'.]*)\b")


class UnsupportedRefutation(ValueError):
    """Cannot safely construct the negative goal for this declaration form."""


def _sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def contract_binding(contract):
    if not isinstance(contract, dict):
        raise ValueError('contract must be a frozen contract record')
    key = contract.get('contract_id', contract.get('id'))
    statement = contract.get('exact_statement', contract.get('statement'))
    context = contract.get('contract_owned_context', contract.get('context', []))
    if not isinstance(key, str) or not key or not isinstance(statement, str) or not statement:
        raise ValueError('missing fixed contract identity/statement')
    if not isinstance(context, list) or any(not isinstance(x, str) for x in context):
        raise ValueError('invalid fixed contract context')
    return {'contract_id':key, 'exact_statement':statement, 'context':deepcopy(context)}


def refutation_capability(contract):
    """Expose the supported declaration scope without starting a native check."""
    binding = contract_binding(contract)
    if not DECLARATION.match(binding['exact_statement']):
        return {'can_attempt':False, 'reason':'declaration_form',
                'diagnostic':'refutation supports one ordinary named theorem declaration'}
    return {'can_attempt':True,
            'reason':'contextual_target_export' if binding['context'] else 'ordinary_context_free_declaration',
            'context_export_required':bool(binding['context']),
            'notice':'The native parser must export all owned assumptions and parameters, then the kernel checks the closed negation outside that context.'}


def render_refutation(contract, proof_body, imports, *, theory_name, audit_path, _schema=SCHEMA):
    capability = refutation_capability(contract)
    if not capability['can_attempt']:
        raise UnsupportedRefutation(capability['diagnostic'])
    binding = contract_binding(contract)
    if _schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError('unsupported refutation schema')
    if _schema == LEGACY_SCHEMA and binding['context']:
        raise UnsupportedRefutation('legacy refutation cannot export owned contexts')
    statement = binding['exact_statement']
    match = DECLARATION.match(statement)
    if not isinstance(proof_body, str) or not proof_body.strip():
        raise ValueError('refutation requires a complete proof body')
    if not re.fullmatch(r'Refutation_[0-9a-f]{32}', theory_name):
        raise ValueError('invalid isolated refutation theory name')
    if not isinstance(imports, list) or not imports or any(not isinstance(x, str) or not x for x in imports):
        raise ValueError('refutation requires fixed checker imports')
    _safe_text(statement, statement=True)
    _safe_text(proof_body)
    if re.search(r'\bregen_refute(?:_[A-Za-z0-9_]+)?\b', proof_body):
        raise ValueError('nested refutation commands are forbidden')
    # Replacing only the theorem command and binding does not negate any text.
    # The fixed declaration tail is interpreted by the trusted Isabelle parser.
    command = 'regen_refute' if _schema == LEGACY_SCHEMA else 'regen_refute_capture'
    declaration = (command + ' ' + json.dumps(binding['contract_id']) +
                   ' refuted_contract' + statement[match.end():])
    support_path = LEGACY_SUPPORT if _schema == LEGACY_SCHEMA else SUPPORT
    support = support_path.read_text().replace('REGEN_AUDIT_PATH', json.dumps(audit_path))
    keywords = ('keywords "regen_refute" :: thy_goal' if _schema == LEGACY_SCHEMA else
                'keywords "regen_refute_capture" :: thy_decl\n  and "regen_refute_exported" :: thy_goal')
    if _schema == SCHEMA:
        from regen.execution.isabelle_node_checker import wrap_context
        declaration = (wrap_context(binding['context'], declaration) + '\n\n'
                       + 'regen_refute_exported ' + json.dumps(binding['contract_id']) + ' refuted_contract')
    notation = ('' if _schema == LEGACY_SCHEMA else
        'local_setup \\<open>fn lthy =>\n'
        '  (case try (Bundle.check lthy) ("l4v_word_context", Position.none) of\n'
        '    SOME name => Bundle.unbundle [name] lthy | NONE => lthy)\\<close>\n\n')
    return ('theory ' + theory_name + '\nimports ' + ' '.join(json.dumps(x) for x in imports)
            + '\n' + keywords + '\nbegin\n\n' + notation + 'ML \\<open>\n'
            + support + '\n\\<close>\n\n' + declaration + '\n' + proof_body.strip()
            + '\n\nend\n')


def _audit(raw, contract_id, schema=LEGACY_SCHEMA):
    root = ET.fromstring(raw)
    version = '2' if schema == SCHEMA else '1'
    if root.tag != 'contract_refutation' or root.attrib != {'version':version, 'contract_id':contract_id}:
        raise ValueError('refutation audit identity mismatch')
    expected = {'skip_proof':'false', 'oracles':'0', 'hypotheses':'0',
                'changed_axioms':'false', 'exact_closed_negation':'true'}
    fields = {child.tag: child.text or '' for child in root if child.tag != 'dependencies'}
    if schema == SCHEMA:
        expected['context_exported'] = 'true'
    if len(root) != (10 if schema == SCHEMA else 8) or any(fields.get(key) != value for key, value in expected.items()):
        raise ValueError('refutation kernel audit is incomplete or unsafe')
    if schema == SCHEMA and not fields.get('closed_target_term'):
        raise ValueError('refutation audit lacks exported target binding')
    if not fields.get('expected_term') or not fields.get('proved_term'):
        raise ValueError('refutation audit lacks native proposition binding')
    deps = root.find('dependencies')
    if deps is None or any(x.tag != 'fact' or set(x.attrib) != {'name','index'} for x in deps):
        raise ValueError('invalid refutation dependency audit')
    return {**fields, 'dependencies':[dict(x.attrib) for x in deps]}


def check_export_scan(checker, contracts, output_dir):
    """Elaborate/export fixed targets without attempting or installing proofs."""
    from collections import defaultdict
    from regen.execution.isabelle_node_checker import wrap_context
    if not contracts or len({contract_binding(c)['contract_id'] for c in contracts}) != len(contracts):
        raise ValueError('export scan requires a nonempty unique fixed contract set')
    output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=False)
    first=contract_binding(contracts[0]); label='Refutation_'+uuid4().hex
    # The canonical v2 preamble is identical to the proof checker. Capture
    # commands install no facts, so equal owned contexts can safely be shared.
    context_free={**first,'context':[]}
    template=render_refutation(context_free,'by fail',list(checker.imports()),
        theory_name=label,audit_path=str(output_dir/'unused-audit.xml'))
    marker='\n\nregen_refute_capture '+json.dumps(first['contract_id'])+' '
    preamble=template.split(marker,1)[0]
    groups=defaultdict(list)
    for contract in contracts:
        binding=contract_binding(contract)
        if not refutation_capability(contract)['can_attempt']:
            raise UnsupportedRefutation('export scan contains unsupported declaration')
        statement=binding['exact_statement'];_safe_text(statement,statement=True)
        declaration=('regen_refute_capture '+json.dumps(binding['contract_id'])+
                     ' refuted_contract'+statement[DECLARATION.match(statement).end():])
        groups[tuple(binding['context'])].append(declaration)
    source=preamble+'\n\n'+'\n\n'.join(wrap_context(list(context),'\n\n'.join(items))
        for context,items in groups.items())+'\n\nend\n'
    native=classify_native_failure(deepcopy(checker._check(source,label)))
    actual=Path(native['source']).read_text();log=Path(native['log']).read_text()
    if actual!=source:raise ValueError('native export scan source changed')
    records={}
    for identity,target in re.findall(r'^REGEN_REFUTATION_TARGET ([0-9a-f]+) ([0-9a-f]{40})$',log,re.M):
        identity=bytes.fromhex(identity).decode()
        if identity in records:raise ValueError('duplicate native export observation')
        records[identity]=target
    expected={contract_binding(c)['contract_id'] for c in contracts}
    passed=(native.get('verdict')=='green' and native.get('returncode')==0 and set(records)==expected)
    result={'policy':'contextual-refutation-export-scan-v1','passed':passed,
            'contracts':len(contracts),'exported_targets':len(records),'records':records,
            'owned_context_groups':len(groups),'benchmark_contracts_proved':0,'model_calls':0,
            'source_sha256':_sha(source),'log_sha256':_sha(log),
            'support_sha256':_sha(SUPPORT.read_bytes()),'native_check':native}
    durable(output_dir/'result.json',result,immutable=True)
    return result


def validate_refutation_certificate(certificate, contract, checker=None, *, expected_imports=None):
    """Offline replay of a *trusted-produced* certificate, not a new proof check.

    Hashes detect rebinding/tampering; they do not authenticate arbitrary model
    JSON. The caller must own native execution and durable acceptance.
    """
    c = certificate
    if not isinstance(c, dict) or c.get('schema') not in {SCHEMA,LEGACY_SCHEMA} or c.get('status') != 'refuted':
        raise ValueError('invalid refutation certificate')
    if c.get('contract') != contract_binding(contract) or c.get('contract_sha256') != digest(c['contract']):
        raise ValueError('refutation fixed contract binding mismatch')
    support_path = LEGACY_SUPPORT if c['schema'] == LEGACY_SCHEMA else SUPPORT
    if c.get('support_sha256') != _sha(support_path.read_bytes()):
        raise ValueError('refutation verifier implementation mismatch')
    if checker is not None:
        if c.get('checker_binding_sha256') != checker_binding(checker):
            raise ValueError('refutation checker binding mismatch')
        expected_imports = list(checker.imports())
    if expected_imports is not None and c.get('imports') != list(expected_imports):
        raise ValueError('refutation import binding mismatch')
    source = render_refutation(contract, c['proof_body'], c['imports'],
                               theory_name=c['theory_name'], audit_path=c['audit_path'], _schema=c['schema'])
    for name, value in [('source',source), ('proof_body',c['proof_body']),
                        ('log',c['log']), ('audit_xml',c['audit_xml'])]:
        if c.get(name) != value or c.get(name+'_sha256') != _sha(value):
            raise ValueError('refutation evidence hash mismatch: '+name)
    if c.get('returncode') != 0 or c.get('native_verdict') != 'green':
        raise ValueError('refutation did not finish its native check')
    if c.get('audit') != _audit(c['audit_xml'], c['contract']['contract_id'], c['schema']):
        raise ValueError('refutation audit projection mismatch')
    payload = {k:v for k,v in c.items() if k != 'certificate_id'}
    if c.get('certificate_id') != 'refutation:'+digest(payload):
        raise ValueError('refutation certificate identity mismatch')
    return True


def check_refutation(checker, contract, proof_body, output_dir):
    """Check a model-supplied negative proof without changing positive state."""
    binding = contract_binding(contract)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    label = 'Refutation_' + uuid4().hex
    imports = list(checker.imports())
    # Local test transports can use a direct path; production uses the native
    # container's writable output mount. This changes I/O only, not the goal.
    audit_path = str(output_dir/'refutation.xml') if getattr(checker, 'refutation_local_io', False) else '/audit-output/refutation.xml'
    try:
        source = render_refutation(contract, proof_body, imports,
                                   theory_name=label, audit_path=audit_path)
    except UnsupportedRefutation as error:
        result = {'status':'unsupported', 'diagnostic':str(error), 'certificate':None}
        durable(output_dir/'result.json', result, immutable=True)
        return result
    durable(output_dir/'request.json', {'contract':binding,'contract_sha256':digest(binding),
        'proof_body_sha256':_sha(proof_body), 'imports':imports,
        'checker_binding_sha256':checker_binding(checker)}, immutable=True)
    check_cancelled()
    native = classify_native_failure(deepcopy(checker._check(
        source, label, writable_mounts=[(output_dir, '/audit-output')])))
    check_cancelled()
    actual_source = Path(native['source']).read_text()
    log = Path(native['log']).read_text()
    if actual_source != source:
        raise ValueError('native refutation source changed')
    if (native.get('framework_blocked') or native.get('verdict') == 'infrastructure_error'
            or native.get('failure_scope') in {'native_preparation','trusted_audit_target'}):
        result = {'status':'infrastructure_error', 'framework_blocked':True,
                  'failure_scope':native.get('failure_scope') or 'native_preparation',
                  'diagnostic':native.get('diagnostic', log), 'certificate':None,
                  'native_check':native, 'source_sha256':_sha(source), 'log_sha256':_sha(log)}
        if native.get('native_failure_stage'):
            result['native_failure_stage']=native['native_failure_stage']
        durable(output_dir/'result.json', result, immutable=True)
        return result
    if native.get('returncode') != 0 or native.get('verdict') != 'green':
        unsupported = 'REGEN_REFUTATION_UNSUPPORTED:' in log
        result = {'status':'unsupported' if unsupported else 'not_refuted',
                  'diagnostic':native.get('diagnostic', log), 'certificate':None,
                  'native_check':native, 'source_sha256':_sha(source), 'log_sha256':_sha(log)}
        durable(output_dir/'result.json', result, immutable=True)
        return result
    audit_file = output_dir/'refutation.xml'
    if not audit_file.is_file():
        raise ValueError('successful native process lacks refutation audit')
    xml = audit_file.read_text()
    certificate = {'schema':SCHEMA,'status':'refuted','contract':binding,
        'contract_sha256':digest(binding),'theory_name':label,'audit_path':audit_path,
        'imports':imports,'checker_binding_sha256':checker_binding(checker),
        'support_sha256':_sha(SUPPORT.read_bytes()),'proof_body':proof_body,
        'source':source,'log':log,'audit_xml':xml,
        'returncode':native['returncode'],'native_verdict':native['verdict'],
        'audit':_audit(xml,binding['contract_id'],SCHEMA), 'native_receipt':native.get('native_receipt')}
    for name in ('source','proof_body','log','audit_xml'):
        certificate[name+'_sha256'] = _sha(certificate[name])
    certificate['certificate_id'] = 'refutation:'+digest(certificate)
    validate_refutation_certificate(certificate, contract, checker)
    check_cancelled()
    durable(output_dir/'certificate.json', certificate, immutable=True)
    result = {'status':'refuted','certificate':certificate,
              'diagnostic':'The kernel checked the negation of the closed fixed contract; no positive GREEN or seal is published.'}
    durable(output_dir/'result.json', result, immutable=True)
    return result
