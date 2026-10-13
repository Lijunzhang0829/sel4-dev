"""Passive VERO draft/usage observation; never supplies proof advice."""
import hashlib
import json
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import re
from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_activity import UsageSnapshots
from regen.infrastructure.experiment_ledger import RolloutUsageReader


class VeroObserver:
    def __init__(self, ledger, stop, workspace, bench):
        self.ledger,self.stop,self.workspace,self.bench=ledger,stop,Path(workspace),bench
        self.usage=UsageSnapshots(ledger);self.offsets={};self.fingerprints={};self.bound_threads={}
        self.nodes={};self.home=None;self.readers={};self.pending_usage={};self.finalized=set()
        self.seals={}
        attempts={}
        for row in ledger.rows:
            if row['kind']=='seal_attempt':
                data=row['data']
                attempts[(data.get('theory'),data.get('source_identity'))]=data.get('source_manifest')
                continue
            if row['kind']=='seal_invalidated':
                data=row['data'];old=self.seals.get(data.get('theory'))
                if old is None or old['source_identity']!=data.get('old_source_identity'):
                    raise ValueError('seal invalidation does not match the current receipt')
                del self.seals[data['theory']]
                continue
            if row['kind']!='seal_result' or row['data'].get('status')!='sealed':continue
            data=row['data'];theory=data.get('theory');identity=data.get('source_identity')
            if not isinstance(theory,str) or not isinstance(identity,str) or len(identity)!=64:
                continue  # Legacy telemetry was not content-bound and is not reusable.
            try:int(identity,16)
            except ValueError:raise ValueError('invalid sealed theory source identity')
            evidence=data.get('evidence')
            if not isinstance(evidence,dict) or evidence.get('returncode')!=0:
                raise ValueError('sealed theory receipt lacks successful build evidence')
            old=self.seals.get(theory)
            if old is not None and old['source_identity']!=identity:
                raise ValueError('conflicting sealed theory source identities')
            self.seals[theory]={'source_identity':identity,
                'evidence':deepcopy(evidence),'event_id':row['event_id'],
                'source_manifest':data.get('source_manifest') or attempts.get((theory,identity))}
        mark=getattr(stop,'mark_sealed',None)
        theories=getattr(stop,'theories',{})
        if callable(mark):
            for theory in sorted(self.seals):
                if theory in theories:mark(theory)
        self.scope={'target_ids':[],'theories':[],'theory':None,
            'phase':'proof','basis':'unattributed'}

    def select_theory(self, theory):
        """Record current work attribution without renewing the global clock."""
        if theory in {c['module'] for c in self.bench['contracts']}:
            self.stop.select(theory)

    def seal_completed(self, theory, result):
        """Publish only successful, independently evidenced theory seals."""
        evidence=result.get('build_evidence')
        if (result.get('seal_status')!='sealed' or not isinstance(evidence,dict)
                or evidence.get('returncode')!=0):
            raise ValueError('cannot seal theory without successful build evidence')
        identity=result.get('source_identity')
        if not isinstance(identity,str) or len(identity)!=64:
            raise ValueError('seal lacks a content-bound source identity')
        try:int(identity,16)
        except ValueError:raise ValueError('seal lacks a content-bound source identity')
        old=self.seals.get(theory)
        if old is not None and old['source_identity']!=identity:
            raise ValueError('sealed theory source identity changed')
        mark=getattr(self.stop,'mark_sealed',None)
        if not callable(mark):
            raise ValueError('seal state owner lacks mark_sealed')
        mark(theory)
        # The durable seal_result is appended before this callback. Cache only
        # the identity here; recovery reconstructs full evidence from the ledger.
        self.seals[theory]={'source_identity':identity,
            'evidence':deepcopy(evidence),'event_id':None,
            'source_manifest':result.get('source_manifest')}

    def invalidate_seal(self, theory, current_source_identity, *, reason,
                        changed_sources=(), missing_imports=()):
        old=self.seals[theory]
        revoke=getattr(self.stop,'invalidate_seal',None)
        if not callable(revoke):
            raise ValueError('seal state owner cannot record durable invalidation')
        revoke(theory,old_source_identity=old['source_identity'],
            current_source_identity=current_source_identity,reason=reason,
            changed_sources=changed_sources,missing_imports=missing_imports)
        del self.seals[theory]

    def sealed_result(self, theory, source_identity, *, source_hashes=None):
        """Reuse only exact evidence; supersede an import-stale seal durably."""
        old=self.seals.get(theory)
        if old is None:return None
        if old['source_identity']!=source_identity:
            manifest_path=old.get('source_manifest')
            if not manifest_path or source_hashes is None:
                raise ValueError('sealed theory source identity changed without auditable manifest')
            manifest=json.loads(Path(manifest_path).read_text())
            own=theory+'.thy'
            if (manifest.get(own)!=source_hashes.get(own) or
                    manifest.get('Vero_Support.thy')!=source_hashes.get('Vero_Support.thy')):
                raise ValueError('sealed theory own/support source changed')
            changed=sorted(name for name,value in source_hashes.items()
                if name not in (own,'Vero_Support.thy') and manifest.get(name)!=value)
            if not changed:
                raise ValueError('sealed theory identity changed without an import source change')
            self.invalidate_seal(theory,source_identity,reason='import_source_changed',
                changed_sources=changed)
            return None
        mark=getattr(self.stop,'mark_sealed',None)
        if callable(mark):mark(theory)
        return deepcopy(old)

    def reconcile_seals(self, theory, snapshot, identity_for, imports):
        """Invalidate stale closure receipts and return unsealed predecessors."""
        def closure(name):
            seen=set();pending=[name]
            while pending:
                current=pending.pop()
                if current in seen:continue
                seen.add(current);pending.extend(imports.get(current,()))
            return seen
        members=(set(imports)|{'Vero_All'} if theory=='Vero_All'
            else closure(theory))
        # Parents first, so an old child cannot authorize a mutable parent.
        ordered=sorted(members,key=lambda name:(len(closure(name)),name))
        hashes={name:hashlib.sha256(data).hexdigest() for name,data in snapshot.items()}
        for member in ordered:
            if member not in self.seals:continue
            current_identity=identity_for(member,snapshot)
            member_closure=(set(imports)|{'Vero_All'} if member=='Vero_All'
                else closure(member))
            scoped={name+'.thy':hashes[name+'.thy'] for name in member_closure}
            if 'Vero_Support.thy' in hashes:
                scoped['Vero_Support.thy']=hashes['Vero_Support.thy']
            self.sealed_result(member,current_identity,source_hashes=scoped)
            if member in self.seals:
                missing=sorted(member_closure-{member}-self.seals.keys())
                if missing:
                    self.invalidate_seal(member,current_identity,
                        reason='unsealed_imports',missing_imports=missing)
        return sorted(members-{theory}-self.seals.keys())

    def drafts(self):
        from vero.isabelle.benchmark import extract
        from vero.isabelle.slots import schedule
        if not self.workspace.exists():return
        files={c['module'] for c in self.bench['contracts']}
        fingerprint={}
        for module in files:
            p=self.workspace/(module+'.thy')
            if p.is_symlink():raise ValueError('symlink in VERO workspace')
            if p.exists():
                st=p.stat();fingerprint[module]=(st.st_mtime_ns,st.st_size)
        if fingerprint==self.fingerprints:return
        artifact=extract(self.bench,self.workspace)
        for key,(theory,kind) in schedule(self.bench).items():
            slot=artifact['slots'][key]
            if not slot['found']:continue
            body=slot['body'].strip()
            if kind=='imports':continue
            uid=key.split(':')[0] if kind=='proof' else 'helper-slot:'+key
            self.stop.observe(uid,body,kind='draft:'+key,theory=theory,baseline=body in ('','oops'))
        self.fingerprints=fingerprint

    def usage_events(self, path, invocation, *, final=False):
        path=Path(path)
        if not path.exists():return
        if path.is_symlink():raise ValueError('symlink in provider accounting')
        offset=self.offsets.get(path,0)
        if path.stat().st_size<offset:raise ValueError('provider accounting truncated')
        with path.open('rb') as stream:
            stream.seek(offset)
            while True:
                line=stream.readline()
                if not line or not line.endswith(b'\n'):break
                event=json.loads(line)
                if event.get('type')=='thread.started':self.bound_threads[invocation]=event['thread_id']
                if event.get('type')=='turn.completed':
                    usage=event.get('usage',{})
                    keys={'inputTokens':'input_tokens','cachedInputTokens':'cached_input_tokens',
                        'cacheWriteInputTokens':'cache_write_input_tokens','outputTokens':'output_tokens',
                        'reasoningOutputTokens':'reasoning_output_tokens','totalTokens':'total_tokens'}
                    current={k:usage.get(v) for k,v in keys.items()}
                    if current['totalTokens'] is None and all(current[k] is not None for k in ('inputTokens','outputTokens')):
                        current['totalTokens']=current['inputTokens']+current['outputTokens']
                    prior=self.pending_usage.get(invocation)
                    self.pending_usage[invocation]={k:(v if prior is None else None if v is None or prior[k] is None else v+prior[k]) for k,v in current.items()}
                self.offsets[path]=stream.tell()
        thread=self.bound_threads.get(invocation)
        if invocation.startswith('planning'):
            scope={'target_ids':[],'theories':[],'theory':None,'phase':'planning',
                'basis':'unattributed repository planning'}
        else:
            scope=dict(self.scope)
        if self.home and thread:
            if invocation not in self.readers:
                self.readers[invocation]=RolloutUsageReader(self.home,{'thread_id':thread},self.ledger,snapshots=self.usage)
            self.readers[invocation].poll(scope=scope,invocation=invocation,model=None,effort=None)
        reader=self.readers.get(invocation)
        if final and invocation not in self.finalized:
            self.finalized.add(invocation)
            fallback=self.pending_usage.get(invocation)
            if fallback is not None:
                self.ledger.append('cli_usage_receipt',invocation_id=invocation,thread_id=thread,
                    usage=fallback,accounting='fallback only; never add to rollout totals')
                if reader is None or thread not in reader.snapshot_threads:
                    self.usage.record(thread or invocation,fallback,scope=scope,source='exec final turn totals fallback')
        updated=getattr(self.stop,'usage_updated',None)
        if callable(updated):updated()

    @contextmanager
    def _publication(self, theory, rows):
        """Admit a fully prepared result atomically with the budget watchdog.

        Parsing and hashing must finish before entering this boundary. Only the
        existing authoritative proof event is published here; GREEN observation
        follows its successful durable write. Older clock adapters without this
        API retain their historical behavior.
        """
        guard=getattr(self.stop,'check_publication',None)
        if not callable(guard):
            yield
            return
        with self.stop.lock:
            state=self.stop.theories.get(theory,{})
            previous=set(state.get('green_contract_ids',()))
            green={node['uid'] for node in rows if node['kind']=='contract'
                and node['status'] in ('green','sealed')}
            guard(theory,green-previous)
            yield

    def _observe_published(self, theory, rows):
        """Run under _publication, only after proof_commit is durable."""
        self.nodes[theory]=rows
        for node in rows:
            self.stop.observe(node['uid'],node,kind='validation',theory=theory,
                baseline=node['status']=='open')

    def checked(self, theory, result, source):
        self.select_theory(theory)
        ids={c['id'] for c in self.bench['contracts'] if c['module']==theory}
        audited=set(result['audited'])
        rows=[]
        for uid in sorted(ids):
            status=('green' if uid+':prove' in audited and
                not result.get('certification_blocked_by') else 'open')
            rows.append({'uid':uid,'kind':'contract','theory':theory,'status':status})
        # Every auxiliary declaration has been kernel checked when its module succeeds.
        from vero.isabelle.checker import strip_comments
        declarations=re.sub(r'"(?:\\.|[^"\\])*"', '""', strip_comments(source))
        names=re.findall(r'\b(?:lemma|theorem|corollary|proposition)\s+(vero_aux_[A-Za-z0-9_]+)', declarations)
        rows.extend({'uid':'helper:'+theory+':'+n,'kind':'helper','theory':theory,
            'status':'green' if result.get('contract_status',result['status'])=='ok'
                and not result.get('certification_blocked_by') else 'open'}
            for n in sorted(set(names)))
        if not rows:return
        commit_id=digest(result)
        native_elapsed=result.get('native_elapsed_seconds',result['elapsed_seconds'])
        with self._publication(theory,rows):
            self.ledger.append('proof_commit',theory=theory,nodes=rows,commit_id=commit_id,
                evidence=result['native_evidence'],seal_evidence=result.get('build_evidence'))
            self._observe_published(theory,rows)
        self.ledger.append('check_completed',theory=theory,target_ids=[r['uid'] for r in rows],
            elapsed_seconds=native_elapsed,
            outcome='blocked_dependency' if result.get('certification_blocked_by')
                else result.get('contract_status',result['status']),
            native_elapsed_seconds=native_elapsed,seal_elapsed_seconds=0,
            timing_basis='native cold contract check only; automatic seal is a separate seal_result event')

    def evaluated(self, report, modules=None):
        """Evaluator-only rejections/blocked consumers must invalidate stale counts."""
        if self.stop.event.is_set():
            self.ledger.append('late_evaluation',report_sha256=digest(report));return
        scope=set(modules) if modules is not None else {c['module'] for c in self.bench['contracts']}
        for theory in sorted(scope):
            rows=[{'uid':c['id'],'kind':'contract','theory':theory,
                'status':'green' if report['contracts'][c['id']]['status']=='proved' else 'open'}
                for c in self.bench['contracts'] if c['module']==theory]
            module=report['modules'].get(theory,{})
            ok=module.get('status')=='ok'
            if module.get('contract_status')=='ok' and module.get('seal_status')=='seal_failed':
                for row in rows:
                    if row['kind']=='contract' and any(n['uid']==row['uid'] and n['status'] in ('green','sealed')
                            for n in self.nodes.get(theory,[])):
                        row['status']='green'
            rows.extend({**n,'status':n['status'] if ok else 'open'}
                for n in self.nodes.get(theory,[]) if n['kind']=='helper')
            if rows==self.nodes.get(theory):continue
            commit_id=digest({'report':report,'theory':theory})
            report_sha256=digest(report)
            with self._publication(theory,rows):
                self.ledger.append('proof_commit',theory=theory,nodes=rows,
                    commit_id=commit_id,authority='original-vero-evaluator',
                    report_sha256=report_sha256)
                self._observe_published(theory,rows)
