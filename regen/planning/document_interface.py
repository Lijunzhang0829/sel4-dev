"""Document vocabulary at the tool boundary; legacy journal bytes stay unchanged.

Only structured protocol keys are translated. Isabelle source, IDs, diagnostic
text and content hashes are never rewritten. The adapter is not proof authority.
"""
from copy import deepcopy

PUBLIC_NAMES = {
    'graph':'declaration_index', 'graph_revision':'document_revision',
    'graph_check':'declaration_check', 'nodes':'entries', 'node':'entry',
    'node_id':'entry_id', 'node_ids':'entry_ids', 'node_sha256':'entry_sha256',
    'context_node_id':'context_entry_id', 'target_node_id':'target_entry_id',
    'failed_node_id':'failed_entry_id', 'failed_declaration_id':'failed_entry_id',
    'focus_node_ids':'focus_entry_ids', 'green_node_ids':'accepted_entry_ids',
    'unproved_node_ids':'open_entry_ids', 'proof_node_ids':'proof_entry_ids',
    'generated_node_ids':'auxiliary_entry_ids', 'node_results':'entry_results',
    'generated_nodes':'auxiliary_entries', 'checked_generated_nodes':'checked_auxiliaries',
    'node_count':'entry_count', 'nodes_checked':'entries_checked',
}
# A statement-check result is not a callable selector, so its two legacy aliases
# need no input inverse. Other public source references must round-trip exactly.
INTERNAL_NAMES = {public:old for old,public in PUBLIC_NAMES.items() if old!='failed_declaration_id'}
ID_MAPS = {'proofs', 'proof_dependencies', 'entry_status', 'node_status', 'batch_examples'}


def public_view(value):
    if isinstance(value,list):
        return [public_view(v) for v in value]
    if not isinstance(value,dict):
        return value
    # Historical evidence is explicitly requested in its original bound schema.
    # Do not rename keys inside the candidate/feedback and invalidate its hashes.
    if value.get('view') == 'full' and 'bindings' in value and 'content_id' in value:
        return deepcopy(value)
    omitted = set()
    if 'statement' in value and 'id' in value and 'role' in value:
        omitted.update(('dependencies','consumers','strategy','intended_consumers'))
    if 'nodes' in value and 'execution_order' in value:
        omitted.update(('execution_order','contract_routes','initial_graph_owner',
                        'runtime_graph_owner','execution_mode','graph_payload_sha256'))
    result = {}
    for key,item in value.items():
        if key in omitted: continue
        mapped = PUBLIC_NAMES.get(key,key)
        if mapped in result:
            raise ValueError('ambiguous document view field: '+mapped)
        if key in ID_MAPS and isinstance(item,dict):
            result[mapped] = {identity:public_view(v) for identity,v in item.items()}
        else:
            result[mapped] = public_view(item)
    return result


def internal_request(value):
    """Normalize read-only selectors/refs, rejecting ambiguous alias pairs."""
    if isinstance(value,list):
        return [internal_request(v) for v in value]
    if not isinstance(value,dict):
        return value
    result = {}
    for key,item in value.items():
        mapped = INTERNAL_NAMES.get(key,key)
        if mapped in result:
            raise ValueError('ambiguous document request field: '+mapped)
        if key == 'path' and isinstance(item,list):
            result[mapped] = [INTERNAL_NAMES.get(v,v) if isinstance(v,str) else v for v in item]
        elif key in ID_MAPS:
            result[mapped] = deepcopy(item)
        else:
            result[mapped] = internal_request(item)
    return result


