"""Isolated, kernel-audited refutation of a fixed HOL contract.

A refutation is not a positive GREEN proof or a seal.  Only the frozen parent's
and already-sealed imports enter the checker; neither the current draft nor
unsealed helper proofs are used. Native goal construction (not text rewriting)
negates the universally closed statement including its own assumptions.

Owned contexts are used only to elaborate and export the complete target. The
negative proof runs after leaving them, without their local assumptions/facts.
Legacy context-free certificates retain their original renderer and support.
The opt-in owned-context policy instead exports E ==> not (ALL x. A x --> Q x).
Its distinct certificate does not establish E or change legacy refutation counts.
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
OWNED_CONTEXT_SCHEMA = 'contract-refutation-owned-context-v1'
OWNED_CONTEXT_POLICY = 'owned-context-outcomes-v1'
STRICT_GLOBAL_SCOPE = 'strict-global-v1'
SUPPORT = Path(__file__).with_suffix('.ML')
LEGACY_SUPPORT = Path(__file__).with_name('contract_refutation_v1.ML')
OWNED_CONTEXT_SUPPORT = Path(__file__).with_name('contract_refutation_owned_context.ML')
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


def _schema_for_scope(result_scope):
    if result_scope in (None, STRICT_GLOBAL_SCOPE):
        return SCHEMA
    if result_scope == OWNED_CONTEXT_POLICY:
        return OWNED_CONTEXT_SCHEMA
    raise ValueError('unsupported refutation result scope')


def _support(schema):
    return {SCHEMA:SUPPORT, LEGACY_SCHEMA:LEGACY_SUPPORT,
            OWNED_CONTEXT_SCHEMA:OWNED_CONTEXT_SUPPORT}[schema]


def refutation_capability(contract, *, result_scope=None):
    """Expose the supported declaration scope without starting a native check."""
    binding = contract_binding(contract)
    schema = _schema_for_scope(result_scope)
    if not DECLARATION.match(binding['exact_statement']):
        return {'can_attempt':False, 'reason':'declaration_form',
                'diagnostic':'refutation supports one ordinary named theorem declaration'}
    if schema == OWNED_CONTEXT_SCHEMA:
        return {'can_attempt':True, 'reason':'owned_context_obligations',
            'result_scope':OWNED_CONTEXT_POLICY, 'certificate_schema':OWNED_CONTEXT_SCHEMA,
            'context_export_required':bool(binding['context']),
            'positive_obligation':'E ==> (ALL x. A x --> Q x)',
            'negative_obligation':'E ==> ~ (ALL x. A x --> Q x)',
            'environment_evidence_status':'not_established_by_negative_certificate',
            'native_obligation_status':'requires_native_elaboration',
            'notice':'The native parser separates owned environment E from statement assumptions A. '
                'The negative proof must exhibit a failure satisfying A, conditional on E; '
                'it does not establish E, environment consistency, a positive premise or a seal.'}
    return {'can_attempt':True,
            'reason':'contextual_target_export' if binding['context'] else 'ordinary_context_free_declaration',
            'context_export_required':bool(binding['context']),
            'notice':'The native parser must export all owned assumptions and parameters, then the kernel checks the closed negation outside that context.'}


def render_refutation(contract, proof_body, imports, *, theory_name, audit_path, _schema=SCHEMA,
                      _modern=False, result_scope=None):
    if result_scope is not None:
        selected = _schema_for_scope(result_scope)
        if _schema not in {SCHEMA, selected}:
            raise ValueError('refutation schema conflicts with result scope')
        _schema = selected
    capability = refutation_capability(contract,
        result_scope=OWNED_CONTEXT_POLICY if _schema == OWNED_CONTEXT_SCHEMA else None)
    if not capability['can_attempt']:
        raise UnsupportedRefutation(capability['diagnostic'])
    binding = contract_binding(contract)
    if _schema not in {SCHEMA, LEGACY_SCHEMA, OWNED_CONTEXT_SCHEMA}:
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
    support_path = _support(_schema)
    support = support_path.read_text().replace('REGEN_AUDIT_PATH', json.dumps(audit_path))
    if _modern:
        support = support.replace('fun negative id name int lthy =', 'fun negative id name lthy =')
        support = support.replace('Specification.theorem false Thm.theoremK NONE',
            'Specification.theorem {verbose = false, long = false, kind = Thm.theoremK} NONE')
        support = support.replace(']) int lthy end;', ']) lthy end;')
        support = support.replace("Outer_Syntax.local_theory_to_proof'", 'Outer_Syntax.local_theory_to_proof')
    keywords = ('keywords "regen_refute" :: thy_goal' if _schema == LEGACY_SCHEMA else
                'keywords "regen_refute_capture" :: thy_decl\n  and "regen_refute_exported" :: thy_goal')
    if _schema != LEGACY_SCHEMA:
        from regen.execution.isabelle_node_checker import wrap_context
        declaration = (wrap_context(binding['context'], declaration) + '\n\n'
                       + 'regen_refute_exported ' + json.dumps(binding['contract_id']) + ' refuted_contract')
    notation = ('' if _schema == LEGACY_SCHEMA else
        'local_setup \\<open>fn lthy =>\n'
        '  (case try (Bundle.check lthy) ("l4v_word_context", Position.none) of\n'
        '    SOME name => Bundle.unbundle [name] lthy | NONE => lthy)\\<close>\n\n')
    if _modern:
        notation = notation.replace('Bundle.unbundle [name]', 'Bundle.unbundle [(true, name)]')
    return ('theory ' + theory_name + '\nimports ' + ' '.join(json.dumps(x) for x in imports)
            + '\n' + keywords + '\nbegin\n\n' + notation + 'ML \\<open>\n'
            + support + '\n\\<close>\n\n' + declaration + '\n' + proof_body.strip()
            + '\n\nend\n')


def _audit(raw, contract_id, schema=LEGACY_SCHEMA):
    if schema == OWNED_CONTEXT_SCHEMA:
        return _owned_audit(raw, contract_id)
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


def _scope_fields(root):
    names = {'result_scope', 'environment_evidence_status', 'assumption_boundary',
             'statement_parameters'} | {name+'_'+suffix for name in (
                 'environment', 'statement_assumptions', 'local_target',
                 'positive_obligation', 'negative_obligation') for suffix in ('term','text')}
    if (len(root) != len(names) or {c.tag for c in root} != names
            or any(c.attrib or len(c) for c in root)):
        raise ValueError('invalid owned-context scope evidence fields')
    fields = {c.tag:c.text or '' for c in root}
    if (fields['result_scope'] != OWNED_CONTEXT_POLICY
            or fields['environment_evidence_status'] != 'not_established_by_negative_certificate'
            or fields['assumption_boundary'] != 'Expression.read_statement:ctxt-to-owned-lthy'):
        raise ValueError('owned-context scope boundary mismatch')
    for name, value in list(fields.items()):
        if name.endswith('_term'):
            if not value or not re.fullmatch(r'(?:[0-9a-f]{2})+', value):
                raise ValueError('invalid owned-context native term binding')
            fields[name+'_sha256'] = _sha(bytes.fromhex(value))
        elif name.endswith('_text') and not value:
            raise ValueError('missing owned-context native readable obligation')
    fields['scope_evidence_sha256'] = digest(fields)
    return fields


def _owned_audit(raw, contract_id):
    root = ET.fromstring(raw)
    if root.tag != 'contract_refutation' or root.attrib != {'version':'3','contract_id':contract_id}:
        raise ValueError('owned-context refutation audit identity mismatch')
    expected = {'skip_proof':'false','oracles':'0','hypotheses':'0',
                'changed_axioms':'false','exact_owned_context_negation':'true'}
    names = set(expected) | {'expected_term','proved_term','scope_evidence','dependencies'}
    if len(root) != len(names) or {c.tag for c in root} != names:
        raise ValueError('owned-context kernel audit is incomplete')
    fields = {c.tag:c.text or '' for c in root if c.tag not in {'scope_evidence','dependencies'}}
    if any(fields.get(k) != v for k,v in expected.items()):
        raise ValueError('owned-context kernel audit is unsafe')
    scope = _scope_fields(root.find('scope_evidence'))
    if (fields['expected_term'] != scope['negative_obligation_term']
            or not re.fullmatch(r'(?:[0-9a-f]{2})+', fields['proved_term'])):
        raise ValueError('owned-context negative obligation mismatch')
    deps = root.find('dependencies')
    if any(x.tag != 'fact' or set(x.attrib) != {'name','index'} for x in deps):
        raise ValueError('invalid owned-context dependency audit')
    return {**fields, 'scope_evidence':scope, 'dependencies':[dict(x.attrib) for x in deps]}


def check_export_scan(checker, contracts, output_dir, *, result_scope=None):
    """Elaborate/export fixed targets without attempting or installing proofs."""
    from collections import defaultdict
    schema = _schema_for_scope(result_scope)
    from regen.execution.isabelle_node_checker import wrap_context
    if not contracts or len({contract_binding(c)['contract_id'] for c in contracts}) != len(contracts):
        raise ValueError('export scan requires a nonempty unique fixed contract set')
    output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=False)
    first=contract_binding(contracts[0]); label='Refutation_'+uuid4().hex
    # The canonical v2 preamble is identical to the proof checker. Capture
    # commands install no facts, so equal owned contexts can safely be shared.
    context_free={**first,'context':[]}
    from regen.execution.native_runtime import is_calg
    template=render_refutation(context_free,'by fail',list(checker.imports()),
        theory_name=label,audit_path=str(output_dir/'unused-audit.xml'), _modern=is_calg(checker), _schema=schema)
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
    if schema == OWNED_CONTEXT_SCHEMA:
        for identity, value in re.findall(r'^REGEN_REFUTATION_SCOPE ([0-9a-f]+) ([0-9a-f]+)$',log,re.M):
            identity = bytes.fromhex(identity).decode()
            element = ET.fromstring(bytes.fromhex(value).decode())
            if element.tag != 'contract_scope' or element.attrib != {'version':'1','contract_id':identity}:
                raise ValueError('native scope observation identity mismatch')
            if identity in records:raise ValueError('duplicate native export observation')
            records[identity] = _scope_fields(element)
    else:
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
            'support_sha256':_sha(_support(schema).read_bytes()),'native_check':native}
    if schema == OWNED_CONTEXT_SCHEMA:
        result.update(policy='owned-context-obligation-scan-v1',result_scope=OWNED_CONTEXT_POLICY)
    durable(output_dir/'result.json',result,immutable=True)
    return result


def validate_refutation_certificate(certificate, contract, checker=None, *, expected_imports=None,
                                   expected_result_scope=None):
    """Offline replay of a *trusted-produced* certificate, not a new proof check.

    Hashes detect rebinding/tampering; they do not authenticate arbitrary model
    JSON. The caller must own native execution and durable acceptance.
    """
    c = certificate
    if not isinstance(c, dict) or c.get('schema') not in {SCHEMA,LEGACY_SCHEMA,OWNED_CONTEXT_SCHEMA}:
        raise ValueError('invalid refutation certificate')
    owned = c['schema'] == OWNED_CONTEXT_SCHEMA
    if c.get('status') != ('context_refuted' if owned else 'refuted'):
        raise ValueError('refutation status conflicts with its result scope')
    if expected_result_scope is not None and owned != (_schema_for_scope(expected_result_scope) == OWNED_CONTEXT_SCHEMA):
        raise ValueError('refutation certificate differs from frozen result scope')
    if owned and c.get('result_scope') != OWNED_CONTEXT_POLICY:
        raise ValueError('owned-context certificate lacks its result scope')
    if c.get('contract') != contract_binding(contract) or c.get('contract_sha256') != digest(c['contract']):
        raise ValueError('refutation fixed contract binding mismatch')
    support_path = _support(c['schema'])
    if c.get('support_sha256') != _sha(support_path.read_bytes()):
        raise ValueError('refutation verifier implementation mismatch')
    if checker is not None:
        from regen.execution.native_runtime import is_calg
        if bool(c.get('native_profile')) != is_calg(checker):
            raise ValueError('refutation native profile differs from checker')
        if c.get('checker_binding_sha256') != checker_binding(checker):
            raise ValueError('refutation checker binding mismatch')
        expected_imports = list(checker.imports())
    if expected_imports is not None and c.get('imports') != list(expected_imports):
        raise ValueError('refutation import binding mismatch')
    if c.get('native_profile') not in (None, 'isabelle2026-v1'):
        raise ValueError('unsupported refutation native profile')
    source = render_refutation(contract, c['proof_body'], c['imports'],
                               theory_name=c['theory_name'], audit_path=c['audit_path'], _schema=c['schema'],
                               _modern=c.get('native_profile') == 'isabelle2026-v1')
    for name, value in [('source',source), ('proof_body',c['proof_body']),
                        ('log',c['log']), ('audit_xml',c['audit_xml'])]:
        if c.get(name) != value or c.get(name+'_sha256') != _sha(value):
            raise ValueError('refutation evidence hash mismatch: '+name)
    if c.get('returncode') != 0 or c.get('native_verdict') != 'green':
        raise ValueError('refutation did not finish its native check')
    if c.get('audit') != _audit(c['audit_xml'], c['contract']['contract_id'], c['schema']):
        raise ValueError('refutation audit projection mismatch')
    if owned and c.get('scope_evidence') != c['audit']['scope_evidence']:
        raise ValueError('owned-context certificate scope evidence mismatch')
    payload = {k:v for k,v in c.items() if k != 'certificate_id'}
    if c.get('certificate_id') != 'refutation:'+digest(payload):
        raise ValueError('refutation certificate identity mismatch')
    return True


def _failure_diagnostic(native, log):
    """Render errors before truncating; raw scope XML stays in the audit log.

    The ordinary native diagnostic is already tail-truncated and can start in
    the middle of a very large hex record. Filtering that fragment is too late.
    This presentation step does not change native failure classification.
    """
    text, omitted = re.subn(
        r'(?m)^REGEN_REFUTATION_SCOPE [0-9a-fA-F]+ [0-9a-fA-F]+[ \t]*(?:\r?\n|$)',
        '', log)
    error = re.search(r'(?m)^\*\*\* ', text)
    if error:
        text = text[error.start():]
    elif native.get('diagnostic') and not log.endswith(str(native['diagnostic'])):
        # Retain an explicit process-level message that is not a raw-log tail.
        text = str(native['diagnostic']) + '\n' + text
    if not text.strip():
        # No raw diagnostic exists (e.g. a process startup failure).
        text = str(native.get('diagnostic') or '') if not log.strip() else (
            'Native check failed without a proof diagnostic; inspect the full native log.')
    truncated = len(text) > 12000
    if truncated:
        notice = '\n[Diagnostic truncated; full evidence remains in native_check.log.]\n'
        text = text[:2000] + notice + text[-(12000 - 2000 - len(notice)):]
    return {'diagnostic':text, 'diagnostic_truncated':truncated,
            'scope_records_omitted':omitted}


def check_refutation(checker, contract, proof_body, output_dir, *, result_scope=None):
    """Check a model-supplied negative proof without changing positive state."""
    binding = contract_binding(contract)
    schema = _schema_for_scope(result_scope)
    owned = schema == OWNED_CONTEXT_SCHEMA
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    label = 'Refutation_' + uuid4().hex
    imports = list(checker.imports())
    # Local test transports can use a direct path; production uses the native
    # container's writable output mount. This changes I/O only, not the goal.
    audit_path = str(output_dir/'refutation.xml') if getattr(checker, 'refutation_local_io', False) else '/audit-output/refutation.xml'
    from regen.execution.native_runtime import is_calg
    try:
        source = render_refutation(contract, proof_body, imports,
                                   theory_name=label, audit_path=audit_path, _modern=is_calg(checker), _schema=schema)
    except UnsupportedRefutation as error:
        result = {'status':'unsupported', 'diagnostic':str(error), 'certificate':None}
        if owned:result['result_scope'] = OWNED_CONTEXT_POLICY
        durable(output_dir/'result.json', result, immutable=True)
        return result
    request = {'contract':binding,'contract_sha256':digest(binding),
        'proof_body_sha256':_sha(proof_body), 'imports':imports,
        'checker_binding_sha256':checker_binding(checker)}
    if owned:request.update(result_scope=OWNED_CONTEXT_POLICY,certificate_schema=schema)
    durable(output_dir/'request.json', request, immutable=True)
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
        if owned:result['result_scope'] = OWNED_CONTEXT_POLICY
        durable(output_dir/'result.json', result, immutable=True)
        return result
    if native.get('returncode') != 0 or native.get('verdict') != 'green':
        unsupported = 'REGEN_REFUTATION_UNSUPPORTED:' in log
        result = {'status':'unsupported' if unsupported else 'not_refuted',
                  **_failure_diagnostic(native, log), 'certificate':None,
                  'native_check':native, 'source_sha256':_sha(source), 'log_sha256':_sha(log)}
        if owned:result['result_scope'] = OWNED_CONTEXT_POLICY
        durable(output_dir/'result.json', result, immutable=True)
        return result
    audit_file = output_dir/'refutation.xml'
    if not audit_file.is_file():
        raise ValueError('successful native process lacks refutation audit')
    xml = audit_file.read_text()
    certificate = {'schema':schema,'status':'context_refuted' if owned else 'refuted','contract':binding,
        'contract_sha256':digest(binding),'theory_name':label,'audit_path':audit_path,
        'imports':imports,'checker_binding_sha256':checker_binding(checker),
        'support_sha256':_sha(_support(schema).read_bytes()),'proof_body':proof_body,
        'source':source,'log':log,'audit_xml':xml,
        'returncode':native['returncode'],'native_verdict':native['verdict'],
        'audit':_audit(xml,binding['contract_id'],schema), 'native_receipt':native.get('native_receipt')}
    if owned:
        certificate.update(result_scope=OWNED_CONTEXT_POLICY,
            scope_evidence=certificate['audit']['scope_evidence'])
    if is_calg(checker): certificate['native_profile'] = 'isabelle2026-v1'
    for name in ('source','proof_body','log','audit_xml'):
        certificate[name+'_sha256'] = _sha(certificate[name])
    certificate['certificate_id'] = 'refutation:'+digest(certificate)
    validate_refutation_certificate(certificate, contract, checker)
    check_cancelled()
    durable(output_dir/'certificate.json', certificate, immutable=True)
    result = {'status':'context_refuted' if owned else 'refuted','certificate':certificate,
              'diagnostic':('The kernel checked the fixed contract failure conditional on its owned environment; '
                  'environment truth/consistency is not established and no positive premise or seal is published.'
                  if owned else 'The kernel checked the negation of the closed fixed contract; no positive GREEN or seal is published.')}
    if owned:
        result.update(result_scope=OWNED_CONTEXT_POLICY,scope_evidence=certificate['scope_evidence'])
    durable(output_dir/'result.json', result, immutable=True)
    return result
