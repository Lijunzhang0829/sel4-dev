"""A persistent, managed Isabelle source document; no model-authored proof DAG.

Markers bind fixed goals to their original declarations. oops denotes an open slot
and is never rendered as a premise by the proof checker. Source order is textual
availability, not a claimed mathematical dependency. Drafts and accepted versions
have separate authority.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from regen.execution.proof_commits import digest, durable
from regen.planning.declaration_index import DECLARATION, build_index, index_payload_sha256

START = '(* REGEN-BEGIN '
PROOF = '(* REGEN-PROOF *)'
END = '(* REGEN-END *)'


def source_hash(source):
    return hashlib.sha256(source.encode()).hexdigest()


def header(theory, imports):
    return 'theory '+theory+'\nimports '+' '.join(json.dumps(x) for x in imports)+'\nbegin\n\nunbundle l4v_word_context\n\n'


def block(entry, proof=None):
    meta = {k:deepcopy(entry[k]) for k in ('id','role','context')}
    prefix = ''.join(x+'\n' for x in meta['context'])
    suffix = '\nend'*len(meta['context'])
    return (START+json.dumps(meta,ensure_ascii=False)+' *)\n'+prefix+entry['statement']+'\n'+PROOF+'\n'
            +(proof if proof is not None else 'oops')+suffix+'\n'+END+'\n\n')


def render(package, state, imports):
    entries = {n['id']:n for n in state['graph']['nodes']}
    order = state['graph'].get('document_order') or [*state['accepted_order'],
        *(k for k in state['graph']['execution_order'] if k not in state['accepted_order'])]
    return header(package['theory'],imports)+''.join(block(entries[k],state['proofs'].get(k)) for k in order)+'end\n'


def parse(source, package, imports):
    from regen.execution.proof_batches import _safe_text
    if not isinstance(source,str) or not source.startswith(header(package['theory'],imports)) or not source.endswith('end\n'):
        raise ValueError('preserve the theory header, planned imports, and final end')
    body = source[len(header(package['theory'],imports)):-len('end\n')]
    fixed = {c['contract_id']:c for c in package['contracts']}
    contexts = {tuple(c.get('contract_owned_context',[])) for c in [*package['contracts'],*package.get('support_contracts',[])]}|{()}
    entries, proofs, seen = [], {}, set()
    while body.strip():
        body = body.lstrip('\n')
        first, sep, rest = body.partition('\n')
        if not sep or not first.startswith(START) or not first.endswith(' *)'):
            raise ValueError('each declaration must have a REGEN-BEGIN identity marker')
        meta = json.loads(first[len(START):-3])
        if not isinstance(meta,dict) or set(meta)!={'id','role','context'}:
            raise ValueError('entry metadata is exactly id, role, context')
        key, role, context = meta['id'], meta['role'], meta['context']
        if not isinstance(key,str) or not key or key in seen or role not in ('contract','helper','theorem'):
            raise ValueError('entries need unique nonempty IDs and contract/helper/theorem roles')
        if not isinstance(context,list) or any(not isinstance(x,str) for x in context) or tuple(context) not in contexts:
            raise ValueError('entry context must be an available fixed/support context')
        seen.add(key)
        chunk, sep, body = rest.partition('\n'+END+'\n')
        if not sep:
            raise ValueError('entry lacks REGEN-END')
        declaration, sep, proof = chunk.partition('\n'+PROOF+'\n')
        prefix, suffix = ''.join(x+'\n' for x in context), '\nend'*len(context)
        if not sep or not declaration.startswith(prefix) or (suffix and not proof.endswith(suffix)):
            raise ValueError('preserve proof marker and entry context boundaries')
        statement = declaration[len(prefix):]
        if suffix: proof = proof[:-len(suffix)]
        match = DECLARATION.match(statement)
        if not match: raise ValueError('entry must contain one named lemma/theorem declaration')
        if role=='contract':
            c = fixed.get(key)
            if c is None or statement!=c['exact_statement'] or context!=c.get('contract_owned_context',[]):
                raise ValueError('fixed contract statement/context cannot change')
        elif key in fixed:
            raise ValueError('fixed contract cannot become an auxiliary')
        _safe_text(statement,statement=True)
        entry = dict(meta,name=match[1],statement=statement)
        entries.append(entry)
        if proof.strip()!='oops':
            if not proof.strip(): raise ValueError('use oops for an open proof slot')
            _safe_text(proof)
            proofs[key]=proof
    if {n['id'] for n in entries if n['role']=='contract'} != set(fixed):
        raise ValueError('document must contain every fixed contract exactly once')
    if len({n['name'] for n in entries}) != len(entries):
        raise ValueError('duplicate declaration name in document')
    return entries,proofs


def candidate(package, state, source, imports, *, legacy=False):
    entries,proofs = parse(source,package,imports)
    index = build_index(package,entries,
        run_id=state['graph']['run_id'],revision=state['graph']['revision']+1)
    if legacy:
        # Replay only: preserve pre-index journal bindings byte-for-byte.
        from regen.planning.proof_graph import build_graph
        index = build_graph(package, {'generated_nodes':[n for n in entries if n['role']!='contract'],
            'contract_dependencies':{}, 'document_order':[n['id'] for n in entries]},
            run_id=state['graph']['run_id'], revision=state['graph']['revision']+1)
    order = [n['id'] for n in entries if n['id'] in proofs]
    index.update(context_policy='committed-prefix-v1',accepted_order=order,
        initial_graph_owner='File Planner',runtime_graph_owner='File Planner',execution_mode='file-planner-batch-v1')
    index['graph_payload_sha256']=index_payload_sha256(index)
    result=deepcopy(state)
    result.update(graph=index,proofs=proofs,accepted_order=order,document_source_sha256=source_hash(source))
    return result


def baseline(old, new):
    """Reuse an unchanged accepted prefix; source rewrites recheck every proof."""
    before = {n['id']:n for n in old['graph']['nodes']}
    after = {n['id']:n for n in new['graph']['nodes']}
    try:
        if 'declaration_index_version' not in new['graph']:
            from regen.planning.proof_graph import assert_green_nodes_unchanged
            assert_green_nodes_unchanged(old['graph'],new['graph'],set(old['proofs']))
        if any(k not in after or any(before[k].get(f) != after[k].get(f)
                for f in ('id','name','role','statement','context')) for k in old['proofs']):
            raise ValueError('changed accepted declaration')
        if new['accepted_order'][:len(old['accepted_order'])]!=old['accepted_order'] or any(new['proofs'].get(k)!=v for k,v in old['proofs'].items()):
            raise ValueError('changed accepted source')
        return old,'append'
    except ValueError:
        fresh=deepcopy(old)
        fresh['proofs']={};fresh['accepted_order']=[]
        fresh['graph']['accepted_order']=[]
        fresh['graph']['graph_payload_sha256']=index_payload_sha256(fresh['graph'])
        return fresh,'full'


def check_document(checker, old, new, source):
    from regen.execution.proof_batches import check_batch, _binding
    base,mode=baseline(old,new)
    evidence=check_batch(checker,base,new)
    evidence.update(document_source=source,document_source_sha256=source_hash(source),
                    document_old_binding=_binding(old),document_check_mode=mode)
    return evidence


def validate_document_commit(package, old, new, evidence):
    from regen.execution.proof_batches import validate_batch_commit, _binding
    source=evidence.get('document_source')
    if old is None or not isinstance(source,str) or evidence.get('document_old_binding')!=_binding(old):
        raise ValueError('document evidence has no matching accepted base')
    expected=candidate(package,old,source,evidence['imports'],
                       legacy='declaration_index_version' not in new['graph'])
    if (_binding(expected)!=_binding(new) or new.get('document_source_sha256')!=source_hash(source)
            or evidence.get('document_source_sha256')!=source_hash(source)):
        raise ValueError('document evidence differs from exact source')
    base,mode=baseline(old,new)
    if evidence.get('document_check_mode')!=mode: raise ValueError('document recheck scope mismatch')
    validate_batch_commit(base,new,evidence)


class DocumentStore:
    def __init__(self, directory, package, state, imports):
        self.root=Path(directory);self.package=package;self.imports=list(imports)
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'draft.thy'
        if not self.path.exists():
            self._write(render(package,state,imports),{'origin':'accepted-state'})

    def read(self):
        source=self.path.read_text()
        entries,proofs=parse(source,self.package,self.imports)
        return {'source':source,'source_sha256':source_hash(source),
                'open_ids':[n['id'] for n in entries if n['id'] not in proofs],
                'notice':'Draft source; proof bodies are candidates until check_theory succeeds. oops slots are not premises.'}

    def _write(self, source, origin):
        sha=source_hash(source)
        durable(self.root/'versions'/sha/'source.json',{'source':source},immutable=True)
        # One owner serializes writes. Content-addressed versions preserve failed drafts.
        durable(self.root/'last-edit.json',{'source_sha256':sha,**origin})
        temp=self.path.with_suffix('.tmp');temp.write_text(source);temp.replace(self.path)

    def edit(self, proposal, origin):
        if set(proposal)-{'action','base_sha256','source','edits','notes'}:
            raise ValueError('edit_theory accepts base_sha256 and either source or exact-text edits')
        current=self.read()
        if proposal.get('base_sha256')!=current['source_sha256']:
            raise ValueError('stale document; read_theory and apply edits to the current source hash')
        if ('source' in proposal)==('edits' in proposal): raise ValueError('provide source OR edits')
        source=proposal.get('source',current['source'])
        if 'edits' in proposal:
            if not isinstance(proposal['edits'],list) or not proposal['edits']: raise ValueError('edits must be a nonempty list')
            for edit in proposal['edits']:
                if not isinstance(edit,dict) or set(edit)!={'old','new'} or not all(isinstance(x,str) for x in edit.values()) or not edit['old'] or source.count(edit['old'])!=1:
                    raise ValueError('each edit needs a unique nonempty exact old string and replacement')
                source=source.replace(edit['old'],edit['new'],1)
        parse(source,self.package,self.imports)
        self._write(source,origin)
        return {'status':'draft_saved','source_sha256':source_hash(source),'checked':False}

    def checked_source(self, sha):
        source=self.path.read_text()
        if sha!=source_hash(source): raise ValueError('check requires the current source_sha256')
        return source