def observation_instructions():
    # Share observation semantics while removing legacy planner action schemas.
    from regen.planning.batch_file_planner import INSTRUCTIONS
    from regen.planning.evidence_reads import EVIDENCE_INSTRUCTIONS
    text = INSTRUCTIONS.split('Observation actions',1)[1].split('{"action":"finish"',1)[0]
    # Historical journal readers remain supported; new document agents see one
    # evidence-recovery operation instead of four independent lookup actions.
    text = '\n'.join(line for line in text.splitlines() if not any(
        line.startswith('{"action":"' + action + '"')
        for action in ('read_facts', 'read_queries', 'read_proofs')))
    text = text.replace('read_proofs', 'read_evidence with proof refs').replace(
        'read_facts', 'read_evidence with fact refs')
    text = text.replace('(all may be used alone or within observe):',
                        '(independent query/diagnostic reads may be grouped with observe):')
    text = text.replace('Omit context_node_id for global context.',
        'For a target-specific query include context_node_id so read_entry can restore its retrieval history. '
        'Omit it for genuinely global queries; the harness will not guess their target.')
    for old,new in sorted(PUBLIC_NAMES.items(),key=lambda pair:-len(pair[0])):
        import re
        text = re.sub(r'\b'+re.escape(old)+r'\b',new,text)
    return text.replace('graph node ID','entry ID').replace('node ID','entry ID').replace(
        'submitted node','submitted entry').replace('multi-node','multi-entry').replace("node's declaration","entry's declaration") + '\n' + EVIDENCE_INSTRUCTIONS + '''
For bounded goal retrieval, explicitly select retrieval_policy="indexed-v1".
It indexes upstream/generated candidates before scoring. candidate_budget (1..500,
default 100) bounds the candidate pool before optional native structural reranking;
only the first five ranked records are displayed. This is a retrieval horizon,
not a claim that other useful facts do not exist. Legacy requests retain their
previous ranking. Exact-name and definition lookup are unchanged.
{"action":"query","request":{"goal_query":true,"context_entry_id":"target entry ID","retrieval_policy":"indexed-v1","candidate_budget":50,"structural_rerank":true}}
structural_rerank elaborates the complete owned declaration in checked imports
and its owned context, then uses native typed matching on this candidate pool.
It can report a conclusion match, a left-to-right rewrite match, and outstanding
premises; it neither discharges those premises nor publishes a proof. Indirect
use and reverse rewriting may still work when no direct match is reported.
Historical printed residuals retain lexical results and report that native
structural matching is unavailable; they are never reparsed as a live context.
Generated discovery is reference-only. Inspect visibility and provider versions
from list_generated/read_generated before choosing imports or copying a proof.
read_entry retrieval memory distinguishes returned candidates, explicit reads,
proof-text mentions in attempts and accepted proof-text mentions. Textual mentions
are not native dependency certificates. Old target/environment bindings remain
historical evidence, not current applicability judgments or permanent exclusions.
'''


