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
