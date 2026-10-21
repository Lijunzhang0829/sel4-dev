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
    text = INSTRUCTIONS.split('Observation actions',1)[1].split('{"action":"finish"',1)[0]
    for old,new in sorted(PUBLIC_NAMES.items(),key=lambda pair:-len(pair[0])):
        import re
        text = re.sub(r'\b'+re.escape(old)+r'\b',new,text)
    return text.replace('graph node ID','entry ID').replace('node ID','entry ID').replace(
        'submitted node','submitted entry').replace('multi-node','multi-entry').replace("node's declaration","entry's declaration")


def tool_contracts():
    """Read-only contract for the shared A/B document tools; no runtime is opened."""
    import json
    import re
    from regen.planning.parallel_file_planner import tools
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
        'edit_theory':{'type':'object','properties':{'action':{'const':'edit_theory'},
            'base_sha256':sha,'source':text,'edits':{'type':'array','items':edit,'minItems':1},'notes':text,
            'sketches':{'type':'array','items':group},
            'helper_purposes':{'type':'object','additionalProperties':{'type':'object',
                'properties':{key:text for key in ('contract_id','gap','expected_use','remaining_gap','used_in_target')},
                'required':['contract_id','gap','expected_use','remaining_gap'],'additionalProperties':False}}},
            'required':['action','base_sha256'],'additionalProperties':False,
            'oneOf':[{'required':['source'],'not':{'required':['edits']}},
                     {'required':['edits'],'not':{'required':['source']}}]},
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
        'finish':{'type':'object','properties':{'action':{'const':'finish'},'source_sha256':sha},
            'required':['action','source_sha256'],'additionalProperties':False},
        'read_context_index':{'type':'object',
            'description':'Page saved fact/query addresses without rereading the draft or invoking Isabelle.',
            'properties':{'action':{'const':'read_context_index'},'index':{'enum':['facts','queries']},
                'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100},
                'snapshot_sha256':sha},
            'required':['action','index'],'additionalProperties':False},
    }
    examples=[{'action':'edit_theory','base_sha256':'hash returned by read_theory',
        'edits':[{'old':'unique exact source text','new':'replacement source text'}]},
        {'action':'check_theory','source_sha256':'current saved hash'},
        {'action':'check_theory','source_sha256':'current saved hash','entry_ids':['h1','h2']},
        {'action':'check_sketch','source_sha256':'current saved hash','target_entry_ids':['parent ID']},
        {'action':'read_sketch','receipt_id':'receipt ID from check_sketch','view':'full'},
        {'action':'probe_helpers','source_sha256':'current saved hash'},
        {'action':'diagnose_failure','failure_id':'ID from read_theory.workflow',
         'classification':'script','plan':'repair the existing proof against the actual native goal'},
        {'action':'finish','source_sha256':'current checked hash'},
        {'action':'read_context_index','index':'facts','offset':0,'limit':32}]
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
        'semantics':{'edit':'saves an unproved draft; sketch strategies require explicit parent groups for every new auxiliary; sketch-local-demands-v1 also requires use_sites covering every group helper',
            'sketch':'temporarily admits precisely the selected group in a discarded native transaction; conditional_closed is never GREEN or publishable',
            'helper_uses':'consumer_line selects a 1-based line of the parent proof body, not the theory file; fact is its exact helper reference. The full sketch receipt records native facts after attributes, assumptions, chained facts and pending goals. Remaining schematic variables are unresolved; these observations do not certify dependency, necessity or difficulty',
            'workflow':'sketch-reviewed-v1 requires native leaf probes and bottom-up reviews before formal helper attempts. Review judgments remain model assessments. Related decomposition revisions are gated on native failure diagnosis; script/known_fact failures require a changed-proof retry first. Probes never accept proofs or replenish budget',
            'guided_workflow':'sketch-guided-v1 requires assess_sketch and bottom-up reviews. Native structure heuristics flag repeated/multi-operation/iteration/unknown leaves: use a successful probe or reviewed child. Other leaves need no mandatory probe. Generalization needs a concrete mechanism. read_failure returns exact saved proof/diagnostic/state. Scores and explanations never certify difficulty, truth, minimality or reuse',
            'check':'native declaration/proof checking; draft_checked is diagnostic only. Publication requires GREEN and preservation of every accepted entry; only committed evidence establishes accepted GREEN',
            'incremental':'entry_ids selects several draft proofs; every accepted proof is retained, the longest identical prefix is reused, and the affected suffix is checked',
            'finish':'complete checked unchanged theory required; independent audit/build before seal',
            'read_theory':'Default returns exact full source. Optional if_source_sha256 suppresses source '
                          'only on an exact hash match and marks source_unchanged; feedback remains current. '
                          'Cumulative fact/query indexes are paginated with explicit totals and next_action.',
            'references':'unsealed generated material is reference-only, never an implicit premise'},
        'helper_format':{'description':'Use the same shared document markers as fixed entries; only helpers may introduce new statements.',
            'example':'(* REGEN-BEGIN {"id":"h1","role":"helper","context":[]} *)\nlemma helper_name: "proposition"\n(* REGEN-PROOF *)\noops\n(* REGEN-END *)'},
        'examples_are_syntax_only':True}