def tool_contracts():
    """Read-only contract for the shared A/B document tools; no runtime is opened."""
    import json
    import re
    from regen.planning.parallel_file_planner import tools
    from regen.planning.evidence_reads import evidence_schema
    text={'type':'string'}
    sha={'type':'string','minLength':1}
    edit={'type':'object','properties':{'old':{'type':'string','minLength':1},'new':text},
          'required':['old','new'],'additionalProperties':False}
    ids={'type':'array','items':sha,'minItems':1,'uniqueItems':True}
    use_site={'type':'object','properties':{'helper_id':sha,
        'consumer_line':{'type':'integer','minimum':1},'fact':text},
        'required':['helper_id','consumer_line','fact'],'additionalProperties':False}
    group={'type':'object','properties':{'target_entry_id':sha,'helper_ids':ids,
        'use_sites':{'type':'array','items':use_site,'minItems':1}},
           'required':['target_entry_id','helper_ids'],'additionalProperties':False}
    review={'type':'object','properties':{'helper_id':sha,
        'use_indices':{'type':'array','items':{'type':'integer','minimum':0},'minItems':1,'uniqueItems':True},
        **{k:{'enum':['local','generalized']} for k in ('domain_scope','premises','conclusion')},
        'generalization':{'enum':['none','induction','reuse']},
        'burden':{'enum':['isolated','delegated','proved','unchanged']},'reduction':text,
        'generalization_evidence':{'oneOf':[
            {'type':'object','properties':{k:text for k in ('variable','invariant','base_case','step_case')},
             'required':['variable','invariant','base_case','step_case'],'additionalProperties':False},
            {'type':'object','properties':{k:text for k in ('consumers','shared_pattern')},
             'required':['consumers','shared_pattern'],'additionalProperties':False}]},
        'probe_id':sha,'child_review_id':sha},
        'required':['helper_id','use_indices','domain_scope','premises','conclusion','generalization','burden','reduction'],
        'additionalProperties':False}
    source_actions={
        'report_unresolved':{'type':'object',
            'description':'Stop this theory with an unknown outcome, preserve accepted work and release its worker. Classifications are unverified reports, never proof that the spec is false.',
            'properties':{'action':{'const':'report_unresolved'},'reason':sha,'entry_ids':ids,
                'classification':{'enum':['proof_search_exhausted','suspected_counterexample',
                    'missing_assumptions','unsupported_specification','no_progress']}},
            'required':['action','reason'],'additionalProperties':False},
        'check_refutation':{'type':'object',
            'description':'Independently check a proof of the negation of the frozen fixed contract, including its quantifiers and assumptions. A verified refutation stops the theory but never proves the positive goal or seals its provider. The checker supplies the negative statement; do not submit a replacement spec. Only frozen parent and sealed imports are available, not the local positive prefix.',
            'properties':{'action':{'const':'check_refutation'},'entry_id':sha,'proof_body':sha},
            'required':['action','entry_id','proof_body'],'additionalProperties':False},
        'read_entry':{'type':'object',
            'description':'Read the exact draft proof for one stable entry ID, immutable declaration/context, and expected_hash for edit_proof. Read-only; does not submit a candidate proof check.',
            'properties':{'action':{'const':'read_entry'},'entry_id':sha},
            'required':['action','entry_id'],'additionalProperties':False},
        'edit_proof':{'type':'object',
            'description':'Replace one complete proof body by entry ID while the harness preserves declaration, context and markers. expected_hash is the entire document source hash returned by read_entry, not proof_sha256. Use oops for an open slot; saving never accepts a proof.',
            'properties':{'action':{'const':'edit_proof'},'entry_id':sha,
                'proof_body':{'type':'string','minLength':1},'expected_hash':sha,'notes':text,
                'sketches':{'type':'array','items':group},
                'helper_purposes':{'type':'object','additionalProperties':{'type':'object',
                    'properties':{key:text for key in ('contract_id','gap','expected_use','remaining_gap','used_in_target')},
                    'required':['contract_id','gap','expected_use','remaining_gap'],'additionalProperties':False}}},
            'required':['action','entry_id','proof_body','expected_hash'],'additionalProperties':False},
        'edit_theory':{'type':'object',
            'description':'Save one draft update: full source, exact-text edits, or a complete imports list. Imports changes are accumulated in the draft and applied by the harness before native checks; saving does not import facts or accept proofs.',
            'properties':{'action':{'const':'edit_theory'},
            'base_sha256':sha,'source':text,'edits':{'type':'array','items':edit,'minItems':1},
            'imports':ids,'notes':text,
            'sketches':{'type':'array','items':group},
            'helper_purposes':{'type':'object','additionalProperties':{'type':'object',
                'properties':{key:text for key in ('contract_id','gap','expected_use','remaining_gap','used_in_target')},
                'required':['contract_id','gap','expected_use','remaining_gap'],'additionalProperties':False}}},
            'required':['action','base_sha256'],'additionalProperties':False,
            'oneOf':[{'required':[key], 'not':{'anyOf':[
                {'required':[other]} for other in ('source','edits','imports') if other != key]}}
                for key in ('source','edits','imports')]},
        'check_theory':{'type':'object','properties':{'action':{'const':'check_theory'},
            'source_sha256':sha,'reason':text,'entry_ids':ids},'required':['action','source_sha256'],'additionalProperties':False},
        'check_sketch':{'type':'object','properties':{'action':{'const':'check_sketch'},
            'source_sha256':sha,'target_entry_ids':ids},
            'required':['action','source_sha256'],'additionalProperties':False},
        'read_sketch':{'type':'object','properties':{'action':{'const':'read_sketch'},
            'receipt_id':sha,'view':{'enum':['summary','full']}},
            'required':['action','receipt_id'],'additionalProperties':False},
        'transition_proof':{'type':'object','properties':{'action':{'const':'transition_proof'},
            'source_sha256':sha,'failure_id':sha,'decision':{'enum':['formal_leaf','revise_consumer','revise_interface','decompose_helper']},'plan':text},
            'required':['action','source_sha256','failure_id','decision','plan'],'additionalProperties':False},
        'probe_helpers':{'type':'object','properties':{'action':{'const':'probe_helpers'},
            'source_sha256':sha,'entry_ids':ids},'required':['action','source_sha256'],'additionalProperties':False},
        'assess_sketch':{'type':'object','properties':{'action':{'const':'assess_sketch'},
            'source_sha256':sha,'target_entry_ids':ids},
            'required':['action','source_sha256'],'additionalProperties':False},
        'read_failure':{'type':'object','properties':{'action':{'const':'read_failure'},'failure_id':sha},
            'required':['action','failure_id'],'additionalProperties':False},
        'review_sketch':{'type':'object','properties':{'action':{'const':'review_sketch'},
            'source_sha256':sha,'target_entry_id':sha,'receipt_id':sha,
            'reviews':{'type':'array','items':review,'minItems':1}},
            'required':['action','source_sha256','target_entry_id','receipt_id','reviews'],'additionalProperties':False},
        'diagnose_failure':{'type':'object','properties':{'action':{'const':'diagnose_failure'},
            'failure_id':sha,'classification':{'enum':['script','known_fact','mathematical','candidate_timeout']},
            'plan':text},'required':['action','failure_id','classification','plan'],'additionalProperties':False},
        'finish':{'type':'object',
            'description':'Check the complete current draft if necessary, then run full verification, audit and build before publishing. Failed checks return feedback without sealing.',
            'properties':{'action':{'const':'finish'},'source_sha256':sha},
            'required':['action','source_sha256'],'additionalProperties':False},
        'read_evidence':evidence_schema(),
    }
    examples=[{'action':'report_unresolved','reason':'The fixed goal remains unresolved after the recorded checks','classification':'proof_search_exhausted'},
        {'action':'check_refutation','entry_id':'fixed entry ID','proof_body':'complete proof of the checker-generated negative goal'},
        {'action':'read_entry','entry_id':'fixed entry ID'},
        {'action':'edit_proof','entry_id':'fixed entry ID','expected_hash':'expected_hash returned by read_entry',
         'proof_body':'by simp'},
        {'action':'edit_theory','base_sha256':'hash returned by read_theory',
        'edits':[{'old':'unique exact source text','new':'replacement source text'}]},
        {'action':'edit_theory','base_sha256':'hash returned by read_theory',
         'imports':['Generated_Refine.Provider']},
        {'action':'check_theory','source_sha256':'current saved hash'},
        {'action':'check_theory','source_sha256':'current saved hash','entry_ids':['h1','h2']},
        {'action':'check_sketch','source_sha256':'current saved hash','target_entry_ids':['parent ID']},
        {'action':'read_sketch','receipt_id':'receipt ID from check_sketch','view':'full'},
        {'action':'probe_helpers','source_sha256':'current saved hash'},
        {'action':'diagnose_failure','failure_id':'ID from read_theory.workflow',
         'classification':'script','plan':'repair the existing proof against the actual native goal'},
        {'action':'finish','source_sha256':'current complete draft hash'},
        {'action':'read_evidence','refs':[{'kind':'fact','id':'record ID returned by query'}]},
        {'action':'read_evidence','index':'facts','offset':0,'limit':32}]
    instructions=observation_instructions()
    decoder=json.JSONDecoder()
    for match in re.finditer(r'\{"action":',instructions):
        try:value,_=decoder.raw_decode(instructions[match.start():])
        except ValueError:continue
        if value not in examples:examples.append(value)
    return {'version':1,'interface':'shared-document-harness',
        'tools':tools(document_mode=True),'source_action_schemas':source_actions,
        'proof_action_examples':[{'theory':'theory from accepted plan','proposal':v} for v in examples],
        'observation_contract':instructions,
        'semantics':{'edit':'saves an unproved draft; prefer read_entry/edit_proof for proof-only changes. edit_theory remains available for helper structure or sequential exact edits. Sketch strategies require explicit parent groups for every new auxiliary; sketch-local-demands-v1 also requires use_sites covering every group helper',
            'imports':'draft_imports can differ from checked_imports. A native action validates and applies the final list; imports_rejected preserves the previous context. New context acceptance retires old GREEN and query projections but preserves scripts and history. Certified providers must belong to this run. Published theories and environments pinned by native refutations cannot change.',
            'proof_edit':'expected_hash binds the entire current source; a change anywhere requires a fresh hash. Only the selected proof span is replaced, with the same hygiene, sketch/review and atomic draft-save checks as edit_theory. Proof edits never alter fixed statements/contexts or directly publish GREEN.',
            'edit_errors':'Rejected edits leave the draft unchanged. Structured details identify stale current_sha256 or exact edit_index (zero-based), match_count and matching entry/line locations. Sequential-edit positions refer to the unsaved candidate after preceding edits.',
            'sketch':'temporarily admits precisely the selected group in a discarded native transaction; conditional_closed is never GREEN or publishable',
            'helper_uses':'consumer_line selects a 1-based line of the parent proof body, not the theory file; fact is its exact helper reference. The full sketch receipt records native facts after attributes, assumptions, chained facts and pending goals. Remaining schematic variables are unresolved; these observations do not certify dependency, necessity or difficulty',
            'workflow':'sketch-reviewed-v1 requires native leaf probes and bottom-up reviews before formal helper attempts. Review judgments remain model assessments. Related decomposition revisions are gated on native failure diagnosis; script/known_fact failures require a changed-proof retry first. Probes never accept proofs or replenish budget',
            'guided_workflow':'sketch-guided-v1 requires assess_sketch and bottom-up reviews. Native structure heuristics flag repeated/multi-operation/iteration/unknown leaves: use a successful probe or reviewed child. Other leaves need no mandatory probe. Generalization needs a concrete mechanism. read_failure returns exact saved proof/diagnostic/state. Scores and explanations never certify difficulty, truth, minimality or reuse',
            'check':'native declaration/proof checking; draft_checked is diagnostic only. Publication requires GREEN and preservation of every accepted entry; only committed evidence establishes accepted GREEN',
            'incremental':'entry_ids selects several draft proofs; every accepted proof is retained, the longest identical prefix is reused, and the affected suffix is checked',
            'finish':'requires the current complete draft hash; pending imports and unaccepted proof text are checked before independent complete-theory audit/build and seal. Under contract-dispositions-v1, every fixed contract must be proved or explicitly disposed; finish may publish an audited positive subset as exported_partial without claiming all contracts proved.',
            'outcomes':'unknown is a scheduling disposition, never unprovability; refuted requires an independently checked negation. Under contract-dispositions-v1, report_unresolved requires explicit entry_ids, only those contracts stop, and other targets continue. Disposed goals export no positive theorem; their slots stay oops. Hard consumers require a checked published export; exported_partial is not complete proof success. Legacy runs retain whole-theory stopping. Positive proofs and costs are preserved in either policy.',
            'read_theory':'Default returns exact full source. Optional if_source_sha256 suppresses source '
                          'only on an exact hash match and marks source_unchanged; feedback remains current. '
                          'Cumulative fact/query indexes are paginated with explicit totals and next_action.',
            'planning_advice':'Optional unverified directions, not mandatory proof routes. read_theory shows '
                'candidate status summaries; read_entry expands the target candidates and their fixed declarations. '
                'Status is recomputed from current accepted snapshots, draft order and checked imports; '
                'can_use_as_current_premise describes certified visibility, not applicability. '
                'Pending contracts may be proved earlier in the same batch; they are never assumed. '
                'Adopt, adapt or discard suggestions based on actual proof feedback. Suggestions create '
                'no imports, facts or mandatory waiting dependencies.',
            'retrieval_memory':'read_entry restores explicitly target-bound query history and a bounded set '
                'of complete recalled candidates, with totals and read_evidence navigation. Unbound queries '
                'remain global. These records are retrieval observations, not proof authority or evidence '
                'of actual premise use; historical environment changes require current-context checking.',
            'references':'unsealed generated material is reference-only, never an implicit premise'},
        'helper_format':{'description':'Use the same shared document markers as fixed entries; only helpers may introduce new statements.',
            'example':'(* REGEN-BEGIN {"id":"h1","role":"helper","context":[]} *)\nlemma helper_name: "proposition"\n(* REGEN-PROOF *)\noops\n(* REGEN-END *)'},
        'examples_are_syntax_only':True}
