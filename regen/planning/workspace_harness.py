"""Trusted planning authority; never mounted into the model worker."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from regen.planning.owner_blind import OwnerBlindView
from regen.planning.iterative_planner import validate_draft
from regen.planning.theory_plan import build_bundle
from regen.planning.workspace_validation import diagnostics as legacy_diagnostics
from regen.preparation.planning_input import PlanningInputQueries
from regen.preparation.refine_campaign import write_immutable_json
from regen.planning.definition_queries import definition_ids, mentioned_identifiers


def diagnostics(payload, proposal, evidence=None):
    """Preserve aggregated legacy diagnostics and validate v6 advisory metadata."""
    from regen.planning.import_contract import proposal_v5
    try:
        errors = legacy_diagnostics(payload, proposal_v5(proposal), evidence)
    except (ValueError, KeyError, TypeError) as error:
        return [{'path': '', 'message': str(error)}]
    if not errors and isinstance(proposal, dict) and proposal.get('schema_version') == 6:
        result, _ = validate_draft(payload, proposal, evidence or {})
        errors.extend({'path': '', 'message': message} for message in result['errors'])
    return errors


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n'); tmp.replace(path)


def short_view(source):
    view = OwnerBlindView(source)
    aliases = {c['contract_id']:f'C{i:04d}' for i,c in enumerate(view.payload['contracts'],1)}
    def replace(v):
        if isinstance(v,str): return aliases.get(v,v)
        if isinstance(v,list): return [replace(x) for x in v]
        if isinstance(v,dict): return {k:replace(x) for k,x in v.items()}
        return v
    view.payload = replace(view.payload)
    view.to_original = {aliases[k]:v for k,v in view.to_original.items()}
    view.to_public = {v:k for k,v in view.to_original.items()}
    view.public_contracts = {c['contract_id']:c for c in view.payload['contracts']}
    return view




class WorkspaceHarness:
    def __init__(self, planning_input, output, native_check):
        self.path=Path(planning_input).resolve()
        self.view=short_view(json.loads(self.path.read_text()))
        self.queries=PlanningInputQueries(self.path)
        if self.queries.database_version != 2: raise ValueError("Workspace planning requires the verified v2 premise database.")
        self.output=Path(output); self.native_check=native_check; self.lock=threading.RLock()
        identity=getattr(native_check,'identity',None)
        write_immutable_json(self.output/'control/input-binding.json',{
            'input_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
            'premise_database_sha256':self.queries.input['parent_premises']['database_sha256'],
            'checker':identity if isinstance(identity,dict) else None,
        })
        self.active_call=None
        self.state_path=self.output/'control/state.json'
        self.state=json.loads(self.state_path.read_text()) if self.state_path.exists() else {'inspected_contracts':{},'inspected_premises':{},'sequence':0}
        submitted=self.output/'control/submitted.json'
        self.submitted=json.loads(submitted.read_text()) if submitted.exists() else None
        save(self.output/'agent-input.json',self.view.payload)
        save(self.output/'private-contract-map.json',self.view.to_original)
        save(self.output/'planner-validation-input.json',self.view.canonical_payload)

    def evidence(self):
        return {'inspected_contracts':list(self.state['inspected_contracts'].values()),'inspected_parent_premises':list(self.state['inspected_premises'].values()),'premise_searches':[]}

    def validate(self, proposal):
        errors=diagnostics(self.view.payload,proposal,self.evidence())
        if errors: return {'valid':False,'errors':errors}
        result,_=validate_draft(self.view.payload,proposal,self.evidence())
        if result['valid']:
            result['notice']='Structure checked, not architectural quality or proof validity. Only required imports determine later availability. Collaboration and reuse suggestions grant no premise visibility or readiness dependencies; array order prioritizes ready theories.'
            if len(proposal['theories'])==1 and len(self.view.payload['contracts'])>1:
                result['warnings']=['A catch-all theory does not fulfill the requested multi-theory design.']
        return result

    def dispatch(self, request):
        with self.lock:
            self.state['sequence']+=1; number=self.state['sequence']
            try:
                result=self._dispatch(request)
                self.view.assert_no_owner_disclosure(json.dumps(result,ensure_ascii=False))
            except (ValueError,KeyError,TypeError,AttributeError) as error:
                message=str(error)
                try: self.view.assert_no_owner_disclosure(message)
                except ValueError: message='Private source-context diagnostic withheld; inspect the public contracts and exact contexts.'
                result={'valid':False,'error':message}
            save(self.output/f'queries/{number:06d}.json',{'request':request,'response':result,'call_number':self.active_call})
            save(self.state_path,self.state)
            return result

    def public_records(self, records):
        visible=[]
        for record in records:
            try:
                self.view.assert_no_owner_disclosure(json.dumps(record,ensure_ascii=False))
            except ValueError:
                continue
            visible.append(record)
        return visible

    def inspect(self, ids):
        records=self.public_records(self.queries.inspect_parent_premises(ids))
        self.state['inspected_premises'].update({r['record_id']:r for r in records})
        return records

    def _dispatch(self, request):
        if not isinstance(request,dict): raise ValueError('Expected a request object.')
        action=request.get('action'); values=request.get('values',[]); limit=request.get('limit',20); offset=request.get('offset',0)
        if not isinstance(values,list) or not all(isinstance(x,str) for x in values): raise ValueError('values must be strings')
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50: raise ValueError('limit must be 1 to 50')
        if isinstance(offset,bool) or not isinstance(offset,int) or not 0<=offset<=2**63-1: raise ValueError('offset must be a nonnegative integer')
        if offset and action not in {'related','definitions'}: raise ValueError('offset is supported by definitions and related only')
        if action=='contracts':
            if not values: return {'records':self.view.payload['contract_cards']}
            if not set(values)<=self.view.public_contracts.keys(): raise ValueError('Unknown public contract ID; use contracts with no arguments for the catalog.')
            records=[self.view.public_contracts[x] for x in values]
            self.state['inspected_contracts'].update({x['contract_id']:x for x in records})
            return {'records':records}
        if action=='related':
            if not values or not set(values)<=self.view.public_contracts.keys(): raise ValueError('related requires public contract IDs')
            rows=[r for r in self.view.payload['contract_relation_candidates'] if r['left_contract_id'] in values or r['right_contract_id'] in values]
            return {'records':rows[offset:offset+limit], 'next_offset':offset+limit if offset+limit<len(rows) else None, 'notice':'Shared symbols are clues, not proven dependencies.'}
        if action in {'search','definitions'}:
            query=' '.join(values).strip()
            if not query: raise ValueError('Provide a fact name, definition name, or concept.')
            if action=='search':
                profiles=[p['context_profile_id'] for p in self.view.payload['context_profiles']]
                hits=self.queries.search_parent_premises(query,context_profile_ids=profiles,limit=limit)
                visible=self.public_records(hits)
                return {'records':visible,'withheld_records':len(hits)-len(visible), 'notice':'Inspect complete statements before reuse. Candidates exposing private target attribution are withheld.'}
            ids,next_offset=definition_ids(self.queries.database,query.split(),limit,offset)
            records=self.inspect(ids) if ids else []
            return {'records':records,'next_offset':next_offset,'withheld_records':len(ids)-len(records),
                    'mentioned_identifiers':{r['record_id']:mentioned_identifiers(r) for r in records},
                    'notice':'Name-matched definition/equation candidates from verified sources. Complete propositions are unchanged. mentioned_identifiers may include variables and syntax; use them to query more definitions, not as certified calls or proof edges.'}
        if action=='inspect':
            if not values or len(values)>50: raise ValueError('Inspect 1 to 50 premise IDs.')
            records=self.inspect(values)
            return {'records':records,'withheld_records':len(values)-len(records),'notice':'Only records respecting the public input boundary are returned and eligible for reuse.'}
        if action in {'validate','preflight','submit'}:
            proposal=request.get('proposal'); result=self.validate(proposal)
            if not result['valid'] or action=='validate': return result
            identity=hashlib.sha256(json.dumps(proposal,sort_keys=True).encode()).hexdigest()
            cache=self.output/f'preflight/{identity}/result.json'
            native=json.loads(cache.read_text()) if cache.exists() else None
            if not native or not native['valid']:
                attempt=cache.parent/f'attempt-{len(list(cache.parent.glob("attempt-*")))+1:04d}'
                native=self.native_check(self.view.payload,proposal,attempt)
                native={**native,'attempt':attempt.name}; save(cache,native)
            if not native['valid']: return {'valid':False,'stage':'native-declaration-preflight',**native}
            if action=='submit':
                if self.submitted is not None and self.submitted['proposal']!=proposal: raise ValueError('A plan was already submitted; end this call.')
                self.submitted={'proposal':deepcopy(proposal),'preflight_sha256':identity,
                                'preflight_result_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
                                'call_number':self.active_call}
                save(self.output/'control/submitted.json',self.submitted)
            return {**result,'native_preflight':'pass','submitted':action=='submit','notice':'Declarations/imports load; no goal or helper has been proved.'}
        raise ValueError('Unknown operation.')

    def artifacts(self):
        if self.submitted is None: raise ValueError('No explicit valid submission.')
        public=self.submitted['proposal']
        identity=hashlib.sha256(json.dumps(public,sort_keys=True).encode()).hexdigest()
        cache=self.output/f'preflight/{identity}/result.json'
        if (identity!=self.submitted['preflight_sha256']
                or hashlib.sha256(cache.read_bytes()).hexdigest()!=self.submitted['preflight_result_sha256']):
            raise ValueError('Submitted plan/preflight evidence integrity mismatch.')
        native=json.loads(cache.read_text())
        source=cache.parent/native['attempt']/'source'
        for name,digest in native.get('source_sha256',{}).items():
            if Path(name).name!=name or hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('Preflight source integrity mismatch.')
        if native.get('log_sha256'):
            from regen.planning.workspace_validation import PREFLIGHT_THEORY
            if hashlib.sha256((source/(PREFLIGHT_THEORY+'.log')).read_bytes()).hexdigest()!=native['log_sha256']:
                raise ValueError('Preflight log integrity mismatch.')
        validation=self.validate(public)
        if not validation['valid']:
            raise ValueError('Submitted plan no longer validates against its inspection receipts.')
        evidence=self.view.canonical_evidence(self.evidence()); canonical=self.view.decode(public)
        bundle=build_bundle({**self.view.canonical_payload,'planner_evidence':evidence},canonical)
        save(self.output/'agent-proposal.json',public); save(self.output/'agent-evidence.json',self.evidence()); save(self.output/'planner-evidence.json',evidence)
        return canonical,bundle
