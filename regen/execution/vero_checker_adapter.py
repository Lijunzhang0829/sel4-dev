"""Trusted VERO-rendered theories on the shared native execution backend.

This adapter does not accept arbitrary model source. VERO must validate/extract
slots and render immutable templates before calling it. Its evaluator retains
authority over audited target IDs; a zero exit code alone never proves a slot.
"""
import hashlib
import json
import os
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
from uuid import uuid4

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.vero_import_graph import (
    attributable_import_failure, conservative_missing_imports,
    effective_generated_imports,
)
from regen.execution.proof_commits import durable
from regen.infrastructure.resource_budget import clone_heaps
from regen.preparation.checker_context import load_context

MAX_EXACT_GREEN_CHECKS = 128
_PROOF_START = re.compile(rb'\(\* VERO START ([ch]\d{4}) (prove|disprove) \*\)')


def _accepted_proof_prefix(source, end, ignored_slots=None):
    """Hash a checked prefix with only its originally open proof slots masked.

    This is a versioned accepted-source projection, not a claim that the
    current draft was checked. Helpers, declarations and accepted proof bodies
    remain byte-bound. Missing or malformed old slots cannot be projected.
    """
    selected=None if ignored_slots is None else set(ignored_slots)
    if selected is not None and len(selected)!=len(ignored_slots):return None,[]
    pieces=[];cursor=0;masked=[]
    for marker in _PROOF_START.finditer(source[:end]):
        uid,polarity=(part.decode('ascii') for part in marker.groups())
        key=uid+':'+polarity
        closing=f'(* VERO END {uid} {polarity} *)'.encode()
        body_start=marker.end();body_end=source.find(closing,body_start,end)
        if body_end<0:return None,[]
        body=source[body_start:body_end].strip()
        if (selected is None and body in (b'',b'oops')) or (
                selected is not None and key in selected):
            pieces.extend((source[cursor:body_start],b'\noops\n'))
            cursor=body_end;masked.append(key)
    if selected is not None and set(masked)!=selected:return None,[]
    pieces.append(source[cursor:end])
    return hashlib.sha256(b''.join(pieces)).hexdigest(),masked


