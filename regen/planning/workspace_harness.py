"""Trusted planning authority; never mounted into the model worker."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from regen.planning.owner_blind import OwnerBlindView
from regen.planning.iterative_planner import validate_draft
from regen.planning.theory_plan import build_bundle
from regen.preparation.planning_input import PlanningInputQueries

NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')


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


def diagnostics(payload, proposal):
    errors=[]
    if not isinstance(proposal,dict) or not isinstance(proposal.get('theories'),list):
        return [{'path':'/theories','message':'Expected a proposal object with a theories array.'}]
    assigned,names,helpers=[],[],[]
    for i,t in enumerate(proposal['theories']):
        path=f'/theories/{i}'
        if not isinstance(t,dict):
            errors.append({'path':path,'message':'Expected an object.'}); continue
        name=t.get('name')
        if not isinstance(name,str) or not NAME.fullmatch(name):
            errors.append({'path':path+'/name','value':name,'message':'Use [A-Za-z][A-Za-z0-9_]*.'})
        else: names.append(name)
        ids=t.get('contract_ids')
        if not isinstance(ids,list) or not all(isinstance(x,str) for x in ids):
            errors.append({'path':path+'/contract_ids','message':'Expected an array of public contract IDs.'})
        else: assigned.extend(ids)
        decisions=t.get('helper_decisions',[])
        if not isinstance(decisions,list):
            errors.append({'path':path+'/helper_decisions','message':'Expected an array.'}); continue
        for j,d in enumerate(decisions):
            key=d.get('id') if isinstance(d,dict) else None
            if not isinstance(key,str) or not NAME.fullmatch(key):
                errors.append({'path':path+f'/helper_decisions/{j}/id','value':key,'message':'Use [A-Za-z][A-Za-z0-9_]*; no hyphens or spaces.'})
            else: helpers.append(key)
    for label,values in [('theory names',names),('helper IDs',helpers),('contract IDs',assigned)]:
        dup=sorted(k for k,n in Counter(values).items() if n>1)
        if dup: errors.append({'path':'/theories','message':f'Duplicate {label}.','values':dup})
    expected={c['contract_id'] for c in payload['contracts']}
    missing,unknown=sorted(expected-set(assigned)),sorted(set(assigned)-expected)
    if missing or unknown:
        errors.append({'path':'/theories','message':'Each contract must occur exactly once.','missing':missing,'unknown':unknown})
    return errors


class WorkspaceHarness:
    def __init__(self, planning_input, output, native_check):
        self.path=Path(planning_input).resolve()
        self.view=short_view(json.loads(self.path.read_text()))
        self.queries=PlanningInputQueries(self.path)
        if self.queries.database_version != 2: raise ValueError("Workspace planning requires the verified v2 premise database.")
        self.output=Path(output); self.native_check=native_check; self.lock=threading.RLock()
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
        errors=diagnostics(self.view.payload,proposal)
        if errors: return {'valid':False,'errors':errors}
        result,_=validate_draft(self.view.payload,proposal,self.evidence())
        if result['valid']:
            result['notice']='Structure checked, not architectural quality or proof validity. Providers determine later import availability; array order only prioritizes ready theories.'
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
            save(self.output/f'queries/{number:06d}.json',{'request':request,'response':result})
            save(self.state_path,self.state)
            return result

    def inspect(self, ids):
        records=self.queries.inspect_parent_premises(ids)
        self.view.assert_no_owner_disclosure(json.dumps(records,ensure_ascii=False))
        self.state['inspected_premises'].update({r['record_id']:r for r in records})
        return records

    def _dispatch(self, request):
        if not isinstance(request,dict): raise ValueError('Expected a request object.')
        action=request.get('action'); values=request.get('values',[]); limit=request.get('limit',20)
        if not isinstance(values,list) or not all(isinstance(x,str) for x in values): raise ValueError('values must be strings')
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50: raise ValueError('limit must be 1 to 50')
        if action=='contracts':
            if not values: return {'records':self.view.payload['contract_cards']}
            if not set(values)<=self.view.public_contracts.keys(): raise ValueError('Unknown public contract ID; use contracts with no arguments for the catalog.')
            records=[self.view.public_contracts[x] for x in values]
            self.state['inspected_contracts'].update({x['contract_id']:x for x in records})
            return {'records':records}
        if action=='related':
            if not values or not set(values)<=self.view.public_contracts.keys(): raise ValueError('related requires public contract IDs')
            return {'records':[r for r in self.view.payload['contract_relation_candidates'] if r['left_contract_id'] in values or r['right_contract_id'] in values][:limit], 'notice':'Shared symbols are clues, not proven dependencies.'}
        if action in {'search','definitions'}:
            query=' '.join(values).strip()
            if not query: raise ValueError('Provide a fact name, definition name, or concept.')
            if action=='search':
                profiles=[p['context_profile_id'] for p in self.view.payload['context_profiles']]
                return {'records':self.queries.search_parent_premises(query,context_profile_ids=profiles,limit=limit),'notice':'Inspect complete statements before reuse.'}
            terms=query.split(); clauses=['(canonical_name LIKE ? OR declaration_name LIKE ?)']*len(terms)
            params=[v for term in terms for v in (f'%{term}%',f'%{term}%')]
            with sqlite3.connect(f'file:{self.queries.database}?mode=ro',uri=True) as db:
                rows=db.execute('SELECT record_id FROM premises WHERE ('+' OR '.join(clauses)+") AND (canonical_name LIKE '%\\_def%' ESCAPE '\\' OR canonical_name LIKE '%.simps%' OR canonical_name LIKE '%.equations%') ORDER BY canonical_name LIMIT ?",[*params,limit]).fetchall()
            records=self.inspect([r[0] for r in rows]) if rows else []
            return {'records':records,'notice':'Name-matched definition/equation candidates from verified sources. Check their statements; mentioned constants are not mandatory proof edges.'}
        if action=='inspect':
            if not values or len(values)>50: raise ValueError('Inspect 1 to 50 premise IDs.')
            return {'records':self.inspect(values)}
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
                self.submitted={'proposal':deepcopy(proposal),'preflight_sha256':identity}
                save(self.output/'control/submitted.json',self.submitted)
            return {**result,'native_preflight':'pass','submitted':action=='submit','notice':'Declarations/imports load; no goal or helper has been proved.'}
        raise ValueError('Unknown operation.')

    def artifacts(self):
        if self.submitted is None: raise ValueError('No explicit valid submission.')
        public=self.submitted['proposal']; evidence=self.view.canonical_evidence(self.evidence()); canonical=self.view.decode(public)
        bundle=build_bundle({**self.view.canonical_payload,'planner_evidence':evidence},canonical)
        save(self.output/'agent-proposal.json',public); save(self.output/'agent-evidence.json',self.evidence()); save(self.output/'planner-evidence.json',evidence)
        return canonical,bundle
