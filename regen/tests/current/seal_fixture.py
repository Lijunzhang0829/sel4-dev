"""Synthetic, explicitly mocked v3 evidence for journal unit tests only."""
import hashlib
from regen.execution.proof_commits import digest
from regen.execution.seal_evidence import state_binding
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_session_runner import generated_checkpoint_theory

def bound(key,text):
    return {key+'_content':text,key+'_sha256':hashlib.sha256(text.encode()).hexdigest()}

def native(source=None):
    result={'verdict':'green','returncode':0,**bound('log','synthetic unit-test checker')}
    if source is not None:result.update(bound('source',source))
    return result

def evidence(state, imports=None, policy='isolated-v1'):
    renderer=object.__new__(IsabelleChecker);renderer.imports=lambda:imports or ['Semantic_Base.Semantic_Root']
    renderer.complete_context_policy=policy
    g,p=state['graph'],state['proofs'];name=generated_checkpoint_theory(g['file_slot']);stem=g['file_slot'].removesuffix('.thy')
    source=renderer.complete_source(g,p,theory_name=name)
    deps=native(renderer.complete_audit_source(g,p));deps['complete_theory_audit']={'proof_dependencies':{k:{'node_id':k,'has_skip_proof':False,'dependencies':[]} for k in p}}
    build=native();build.update(session='Sealed_Refine',theories=[name],source_files={name:source},**bound('root','\n'.join(['session Sealed_Refine in "sealed-session" = Semantic_Base +','  sessions Lib CorresK AInvs','  theories','    '+name,''])))
    return dict(seal_evidence_version=3,complete_context_policy=policy,state_binding=state_binding(state),imports=renderer.imports(),base_session='Semantic_Base',source_text=source,source_hash=digest(source),
        complete=native(renderer._complete_theory_source(g,p,theory_name='Complete_'+stem,diagnostic_markers=True)),dependencies=deps,build=build,
        impact={'verdict':'pass','theory':g['theory'],'graph_revision':g['revision']},
        dependency_receipts={k:bound('raw','HAS_SKIP_PROOF\t'+b'false'.hex()+'\n') for k in p})

def seal(state,imports=None):
    ev=evidence(state,imports);state.update(status='sealed',seal_id='theory-seal:'+digest(ev)[:20]);return ev

def mock_bind(state,old_evidence,checker):
    return evidence(state,checker.imports())
