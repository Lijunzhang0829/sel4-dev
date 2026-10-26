"""Fail-closed, self-contained seal certificates. No filesystem reads on replay."""
import hashlib
import re
from regen.execution.proof_commits import digest
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.checker_policy import resolve_complete_context_policy

AUDIT_POLICY = 'declaration-export-audit-v1'


def _bytes(row, key):
    value = row.get(key + '_content')
    if not isinstance(value, str) or hashlib.sha256(value.encode()).hexdigest() != row.get(key + '_sha256'):
        raise ValueError('seal lacks bound ' + key + ' bytes')
    return value


def _native(row, source=True):
    if row.get('verdict') != 'green' or type(row.get('returncode')) is not int or row['returncode'] != 0:
        raise ValueError('seal native check did not succeed')
    _bytes(row, 'log')
    return _bytes(row, 'source') if source else None


def state_binding(state):
    return digest({k:state[k] for k in ('graph','proofs','accepted_order')})


def validate_seal(state, evidence, *, expected_imports=None, base_session=None, runtime=None):
    # Only v3 self-contained evidence can be replayed; older weak seals still
    # require explicit re-sealing. Historical v3 renderer compatibility below
    # never skips the ordinary source, native receipt or build checks.
    if evidence.get('seal_evidence_version') != 3:
        raise ValueError('seal needs v3 bound native evidence; legacy runs require explicit re-seal')
    if state['seal_id'] != 'theory-seal:' + digest(evidence)[:20]:
        raise ValueError('seal ID differs from evidence')
    if evidence.get('state_binding') != state_binding(state):
        raise ValueError('seal state binding mismatch')
    imports = evidence.get('imports')
    if not isinstance(imports,list) or not imports or any(not isinstance(x,str) for x in imports):
        raise ValueError('seal imports missing')
    if expected_imports is not None and imports != expected_imports:
        raise ValueError('seal imports differ from frozen journal imports')
    if base_session is not None and evidence.get('base_session') != base_session:
        raise ValueError('seal base session differs from frozen run')
    renderer = object.__new__(IsabelleChecker); renderer.imports = lambda: imports
    policy = resolve_complete_context_policy(runtime or {})
    if evidence.get('complete_context_policy') != policy:
        raise ValueError('seal render policy differs from frozen run')
    renderer.complete_context_policy = policy
    graph, proofs = state['graph'], state['proofs']
    stem = graph['file_slot'].removesuffix('.thy')
    source = evidence.get('source_text')
    if not isinstance(source,str) or digest(source) != evidence.get('source_hash'):
        raise ValueError('sealed source hash mismatch')
    match = re.match(r'theory ([A-Za-z0-9_]+)\n',source)
    if not match or source != renderer.complete_source(graph,proofs,theory_name=match[1]):
        raise ValueError('seal source differs from accepted state')
    from regen.execution.proof_session_runner import generated_checkpoint_theory
    if match[1] != generated_checkpoint_theory(graph['file_slot']):
        raise ValueError('published theory name mismatch')
    complete = _native(evidence.get('complete',{}))
    if complete != renderer._complete_theory_source(graph,proofs,theory_name='Complete_'+stem,diagnostic_markers=True):
        raise ValueError('complete check differs from accepted source')
    dependencies = evidence.get('dependencies',{})
    native_source = _native(dependencies)
    audit = dependencies.get('complete_theory_audit',{})
    certificates = audit.get('proof_dependencies',{})
    if set(certificates) != set(proofs):
        raise ValueError('seal dependency certificate coverage mismatch')
    audit_policy = evidence.get('audit_policy')
    if audit_policy == AUDIT_POLICY:
        expected_audit = renderer.complete_audit_source(graph, proofs)
    elif 'audit_policy' not in evidence:
        # Existing v3 seals predate declaration-bound audits. Accept only the
        # exact historical renderer; this branch also supports immutable seal
        # reuse in a continuation without pretending it is new certification.
        from regen.execution.legacy_seal_audit import legacy_complete_audit_source
        expected_audit = legacy_complete_audit_source(renderer, graph, proofs)
    else:
        raise ValueError('unsupported seal audit policy')
    if native_source != expected_audit:
        raise ValueError('dependency audit source mismatch')
    raw = evidence.get('dependency_receipts',{})
    if set(raw) != set(proofs):
        raise ValueError('native dependency receipts missing')
    for key, cert in certificates.items():
        text = _bytes(raw[key],'raw'); deps=[]; skip=[]
        for line in text.splitlines():
            fields=line.split('\t'); tag=fields.pop(0)
            try: fields=[bytes.fromhex(x).decode() for x in fields]
            except (ValueError,UnicodeError) as exc: raise ValueError('bad dependency receipt') from exc
            if tag=='THM_DEP' and len(fields)==2: deps.append({'canonical_name':fields[0],'fact_index':int(fields[1])})
            elif tag=='HAS_SKIP_PROOF' and len(fields)==1: skip.append(fields[0])
            else: raise ValueError('unknown dependency receipt field')
        if skip!=['false'] or cert.get('has_skip_proof') is not False or cert.get('dependencies')!=deps or cert.get('node_id')!=key:
            raise ValueError('native dependency receipt mismatch/admitted proof')
    build=evidence.get('build',{}); _native(build,source=False)
    sources=build.get('source_files',{}); names=build.get('theories',[])
    if not names or names[-1]!=match[1] or set(sources)!=set(names) or sources[match[1]]!=source:
        raise ValueError('session build source coverage mismatch')
    if build.get('session') != 'Sealed_Refine':
        raise ValueError('seal requires isolated child session build')
    providers = names[:-1]
    expected_import_names = ['Generated_Refine.' + name for name in providers]
    if providers and imports != expected_import_names:
        raise ValueError('build providers differ from checked imports')
    definitions = ['session Generated_Refine = '+evidence['base_session']+' +',
        '  sessions','    Lib','    CorresK','    AInvs','  theories',
        *('    '+n for n in providers),''] if providers else []
    definitions += ['session Sealed_Refine in "sealed-session" = ' +
        ('Generated_Refine' if providers else evidence['base_session']) + ' +',
        '  sessions Lib CorresK AInvs','  theories','    '+names[-1],'']
    expected_root = '\n'.join(definitions)
    if _bytes(build,'root') != expected_root:
        raise ValueError('session build ROOT mismatch')
    impact=evidence.get('impact',{})
    if impact.get('verdict')!='pass' or impact.get('theory')!=graph['theory'] or impact.get('graph_revision')!=graph['revision']:
        raise ValueError('semantic impact binding mismatch')


def bind_seal(state, evidence, checker):
    from pathlib import Path
    from regen.execution.proof_commits import freeze_evidence
    evidence = freeze_evidence(evidence)
    evidence.update(seal_evidence_version=3, audit_policy=AUDIT_POLICY,
                    complete_context_policy=resolve_complete_context_policy(vars(checker)), state_binding=state_binding(state),
                    imports=checker.imports(), base_session=checker.base_session,
                    dependency_receipts={})
    paths = evidence['dependencies']['complete_theory_audit']['proof_dependency_paths']
    for key, locations in paths.items():
        raw = Path(locations['raw']).read_bytes()
        evidence['dependency_receipts'][key] = {
            'raw_content': raw.decode(), 'raw_sha256': hashlib.sha256(raw).hexdigest()}
    return evidence