class VeroCheckerAdapter:
    def __init__(self, dataset, output, *, timeout=900, observer=None,
                 native_slots=1,
                 shared_host_lock_dir=None, shared_host_slots=1,
                 shared_host_cleanup_timeout=30):
        self.root = Path(output).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.context = load_context(Path(dataset))
        self.timeout = timeout
        self.observer = observer
        if native_slots not in (1,2):
            raise ValueError('VERO native_slots must be 1 or 2')
        self.resources = NativeResources(native_slots, 4, native_slots,
            shared_host_lock_dir=shared_host_lock_dir,
            shared_host_slots=shared_host_slots,
            shared_host_cleanup_timeout=shared_host_cleanup_timeout)
        self.lock = threading.RLock()
        self.closed = False
        self.frozen_plan = None
        # Process-local only: a continuation must establish fresh native evidence.
        self._exact_green_checks = OrderedDict()
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

    def _seal_source_identity(self,theory,snapshot,effective_imports=None):
        """Identify the effective generated import closure, not siblings."""
        plan=getattr(self,'frozen_plan',None)
        if plan is None:
            # Compatibility for isolated adapter tests. Production binds the
            # accepted plan before exposing the checker service.
            closure={theory};imports={theory:()};plan_identity=None
        else:
            graph=plan['imports'] if effective_imports is None else effective_imports
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

    def _canonical_workspace_snapshot(self, source, observer, bench, fallback,
                                      *, workspace_override=None):
        """Render the complete original workspace for cross-theory accounting.

        A local VERO check renders out-of-closure proofs as ``oops``. Those
        placeholders are check inputs, not edits to the shared workspace, and
        must never supersede a sibling's seal or accepted contract receipt.
        Re-rendering extracted slots also gives the same whitespace-normalized
        source representation used by the VERO evaluator.
        """
        frozen=getattr(observer,'audit_workspace_snapshot',None)
        workspace=(workspace_override if workspace_override is not None else
            frozen if isinstance(frozen,(str,Path)) else
            getattr(observer,'workspace',None))
        if (not isinstance(workspace,(str,Path)) or Path(workspace).resolve()==source
                or not isinstance(bench,dict) or not bench.get('benchmark_id')):
            return fallback
        from vero.isabelle.benchmark import extract, render
        artifact=extract(bench,workspace)
        bodies={key:slot['body'] for key,slot in artifact['slots'].items()
            if slot['found']}
        self.root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='full-workspace-audit-',dir=self.root) as tmp:
            render(bench,tmp,bodies)
            return {path.name:path.read_bytes() for path in Path(tmp).glob('*.thy')}

    @staticmethod
    def _read_planned_raw_sources(directory, names):
        """Read a fixed theory inventory without following model-owned links."""
        def read(directory,name):
            try:
                fd=os.open(Path(directory)/name,
                    os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            except FileNotFoundError:
                return None
            except OSError as error:
                raise ValueError('cannot read generated source during native check: '+name) from error
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>10*1024*1024:
                    raise ValueError('invalid generated source during native check: '+name)
                body=stream.read(10*1024*1024+1)
                if len(body)>10*1024*1024:
                    raise ValueError('oversized generated source during native check: '+name)
                return body
        return {name:data for name in sorted(names)
            if (data:=read(directory,name)) is not None}

    @classmethod
    def _read_planned_raw_sources_with_errors(cls,directory,names):
        """Keep readable siblings when one raw theory is a symlink or invalid."""
        sources={};errors={}
        for name in sorted(names):
            try:sources.update(cls._read_planned_raw_sources(directory,{name}))
            except (OSError,ValueError) as error:
                errors[name[:-4]]=str(error)
        return sources,errors

    def _assert_workspace_snapshot_unchanged(self, observer):
        """Reject a local check whose model-writable source changed in flight.

        Service snapshots only planned generated modules, so compare that fixed
        inventory, including files absent when the snapshot was captured. The
        model cannot authorize native evidence for an older version of its
        shared workspace by editing a source during a long Isabelle check.
        """
        frozen=getattr(observer,'audit_workspace_snapshot',None)
        workspace=getattr(observer,'workspace',None)
        bench=getattr(observer,'bench',None)
        if (not isinstance(frozen,(str,Path)) or
                not isinstance(workspace,(str,Path)) or
                not isinstance(bench,dict)):
            return
        if Path(frozen).is_symlink() or not Path(frozen).is_dir():
            raise ValueError('invalid immutable raw source snapshot')
        names={c['module']+'.thy' for c in bench.get('contracts',[])}
        old=self._read_planned_raw_sources(frozen,names)
        current=self._read_planned_raw_sources(workspace,names)
        for name in sorted(names):
            if old.get(name)!=current.get(name):
                raise ValueError('raw workspace changed during native check: '+name)

    def _reconcile_changed_raw_workspace(self, observer, source, theory,
                                         bench, frozen_imports, snapshot, *, reason):
        """Audit old and newly published receipts when raw source changes.

        A seal build may run after the first contract GREEN has been recorded.
        If the agent edits its workspace during that build, the stale seal must
        never be published. Still, a later independent helper edit must not
        erase an earlier contract whose checked prefix and imports are intact.
        """
        workspace=getattr(observer,'workspace',None)
        frozen=getattr(observer,'audit_workspace_snapshot',None)
        if (not isinstance(workspace,(str,Path)) or
                not isinstance(frozen,(str,Path)) or not isinstance(bench,dict)):
            return
        names={c['module']+'.thy' for c in bench.get('contracts',[])}
        old=self._read_planned_raw_sources(frozen,names)
        # This private copy makes extraction and reconciliation refer to the
        # same observed bytes even if the agent keeps editing the workspace.
        with tempfile.TemporaryDirectory(prefix='post-native-raw-',dir=self.root) as tmp:
            fresh=Path(tmp)
            current,read_errors=self._read_planned_raw_sources_with_errors(
                workspace,names)
            changed={name[:-4] for name in names
                if old.get(name)!=current.get(name)}
            for name,data in current.items():
                (fresh/name).write_bytes(data)
            require_rendered=bool(bench.get('schema_version',1)>=2)
            try:
                raw_imports=effective_generated_imports(current,frozen_imports)
            except (ValueError,UnicodeError) as error:
                invalid_theories,trusted_imports=attributable_import_failure(
                    current,frozen_imports)
                observer.source_invalid(reason=type(error).__name__,
                    requested_theory=theory,
                    candidate_theories=observer.candidate_theories_for_snapshot(fresh),
                    invalid_theories=invalid_theories,imports=trusted_imports)
                if not invalid_theories or trusted_imports is None:
                    # A cycle or unknown culprit has already revoked every
                    # current certificate. No selective audit is possible.
                    return
                raw_imports=trusted_imports
                # Keep the bad bytes in `current` as evidence, but exclude
                # those modules from this private extraction copy so another
                # independent edited theory can still have its seal audited.
                for invalid_theory in invalid_theories:
                    (fresh/(invalid_theory+'.thy')).unlink(missing_ok=True)
            missing=getattr(observer,'source_missing',None)
            if callable(missing):
                missing(current,raw_imports,theory,support_required=False)
            from vero.isabelle.benchmark import extract
            try:artifact=extract(bench,fresh)
            except (OSError,UnicodeError,ValueError):
                observer.source_extraction_invalid(
                    {name:'raw_extraction_exception' for name in changed or
                        {member[:-4] for member in names}},raw_imports,
                    snapshot_dir=fresh,reason=reason)
                raise
            file_errors={**(artifact.get('file_errors') or {}),**read_errors}
            if file_errors:
                observer.source_extraction_invalid(file_errors,raw_imports,
                    snapshot_dir=fresh,reason=reason)
            try:
                canonical=self._canonical_workspace_snapshot(source,observer,bench,
                    snapshot,workspace_override=fresh)
            except (OSError,UnicodeError,ValueError):
                observer.source_extraction_invalid(
                    {name:'canonical_raw_render_failed' for name in changed or
                        {member[:-4] for member in names}},
                    raw_imports,snapshot_dir=fresh,reason=reason)
                raise
            try:
                imports=effective_generated_imports(canonical,frozen_imports,
                    require_rendered=require_rendered)
            except (ValueError,UnicodeError) as error:
                invalid_theories,trusted_imports=attributable_import_failure(
                    canonical,frozen_imports,require_rendered=require_rendered)
                observer.source_invalid(reason=type(error).__name__,
                    requested_theory=theory,
                    candidate_theories=observer.candidate_theories_for_snapshot(fresh),
                    invalid_theories=invalid_theories,imports=trusted_imports)
                return
            receipts={name:self._contract_receipts(name,canonical,imports)
                for name in imports if name+'.thy' in current
                    and name not in file_errors}
            guard_error=None
            try:observer.guard_green_proofs(artifact,snapshot_dir=fresh)
            except ValueError as error:
                guard_error=error
                candidates={uid:receipt for theory_receipts in receipts.values()
                    for uid,receipt in theory_receipts.items()}
                illegal={uid:candidates.get(uid) for uid,old in
                    observer.accepted_receipts.items()
                    if observer._owner(uid) not in observer.seal_transactions and
                    (uid not in candidates or
                        any(candidates[uid].get(key)!=old.get(key) for key in
                            ('polarity','filled','proof_body_sha256')))}
                if illegal:
                    observer.invalidate_contract_receipts(theory,illegal,
                        reason='accepted_proof_body_changed_outside_seal')
                else:
                    # A malformed proof slot with no trustworthy candidate
                    # cannot be scoped to an individual accepted contract.
                    observer.source_extraction_invalid(
                        {name:'unscoped_green_proof_edit' for name in changed},
                        imports,snapshot_dir=fresh,reason=reason)
            observer.reconcile_contracts(theory,receipts)
            observer.reconcile_seals(theory,canonical,
                lambda member,contents:self._seal_source_identity(
                    member,contents,imports),imports)
            # If the workspace changed again while it was audited, the newer
            # bytes are unknown. Revoke just the changed theories and their
            # generated-import consumers rather than accept stale evidence.
            latest,latest_errors=self._read_planned_raw_sources_with_errors(
                workspace,names)
            if latest_errors:
                try:latest_imports=effective_generated_imports(latest,frozen_imports)
                except (ValueError,UnicodeError):latest_imports=None
                observer.source_extraction_invalid(
                    latest_errors,latest_imports,reason=reason)
            raced={name[:-4] for name in names
                if latest.get(name)!=current.get(name)
                    and name[:-4] not in latest_errors}
            if raced:
                observer.source_extraction_invalid(
                    {name:'changed_during_post_native_audit' for name in raced},
                    imports,snapshot_dir=fresh,reason=reason)
            if guard_error is not None:raise guard_error

    def _assert_workspace_current_or_reconcile(self, observer, source, theory,
                                               bench, frozen_imports, snapshot,
                                               *, reason):
        """Reconcile existing certificates at every raw snapshot mismatch."""
        try:self._assert_workspace_snapshot_unchanged(observer)
        except (OSError,ValueError):
            # A prior GREEN may already exist even when this check has not
            # published anything. Mock observers never own durable receipts.
            if hasattr(type(observer),'reconcile_contracts'):
                self._reconcile_changed_raw_workspace(observer,source,theory,
                    bench,frozen_imports,snapshot,reason=reason)
            raise

    def _contract_receipts(self,theory,snapshot,imports):
        """Fingerprint each contract's checked prefix and external context.

        These are candidate fingerprints, not GREEN authority. Only a native
        audited result may accept one; a later RED may use them to retain an
        unchanged earlier certificate without accepting a new proof.
        """
        observer=getattr(self,'observer',None)
        bench=getattr(observer,'bench',None)
        if not isinstance(bench,dict):return {}
        contracts=[c for c in bench.get('contracts',[])
            if c.get('module')==theory]
        if not contracts:return {}
        source=snapshot[theory+'.thy']
        support=snapshot.get('Vero_Support.thy')
        dependencies=imports.get(theory,())
        context={'imports':{name:self._seal_source_identity(name,snapshot,imports)
                for name in dependencies},
            'support':hashlib.sha256(support).hexdigest() if support is not None else None,
            'checker_context_id':self.context['checker_context_id'],
            'frozen_plan':{key:self.frozen_plan[key] for key in
                ('plan_sha256','allocation_sha256','imports_sha256')}
                if self.frozen_plan else None,
            'backend':'common-application-vero-v1'}
        context_identity=hashlib.sha256(json.dumps(context,sort_keys=True,
            separators=(',',':')).encode()).hexdigest()
        receipts={}
        for contract in contracts:
            uid=contract['id'];candidates=[]
            for polarity in ('prove','disprove'):
                start=f'(* VERO START {uid} {polarity} *)'.encode()
                end=f'(* VERO END {uid} {polarity} *)'.encode()
                if source.count(start)!=1 or source.count(end)!=1:
                    continue
                begin=source.index(start);body_start=begin+len(start)
                body_end=source.index(end,body_start)
                body=source[body_start:body_end].decode('utf-8').strip()
                candidates.append((polarity,begin,body_end+len(end),body))
            if len(candidates)!=2:
                if bench.get('schema_version',1)>=2 and bench.get('benchmark_id'):
                    raise ValueError('rendered contract markers absent: '+uid)
                continue
            filled=[candidate for candidate in candidates
                if candidate[3] not in ('','oops')]
            # An overfilled contract is not eligible for GREEN. Retain both
            # candidates in the rendered source, but do not invent a receipt.
            if len(filled)>1:continue
            polarity,begin,prefix_end,body=(filled[0] if filled else candidates[0])
            accepted_prefix,open_slots=_accepted_proof_prefix(source,begin)
            if accepted_prefix is None:
                raise ValueError('malformed prior proof slot before '+uid)
            receipts[uid]={'polarity':polarity,
                'filled':bool(filled),
                'pre_proof_sha256':hashlib.sha256(source[:begin]).hexdigest(),
                'proof_body_sha256':hashlib.sha256(body.encode()).hexdigest(),
                'prefix_sha256':hashlib.sha256(source[:prefix_end]).hexdigest(),
                'context_identity':context_identity,
                'accepted_prefix_sha256':accepted_prefix,
                'open_prefix_slots':open_slots}
            old=getattr(observer,'accepted_receipts',{}).get(uid)
            if isinstance(old,dict) and isinstance(old.get('open_prefix_slots'),list):
                projected,_=_accepted_proof_prefix(source,begin,old['open_prefix_slots'])
                receipts[uid]['prior_accepted_prefix_sha256']=projected
        return receipts

    def _exact_green_key(self,theory,snapshot,source_identity,effective_timeout):
        """Permit native reuse only for a complete, frozen execution identity."""
        if (theory=='Vero_All' or self.frozen_plan is None
                or theory not in self.frozen_plan['imports']
                or 'Vero_Support.thy' not in snapshot):
            return None
        bench=getattr(getattr(self,'observer',None),'bench',None)
        if not isinstance(bench,dict) or not isinstance(bench.get('contracts'),list):
            return None
        c=self.context
        required=('checker_context_id','image','l4v','semantic_workspace',
                  'isabelle','heaps','base_session','base_import','base_catalog')
        if any(not c.get(name) for name in required):
            return None
        policy=getattr(self.resources,'policy',None)
        if not isinstance(policy,dict):
            return None
        # Method identities make a monkey-patched/reloaded checker in the same
        # Python process a new code instance. This cache is never serialized.
        binding={'source_identity':source_identity,'theory':theory,
            'context':{name:str(c[name]) for name in required},
            'resources':policy,'timeout':effective_timeout,
            'benchmark':bench,
            'native_method':id(IsabelleChecker._check),
            'native_command':id(IsabelleChecker._command),
            'adapter_method':id(type(self).check)}
        try:
            return hashlib.sha256(json.dumps(binding,sort_keys=True,
                separators=(',',':')).encode()).hexdigest()
        except (TypeError,ValueError):
            return None

    def _reuse_exact_green(self,key):
        if key is None:
            return None
        receipt=getattr(self,'_exact_green_checks',{}).get(key)
        if receipt is None:
            return None
        try:
            evidence=receipt['native_evidence']
            source=Path(evidence['source'])
            log=Path(evidence['log'])
            manifest=Path(receipt['source_manifest'])
            if source.is_symlink() or log.is_symlink() or manifest.is_symlink():
                return None
            log_bytes=log.read_bytes()
            if (evidence['returncode']!=0
                    or hashlib.sha256(source.read_bytes()).hexdigest()!=receipt['source_sha256']
                    or hashlib.sha256(log_bytes).hexdigest()!=receipt['log_sha256']
                    or hashlib.sha256(manifest.read_bytes()).hexdigest()!=receipt['manifest_sha256']):
                return None
            for name,expected in receipt['snapshot_hashes'].items():
                path=manifest.parent/name
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                    return None
            result=deepcopy(receipt)
            result['log_content']=log_bytes.decode()
            self._exact_green_checks.move_to_end(key)
            return result
        except (KeyError,TypeError,ValueError,OSError,UnicodeError):
            return None

    def _remember_exact_green(self,key,request,theory,snapshot,evidence,log):
        if key is None or evidence.get('returncode')!=0:
            return
        source=request/(theory+'.thy')
        native_log=request/(theory+'.log')
        if (evidence.get('source')!=str(source) or evidence.get('log')!=str(native_log)
                or not isinstance(evidence.get('wall_ms'),(int,float))):
            return
        try:
            manifest=request/'source-manifest.json'
            if source.is_symlink() or native_log.is_symlink() or manifest.is_symlink():
                return
            log_bytes=native_log.read_bytes()
            snapshot_hashes={name:hashlib.sha256(data).hexdigest()
                for name,data in snapshot.items()}
            if (source.read_bytes()!=snapshot[theory+'.thy']
                    or log_bytes.decode()!=log
                    or json.loads(manifest.read_text())!=snapshot_hashes):
                return
        except (OSError,UnicodeError,ValueError):
            return
        receipt={'native_evidence':deepcopy(evidence),
            'source_sha256':hashlib.sha256(snapshot[theory+'.thy']).hexdigest(),
            'log_sha256':hashlib.sha256(log_bytes).hexdigest(),
            'source_manifest':str(manifest),
            'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'snapshot_hashes':snapshot_hashes,
            'original_native_elapsed_seconds':evidence['wall_ms']/1000}
        cache=getattr(self,'_exact_green_checks',None)
        if cache is None:
            cache=self._exact_green_checks=OrderedDict()
        cache[key]=receipt
        cache.move_to_end(key)
        if len(cache)>MAX_EXACT_GREEN_CHECKS:
            cache.popitem(last=False)

    def check(self, directory, theory, timeout=None):
        with self.lock:
            if self.closed:
                raise RuntimeError('checker is closed')
            if not isinstance(theory,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',theory):
                raise ValueError('invalid theory name')
            source = Path(directory).resolve()
            files = sorted(source.glob('*.thy'))
            if any(p.is_symlink() or not p.is_file() for p in files):
                raise ValueError('rendered source must contain regular theory files')
            snapshot = {p.name:p.read_bytes() for p in files}
            observer=getattr(self,'observer',None)
            frozen_imports=(self.frozen_plan or {}).get('imports',{})
            missing_hook=getattr(observer,'source_missing',None) if observer else None
            bench=getattr(observer,'bench',{}) if observer else {}
            if observer:
                self._assert_workspace_current_or_reconcile(observer,source,theory,
                    bench,frozen_imports,snapshot,
                    reason='source_changed_before_native_check')
            try:
                imports=effective_generated_imports(snapshot,frozen_imports,
                    require_rendered=bool(isinstance(bench,dict)
                        and bench.get('schema_version',1)>=2))
            except (ValueError,UnicodeError) as error:
                invalid_hook=getattr(observer,'source_invalid',None) if observer else None
                trusted_imports=None
                if (self.frozen_plan is not None and callable(invalid_hook)
                        and hasattr(type(observer),'source_invalid')):
                    frozen_raw=getattr(observer,'audit_workspace_snapshot',None)
                    invalid_theories,trusted_imports=attributable_import_failure(
                        snapshot,frozen_imports,
                        require_rendered=bool(isinstance(bench,dict)
                            and bench.get('schema_version',1)>=2))
                    invalid_hook(reason=type(error).__name__,requested_theory=theory,
                        candidate_theories=(observer.candidate_theories_for_snapshot(
                            frozen_raw) if frozen_raw is not None else ()),
                        invalid_theories=invalid_theories,imports=trusted_imports)
                if (self.frozen_plan is not None and callable(missing_hook)
                        and hasattr(type(observer),'source_missing')):
                    missing_hook(snapshot,trusted_imports or
                        conservative_missing_imports(frozen_imports),
                        theory,support_required=False)
                raise
            if (self.frozen_plan is not None and callable(missing_hook)
                    and hasattr(type(observer),'source_missing')):
                # Support removal has its own manifest-based reconciliation;
                # this hook is for absent generated theory files.
                missing_hook(snapshot,imports,theory,support_required=False)
            if not files:
                raise ValueError('rendered source must contain regular theory files')
            if theory+'.thy' not in snapshot:
                raise ValueError('theory absent from rendered source')
            source_identity=self._seal_source_identity(theory,snapshot,imports)
            # The local renderer masks every out-of-closure proof with oops.
            # Audit old receipts against the complete original workspace,
            # canonically rendered from its slots, while the selected theory
            # keeps the exact snapshot that will reach Isabelle below.
            audit_snapshot=self._canonical_workspace_snapshot(
                source,observer,bench,snapshot) if observer else snapshot
            audit_graph_valid=True
            if audit_snapshot is snapshot:
                audit_imports=imports
            else:
                try:
                    audit_imports=effective_generated_imports(
                        audit_snapshot,frozen_imports,
                        require_rendered=bool(isinstance(bench,dict)
                            and bench.get('schema_version',1)>=2))
                except (ValueError,UnicodeError) as error:
                    invalid_hook=getattr(observer,'source_invalid',None)
                    if callable(invalid_hook) and hasattr(type(observer),'source_invalid'):
                        frozen_raw=getattr(observer,'audit_workspace_snapshot',None)
                        invalid_theories,trusted_imports=attributable_import_failure(
                            audit_snapshot,frozen_imports,
                            require_rendered=bool(isinstance(bench,dict)
                                and bench.get('schema_version',1)>=2))
                        invalid_hook(reason=type(error).__name__,requested_theory=theory,
                            candidate_theories=(observer.candidate_theories_for_snapshot(
                                frozen_raw) if frozen_raw is not None else ()),
                            invalid_theories=invalid_theories,imports=trusted_imports)
                    # The raw graph has no trustworthy cross-theory closure.
                    # Any former authority was revoked by source_invalid;
                    # still allow the local check's own diagnostics to run.
                    audit_graph_valid=False
                    audit_snapshot=snapshot;audit_imports=imports
            candidate_receipts=self._contract_receipts(theory,snapshot,imports)
            reconcile_contracts=getattr(observer,'reconcile_contracts',None) if observer else None
            if (audit_graph_valid and callable(reconcile_contracts)
                    and hasattr(type(observer),'reconcile_contracts')):
                receipts_by_theory={name:self._contract_receipts(name,audit_snapshot,audit_imports)
                    for name in audit_imports if name+'.thy' in audit_snapshot}
                reconcile_contracts(theory,receipts_by_theory)
            reconcile=getattr(observer,'reconcile_seals',None) if observer else None
            identity_for=lambda member,contents:self._seal_source_identity(
                member,contents,audit_imports)
            missing_imports=(reconcile(theory,audit_snapshot,identity_for,audit_imports)
                if audit_graph_valid and callable(reconcile)
                    and hasattr(type(observer),'reconcile_seals') else
                ['invalid_generated_imports'] if not audit_graph_valid else [])
            sealed_result=None
            sealed_lookup=getattr(observer,'sealed_result',None) if observer else None
            # unittest.mock synthesizes arbitrary attributes. Only a real
            # observer implementation may authorize reuse.
            if (audit_graph_valid and callable(sealed_lookup)
                    and hasattr(type(observer),'sealed_result')):
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
            checker.checker_context_id=c['checker_context_id']
            from regen.execution.check_tasks import CURRENT_TASK, Cancellation
            if observer:observer.stop.check()
            cancellation=CURRENT_TASK.set(Cancellation(event=observer.stop.event)) if observer else None
            token=CURRENT_RESOURCES.set(self.resources)
            try:
                effective_timeout=self.timeout if timeout is None else min(timeout,self.timeout)
                exact_key=self._exact_green_key(theory,snapshot,source_identity,effective_timeout)
                prior=self._reuse_exact_green(exact_key)
                reused_exact_check=prior is not None
                if reused_exact_check:
                    evidence=prior['native_evidence']
                    log=prior['log_content']
                else:
                    evidence=checker._check(snapshot[theory+'.thy'].decode(),theory,
                        timeout=effective_timeout)
                    log=Path(evidence['log']).read_text()
                if any((request/name).read_bytes()!=data for name,data in snapshot.items()):
                    raise ValueError('rendered snapshot mutated during native check')
                if observer:
                    self._assert_workspace_current_or_reconcile(observer,source,
                        theory,bench,frozen_imports,snapshot,
                        reason='source_changed_during_native_check')
                native_rc=evidence['returncode']
                native_elapsed=0 if reused_exact_check else evidence['wall_ms']/1000
                reuse_receipt=({'original_source_manifest':prior['source_manifest'],
                    'original_manifest_sha256':prior['manifest_sha256'],
                    'original_source_sha256':prior['source_sha256'],
                    'original_log_sha256':prior['log_sha256'],
                    'original_native_elapsed_seconds':prior['original_native_elapsed_seconds']}
                    if reused_exact_check else None)
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
                    'reused_exact_check':reused_exact_check,'reuse_receipt':reuse_receipt,
                    'certification_blocked_by':missing_imports,
                    'native_elapsed_seconds':native_elapsed,
                    'seal_elapsed_seconds':0,'elapsed_seconds':native_elapsed,
                    'audited':audited_ids,'contract_receipts':candidate_receipts,
                    'log':log,
                    'backend':'common-application-vero-v1','native_evidence':evidence,
                    'build_evidence':None}
                native_published=False
                if observer:
                    # Recheck immediately before the durable proof_commit, not
                    # only immediately after Isabelle returns: preparing the
                    # result above can itself overlap a workspace edit.
                    self._assert_workspace_current_or_reconcile(observer,source,
                        theory,bench,frozen_imports,snapshot,
                        reason='source_changed_before_proof_publication')
                    if observer.stop.event.is_set():
                        observer.ledger.append('late_check_result',theory=theory,
                            phase='native',evidence=str(request/'source-manifest.json'))
                    else:
                        observer.checked(theory,native_result,snapshot[theory+'.thy'].decode())
                        native_published=True
                seal_ready=bool(ids) and ids<=audited
                # A successful Isabelle process is not by itself a VERO
                # contract GREEN. Cache only a published, fully audited native
                # result whose fixed imported theories are available.
                if (not reused_exact_check and native_rc==0 and seal_ready
                        and not missing_imports and native_published):
                    self._remember_exact_green(exact_key,request,theory,snapshot,evidence,log)
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
                if observer:
                    self._assert_workspace_current_or_reconcile(observer,source,
                        theory,bench,frozen_imports,snapshot,
                        reason='source_changed_after_native_publication')
                # A failed seal must block dependent modules even though the
                # already published native contract GREEN remains authoritative.
                status=('build_error' if build and build['returncode']!=0
                    else 'blocked_dependency' if native_rc==0 and missing_imports
                    else contract_status)
                result={'status':status,'contract_status':contract_status,'seal_status':seal_status,
                    'returncode':native_rc,
                    'seal_returncode':build['returncode'] if build else 0 if reused_seal else None,
                    'source_identity':source_identity,'seal_reused':reused_seal,
                    'reused_exact_check':reused_exact_check,'reuse_receipt':reuse_receipt,
                    'certification_blocked_by':missing_imports,
                    'source_manifest':str(request/'source-manifest.json'),
                    'native_elapsed_seconds':native_elapsed,
                    'seal_elapsed_seconds':build['wall_ms']/1000 if build else 0,
                    'elapsed_seconds':native_elapsed+(build['wall_ms'] if build else 0)/1000,
                    'audited':audited_ids,'contract_receipts':candidate_receipts,
                    'log':log,'backend':'common-application-vero-v1','native_evidence':evidence,
                    'build_evidence':build if build else
                        sealed_result.get('evidence') if reused_seal else None}
                durable(request/'result.json',result)
                if observer:
                    self._assert_workspace_current_or_reconcile(observer,source,
                        theory,bench,frozen_imports,snapshot,
                        reason='source_changed_after_native_publication')
                    if reused_exact_check:
                        observer.ledger.append('reused_exact_check',theory=theory,
                            source_identity=source_identity,
                            source_manifest=str(request/'source-manifest.json'),
                            reuse_receipt=reuse_receipt,native_elapsed_seconds=0)
                    if observer.stop.event.is_set():
                        if build:
                            observer.ledger.append('late_check_result',theory=theory,
                                phase='seal',evidence=str(request/'result.json'))
                    else:
                        if not native_published:
                            self._assert_workspace_current_or_reconcile(observer,
                                source,theory,bench,frozen_imports,snapshot,
                                reason='source_changed_before_proof_publication')
                            observer.checked(theory,native_result,snapshot[theory+'.thy'].decode())
                        if build:
                            self._assert_workspace_current_or_reconcile(observer,
                                source,theory,bench,frozen_imports,snapshot,
                                reason='source_changed_before_seal_publication')
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
                            # seal_result and the completion callback both
                            # perform durable work and can overlap a model edit.
                            # When a seal_result is already on disk, this
                            # post-check writes a later invalidation before
                            # returning, so replay cannot resurrect it.
                            self._assert_workspace_current_or_reconcile(observer,
                                source,theory,bench,frozen_imports,snapshot,
                                reason='source_changed_after_seal_publication')
                            observer.ledger.append(seal_status,theory=theory,
                                source_identity=source_identity,evidence=build)
                        elif reused_seal:
                            self._assert_workspace_current_or_reconcile(observer,
                                source,theory,bench,frozen_imports,snapshot,
                                reason='source_changed_before_seal_reuse')
                            observer.ledger.append('seal_reused',theory=theory,
                                source_identity=source_identity,
                                sealed_event_id=sealed_result.get('event_id'))
                            self._assert_workspace_current_or_reconcile(observer,
                                source,theory,bench,frozen_imports,snapshot,
                                reason='source_changed_after_seal_reuse')
                        if theory=='Vero_All':observer.ledger.append('integrated',verified=seal_status=='sealed',evidence=result)
                    # Keep the last check adjacent to the returned result:
                    # telemetry callbacks above may themselves overlap edits.
                    self._assert_workspace_current_or_reconcile(observer,source,
                        theory,bench,frozen_imports,snapshot,
                        reason='source_changed_after_check_publication')
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
