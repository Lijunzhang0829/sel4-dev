"""Trusted VERO-rendered theories on the shared native execution backend.

This adapter does not accept arbitrary model source. VERO must validate/extract
slots and render immutable templates before calling it. Its evaluator retains
authority over audited target IDs; a zero exit code alone never proves a slot.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
from uuid import uuid4

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.infrastructure.resource_budget import clone_heaps
from regen.preparation.checker_context import load_context


class VeroCheckerAdapter:
    def __init__(self, dataset, output, *, timeout=900, observer=None):
        self.root = Path(output).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.context = load_context(Path(dataset))
        self.timeout = timeout
        self.observer = observer
        self.resources = NativeResources(1, 4, 1)
        self.lock = threading.RLock()
        self.closed = False
        self.frozen_plan = None
        durable(self.root/'binding.json', {
            'backend':'common-application-vero-v1',
            'checker_context_id':self.context['checker_context_id'],
            'resources':self.resources.policy, 'timeout':timeout,
            'trust_boundary':'VERO validated and freshly rendered source only'})
        durable(self.root/'heap-copy.json', clone_heaps(self.context['heaps'], self.root/'heaps'))

    def set_frozen_plan(self, binding):
        """Bind seal reuse to the accepted plan before any proof check."""
        imports=binding.get('imports')
        if (not isinstance(imports,dict) or
                any(not isinstance(name,str) or not isinstance(providers,list) or
                    any(not isinstance(provider,str) for provider in providers)
                    for name,providers in imports.items())):
            raise ValueError('invalid frozen plan imports')
        required=('plan_sha256','allocation_sha256','imports_sha256')
        if any(not isinstance(binding.get(key),str) or len(binding[key])!=64 for key in required):
            raise ValueError('invalid frozen plan identity')
        value={'imports':{name:tuple(sorted(providers))
                for name,providers in sorted(imports.items())},
            **{key:binding[key] for key in required}}
        if self.frozen_plan is not None and self.frozen_plan!=value:
            raise ValueError('checker plan is already frozen')
        self.frozen_plan=value

    def _seal_source_identity(self,theory,snapshot):
        """Identify exactly the sealed generated import closure, not siblings."""
        plan=getattr(self,'frozen_plan',None)
        if plan is None:
            # Compatibility for isolated adapter tests. Production binds the
            # accepted plan before exposing the checker service.
            closure={theory};imports={theory:()};plan_identity=None
        else:
            graph=plan['imports']
            roots=([theory]+sorted(graph)) if theory=='Vero_All' else [theory]
            closure=set();pending=list(roots)
            while pending:
                current=pending.pop()
                if current in closure:continue
                closure.add(current)
                pending.extend(graph.get(current,()))
            imports={name:graph.get(name,()) for name in sorted(closure)}
            plan_identity={key:plan[key] for key in
                ('plan_sha256','allocation_sha256','imports_sha256')}
        missing=sorted(name for name in closure if name+'.thy' not in snapshot)
        if missing:
            raise ValueError('frozen seal closure source absent: '+', '.join(missing))
        support=({'Vero_Support':hashlib.sha256(
            snapshot['Vero_Support.thy']).hexdigest()}
            if 'Vero_Support.thy' in snapshot else {})
        payload={'theory':theory,'imports':imports,
            'sources':{name:hashlib.sha256(snapshot[name+'.thy']).hexdigest()
                for name in sorted(closure)},
            'support':support,'checker_context_id':self.context['checker_context_id'],
            'backend':'common-application-vero-v1','frozen_plan':plan_identity}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,
            separators=(',',':')).encode()).hexdigest()

    def check(self, directory, theory, timeout=None):
        with self.lock:
            if self.closed:
                raise RuntimeError('checker is closed')
            if not isinstance(theory,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',theory):
                raise ValueError('invalid theory name')
            source = Path(directory).resolve()
            files = sorted(source.glob('*.thy'))
            if not files or any(p.is_symlink() or not p.is_file() for p in files):
                raise ValueError('rendered source must contain regular theory files')
            snapshot = {p.name:p.read_bytes() for p in files}
            if theory+'.thy' not in snapshot:
                raise ValueError('theory absent from rendered source')
            source_identity=self._seal_source_identity(theory,snapshot)
            observer=getattr(self,'observer',None)
            imports=(self.frozen_plan or {}).get('imports',{})
            reconcile=getattr(observer,'reconcile_seals',None) if observer else None
            missing_imports=(reconcile(theory,snapshot,self._seal_source_identity,imports)
                if callable(reconcile) and hasattr(type(observer),'reconcile_seals') else [])
            sealed_result=None
            sealed_lookup=getattr(observer,'sealed_result',None) if observer else None
            # unittest.mock synthesizes arbitrary attributes. Only a real
            # observer implementation may authorize reuse.
            if callable(sealed_lookup) and hasattr(type(observer),'sealed_result'):
                sealed_result=sealed_lookup(theory,source_identity)
            request = self.root/'checks'/uuid4().hex
            request.mkdir(parents=True)
            for name, data in snapshot.items():
                (request/name).write_bytes(data)
            durable(request/'source-manifest.json', {
                name:hashlib.sha256(data).hexdigest() for name,data in snapshot.items()})
            c=self.context
            checker=IsabelleChecker(self.root,request,image=c['image'],generated_dir=request,
                heaps_dir=self.root/'heaps',l4v_dir=c['l4v'],
                semantic_workspace=c['semantic_workspace'],isabelle_dir=c['isabelle'],
                base_session=c['base_session'],base_import=c['base_import'],
                base_catalog=c['base_catalog'],timeout=self.timeout)
            from regen.execution.check_tasks import CURRENT_TASK, Cancellation
            if observer:observer.stop.check()
            cancellation=CURRENT_TASK.set(Cancellation(event=observer.stop.event)) if observer else None
            token=CURRENT_RESOURCES.set(self.resources)
            try:
                evidence=checker._check(snapshot[theory+'.thy'].decode(),theory,
                    timeout=self.timeout if timeout is None else min(timeout,self.timeout))
                log=Path(evidence['log']).read_text()
                if any((request/name).read_bytes()!=data for name,data in snapshot.items()):
                    raise ValueError('rendered snapshot mutated during native check')
                native_rc=evidence['returncode']
                build=None
                observer=getattr(self,'observer',None)
                ids={c['id']+':prove' for c in observer.bench['contracts'] if c['module']==theory} if observer else set()
                audited={cid+':'+kind for cid,kind in re.findall(r'^VERO_RESULT ([ch]\d{4}) (prove|disprove) clean$',log,re.M)}
                contract_status='ok' if native_rc==0 else ('timeout' if native_rc==124 else 'build_error')
                audited_ids=sorted(audited) if native_rc==0 else []
                # Publish the native contract result before attempting a seal.
                # This makes an authoritative first GREEN at the exact effective
                # deadline eligible to win, and keeps seal evidence independent.
                native_result={'status':contract_status,'contract_status':contract_status,
                    'seal_status':'not_attempted','returncode':native_rc,'seal_returncode':None,
                    'source_identity':source_identity,'seal_reused':False,
                    'certification_blocked_by':missing_imports,
                    'native_elapsed_seconds':evidence['wall_ms']/1000,
                    'seal_elapsed_seconds':0,'elapsed_seconds':evidence['wall_ms']/1000,
                    'audited':audited_ids,'log':log,
                    'backend':'common-application-vero-v1','native_evidence':evidence,
                    'build_evidence':None}
                native_published=False
                if observer:
                    if observer.stop.event.is_set():
                        observer.ledger.append('late_check_result',theory=theory,
                            phase='native',evidence=str(request/'source-manifest.json'))
                    else:
                        observer.checked(theory,native_result,snapshot[theory+'.thy'].decode())
                        native_published=True
                seal_ready=bool(ids) and ids<=audited
                seal_requested=native_rc==0 and (theory=='Vero_All' or seal_ready)
                reused_seal=bool(sealed_result) and seal_requested and not missing_imports
                if seal_requested and missing_imports and observer:
                    observer.ledger.append('seal_deferred',theory=theory,
                        source_identity=source_identity,
                        missing_imports=missing_imports,
                        reason='fixed imported theories are not sealed')
                if seal_requested and not missing_imports and not reused_seal:
                    if observer:
                        observer.stop.check()
                        observer.ledger.append('seal_attempt',theory=theory,
                            source_identity=source_identity,
                            source_manifest=str(request/'source-manifest.json'))
                    begin=getattr(observer.stop,'begin_exclusion',None) if observer else None
                    end=getattr(observer.stop,'end_exclusion',None) if observer else None
                    exclusion=begin('seal') if callable(begin) else None
                    try:
                        build=checker.build_generated_session([theory])
                    finally:
                        if exclusion is not None and callable(end):end(exclusion)
                    log += '\n'+Path(build['log']).read_text()
                seal_status=('sealed' if reused_seal or build and build['returncode']==0
                    else 'seal_failed' if build else
                    'waiting_for_imports' if seal_requested and missing_imports
                    else 'not_attempted')
                # A failed seal must block dependent modules even though the
                # already published native contract GREEN remains authoritative.
                status=('build_error' if build and build['returncode']!=0
                    else 'blocked_dependency' if native_rc==0 and missing_imports
                    else contract_status)
                result={'status':status,'contract_status':contract_status,'seal_status':seal_status,
                    'returncode':native_rc,
                    'seal_returncode':build['returncode'] if build else 0 if reused_seal else None,
                    'source_identity':source_identity,'seal_reused':reused_seal,
                    'certification_blocked_by':missing_imports,
                    'source_manifest':str(request/'source-manifest.json'),
                    'native_elapsed_seconds':evidence['wall_ms']/1000,
                    'seal_elapsed_seconds':build['wall_ms']/1000 if build else 0,
                    'elapsed_seconds':(evidence['wall_ms']+(build['wall_ms'] if build else 0))/1000,
                    'audited':audited_ids,
                    'log':log,'backend':'common-application-vero-v1','native_evidence':evidence,
                    'build_evidence':build if build else
                        sealed_result.get('evidence') if reused_seal else None}
                durable(request/'result.json',result)
                if observer:
                    if observer.stop.event.is_set():
                        if build:
                            observer.ledger.append('late_check_result',theory=theory,
                                phase='seal',evidence=str(request/'result.json'))
                    else:
                        if not native_published:
                            observer.checked(theory,native_result,snapshot[theory+'.thy'].decode())
                        if build:
                            observer.ledger.append('seal_result',theory=theory,
                                status=seal_status,elapsed_seconds=build['wall_ms']/1000,
                                source_identity=source_identity,
                                source_manifest=str(request/'source-manifest.json'),evidence=build)
                            if seal_status=='sealed' and theory!='Vero_All':
                                completed=getattr(observer,'seal_completed',None)
                                if callable(completed):completed(theory,result)
                                else:
                                    mark_sealed=getattr(observer.stop,'mark_sealed',None)
                                    if callable(mark_sealed):mark_sealed(theory)
                            observer.ledger.append(seal_status,theory=theory,
                                source_identity=source_identity,evidence=build)
                        elif reused_seal:
                            observer.ledger.append('seal_reused',theory=theory,
                                source_identity=source_identity,
                                sealed_event_id=sealed_result.get('event_id'))
                        if theory=='Vero_All':observer.ledger.append('integrated',verified=seal_status=='sealed',evidence=result)
                return result
            finally:
                checker.close_prefix()
                CURRENT_RESOURCES.reset(token)
                if cancellation is not None:CURRENT_TASK.reset(cancellation)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.resources.close()
            # Only the private heap clone owned by this fresh adapter directory.
            heaps=self.root/'heaps'
            if heaps.is_dir() and not heaps.is_symlink():
                shutil.rmtree(heaps)
            self.closed=True

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()
