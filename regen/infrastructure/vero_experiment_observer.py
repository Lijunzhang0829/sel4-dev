"""Passive VERO draft/usage observation; never supplies proof advice."""
import hashlib
import json
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import re
from uuid import uuid4
from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_activity import UsageSnapshots
from regen.infrastructure.experiment_ledger import RolloutUsageReader


class VeroObserver:
    def __init__(self, ledger, stop, workspace, bench):
        self.ledger,self.stop,self.workspace,self.bench=ledger,stop,Path(workspace),bench
        self.usage=UsageSnapshots(ledger);self.offsets={};self.fingerprints={};self.bound_threads={}
        self.nodes={};self.home=None;self.readers={};self.pending_usage={};self.finalized=set()
        self.seals={}
        # Native proof receipts, unlike an evaluator's latest draft status,
        # survive a later failed check of an unrelated sibling declaration.
        self.accepted_receipts={}
        self.accepted_origins={}
        self.pending_seal_receipts={}
        self.seal_transactions=set()
        self._seal_transaction_ids={}
        self.seal_base_manifests={}
        for row in ledger.rows:
            kind=row['kind'];data=row['data']
            if kind=='proof_commit':
                theory=data.get('theory')
                if isinstance(theory,str):
                    self.nodes[theory]=deepcopy(data.get('nodes',[]))
                    if theory in self.seal_transactions:
                        staged={node['uid']:{'receipt':deepcopy(node['seal_candidate_receipt']),
                            'evidence_event_id':row['event_id'],
                            'source_identity':data.get('source_identity')}
                            for node in data.get('nodes',[])
                            if node.get('kind')=='contract' and
                                isinstance(node.get('seal_candidate_receipt'),dict)}
                        self.pending_seal_receipts[theory]=staged
                for node in data.get('nodes',[]):
                    uid=node.get('uid')
                    if node.get('kind')!='contract' or not isinstance(uid,str):continue
                    receipt=node.get('accepted_receipt')
                    if node.get('status') in ('green','sealed') and isinstance(receipt,dict):
                        self.accepted_receipts[uid]=deepcopy(receipt)
                        self.accepted_origins[uid]=(node.get('accepted_evidence_event_id')
                            or row['event_id'])
                    elif node.get('status') not in ('green','sealed'):
                        self.accepted_receipts.pop(uid,None)
                        self.accepted_origins.pop(uid,None)
            elif kind=='contract_green_invalidated':
                self.accepted_receipts.pop(data.get('target_id'),None)
                self.accepted_origins.pop(data.get('target_id'),None)
            elif kind in ('source_missing','source_invalid','source_extraction_invalid'):
                affected=(set(data.get('affected_theories',()))-
                    set(data.get('preserved_contract_theories',())))
                for uid in list(self.accepted_receipts):
                    if self._owner(uid) in affected:
                        self.accepted_receipts.pop(uid,None)
                        self.accepted_origins.pop(uid,None)
            elif kind in ('plan_accepted','plan_restored'):
                self.accepted_receipts.clear();self.accepted_origins.clear();self.nodes.clear()
            elif kind=='seal_edit_begin':
                theory=data.get('theory');transaction=data.get('transaction_id')
                if theory in self.seal_transactions or not isinstance(transaction,str):
                    raise ValueError('invalid or duplicate seal edit transaction')
                self.seal_transactions.add(theory);self._seal_transaction_ids[theory]=transaction
                self.pending_seal_receipts[theory]={}
                manifest=data.get('source_manifest')
                if manifest is not None:
                    if not self._valid_source_manifest(manifest):
                        raise ValueError('seal edit raw source manifest is invalid')
                    self.seal_base_manifests[theory]=deepcopy(manifest)
            elif kind=='seal_edit_end':
                theory=data.get('theory')
                if self._seal_transaction_ids.get(theory)!=data.get('transaction_id'):
                    raise ValueError('unmatched seal edit transaction end')
                self.seal_transactions.discard(theory);self._seal_transaction_ids.pop(theory)
                self.pending_seal_receipts.pop(theory,None)
                self.seal_base_manifests.pop(theory,None)
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
            if theory=='Vero_All':
                # Integration is not a budget-owned theory seal. It is rebuilt
                # on recovery, rather than reusing a receipt we cannot revoke.
                continue
            old=self.seals.get(theory)
            if old is not None and old['source_identity']!=identity:
                raise ValueError('conflicting sealed theory source identities')
            self.seals[theory]={'source_identity':identity,
                'evidence':deepcopy(evidence),'event_id':row['event_id'],
                'source_manifest':data.get('source_manifest') or attempts.get((theory,identity))}
        for theory in sorted(self.seal_transactions & self.seals.keys()):
            self._promote_seal_candidates(theory,self.seals[theory]['source_identity'])
        mark=getattr(stop,'mark_sealed',None)
        theories=getattr(stop,'theories',{})
        if callable(mark):
            for theory in sorted(self.seals):
                if theory in theories:mark(theory)
        # A crash can land after the native seal_result but before the small
        # seal_edit_end marker. The successful content-bound seal is already
        # durable authority, so close that transaction idempotently on replay.
        for theory in sorted(self.seal_transactions & self.seals.keys()):
            self.finish_seal_transaction(theory,'sealed')
        self.scope={'target_ids':[],'theories':[],'theory':None,
            'phase':'proof','basis':'unattributed'}

    def _owner(self, uid):
        return next((c['module'] for c in self.bench.get('contracts',())
            if c['id']==uid),None)

    @staticmethod
    def _body_hash(body):
        return hashlib.sha256(body.strip().encode('utf-8')).hexdigest()

    @staticmethod
    def _valid_receipt(receipt):
        base=(isinstance(receipt,dict) and receipt.get('polarity')=='prove'
            and receipt.get('filled') is True and all(
                isinstance(receipt.get(key),str) and
                re.fullmatch(r'[0-9a-f]{64}',receipt[key]) is not None
                for key in ('pre_proof_sha256','proof_body_sha256',
                    'prefix_sha256','context_identity')))
        if not base:return False
        if 'accepted_prefix_sha256' not in receipt and 'open_prefix_slots' not in receipt:
            return True  # Replaying a receipt from an older frozen release.
        slots=receipt.get('open_prefix_slots')
        return (isinstance(receipt.get('accepted_prefix_sha256'),str)
            and re.fullmatch(r'[0-9a-f]{64}',receipt['accepted_prefix_sha256']) is not None
            and isinstance(slots,list) and all(isinstance(slot,str) and
                re.fullmatch(r'[ch]\d{4}:(?:prove|disprove)',slot) is not None
                for slot in slots) and len(slots)==len(set(slots)))

    @staticmethod
    def _same_accepted_proof(old,candidate):
        if not isinstance(old,dict) or not isinstance(candidate,dict):return False
        if any(candidate.get(key)!=old.get(key) for key in
                ('polarity','filled','proof_body_sha256','context_identity')):
            return False
        if (candidate.get('pre_proof_sha256')==old.get('pre_proof_sha256')
                and candidate.get('prefix_sha256')==old.get('prefix_sha256')):
            return True
        # The old proof remains certified against its immutable accepted
        # source, even while earlier OPEN proof slots contain failed drafts.
        return (isinstance(old.get('accepted_prefix_sha256'),str)
            and candidate.get('prior_accepted_prefix_sha256')==
                old['accepted_prefix_sha256'])

    @staticmethod
    def _native_receipt(candidate):
        receipt=deepcopy(candidate)
        receipt.pop('prior_accepted_prefix_sha256',None)
        return receipt

    @staticmethod
    def _valid_source_manifest(manifest):
        return (isinstance(manifest,dict) and all(
            isinstance(name,str) and name.endswith('.thy') and
            '/' not in name and '\\' not in name and
            isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value)
            for name,value in manifest.items()))

    @staticmethod
    def _source_manifest(snapshot_dir):
        root=Path(snapshot_dir)
        if not root.is_dir() or root.is_symlink():
            raise ValueError('immutable raw source snapshot is absent')
        result={}
        for path in sorted(root.glob('*.thy')):
            if path.is_symlink() or not path.is_file():
                raise ValueError('raw source snapshot contains a non-regular theory')
            result[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def candidate_theories_for_snapshot(self, snapshot_dir):
        """Identify local speculative edits without trusting the agent's claim."""
        try:current=self._source_manifest(snapshot_dir)
        except (OSError,ValueError):return []
        return self._candidate_theories_for_hashes(current)

    def _candidate_theories_for_hashes(self, current):
        allowed=[]
        for theory in sorted(self.seal_transactions):
            baseline=self.seal_base_manifests.get(theory)
            if not self._valid_source_manifest(baseline):continue
            names=set(baseline)|set(current)
            changed={name for name in names if baseline.get(name)!=current.get(name)}
            if changed<={theory+'.thy'}:allowed.append(theory)
        return allowed

    def guard_green_proofs(self, artifact, *, snapshot_dir=None,
                           enforce_theories=()):
        """Reject a raw-workspace edit of accepted proofs outside trusted seal.

        `artifact` is VERO's extraction of the *original* shared workspace,
        never a local checker rendering that masks unrelated slots with oops.
        """
        slots=artifact.get('slots') if isinstance(artifact,dict) else None
        if not isinstance(slots,dict):raise ValueError('VERO slot extraction is absent')
        raw_root=Path(snapshot_dir or getattr(self,'audit_workspace_snapshot',None)
            or self.workspace)
        enforced=set(enforce_theories)
        violations=[]
        for uid,receipt in sorted(self.accepted_receipts.items()):
            theory=self._owner(uid)
            if theory in self.seal_transactions and theory not in enforced:continue
            slot=slots.get(uid+':prove')
            body=(slot.get('body') if isinstance(slot,dict) and slot.get('found')
                and isinstance(slot.get('body'),str) else None)
            if body is None:
                # Extraction may reject a malformed *import* slot and omit
                # otherwise unchanged proof slots. Read only the immutable
                # raw snapshot's exact delimiters before declaring an edit.
                path=raw_root/(theory+'.thy')
                if path.is_file() and not path.is_symlink():
                    try:raw=path.read_text(encoding='utf-8')
                    except (OSError,UnicodeError):raw=''
                    start=f'(* VERO START {uid} prove *)'
                    end=f'(* VERO END {uid} prove *)'
                    if raw.count(start)==1 and raw.count(end)==1:
                        begin=raw.index(start)+len(start)
                        finish=raw.find(end,begin)
                        if finish>=begin:body=raw[begin:finish]
            if not isinstance(body,str) or self._body_hash(body)!=receipt.get('proof_body_sha256'):
                violations.append(uid)
        if violations:
            self.ledger.append('green_proof_edit_rejected',target_ids=violations,
                reason='accepted proof body changed outside trusted seal transaction')
            raise ValueError('accepted GREEN proof body changed outside seal: '+', '.join(violations))

    def begin_seal_transaction(self, theory, artifact, *, snapshot_dir=None):
        """Open a durable, host-owned seal repair window for a ready theory."""
        ids={c['id'] for c in self.bench.get('contracts',()) if c['module']==theory}
        if not ids or not ids<=self.accepted_receipts.keys():
            raise ValueError('seal edit requires all theory contracts GREEN')
        if theory in self.seals or theory in self.seal_transactions:
            raise ValueError('seal edit already sealed or active')
        self.guard_green_proofs(artifact,snapshot_dir=snapshot_dir)
        source_manifest=self._source_manifest(snapshot_dir or self.workspace)
        planned={c['module']+'.thy' for c in self.bench.get('contracts',())}
        if not planned<=source_manifest.keys():
            raise ValueError('seal edit raw source snapshot lacks a planned theory')
        transaction=uuid4().hex
        self.ledger.append('seal_edit_begin',theory=theory,transaction_id=transaction,
            contract_ids=sorted(ids),base_receipts_sha256=digest({uid:self.accepted_receipts[uid]
                for uid in sorted(ids)}),source_manifest=source_manifest)
        self.seal_transactions.add(theory)
        self._seal_transaction_ids[theory]=transaction
        self.pending_seal_receipts[theory]={}
        self.seal_base_manifests[theory]=source_manifest
        return transaction

    def finish_seal_transaction(self, theory, status):
        transaction=self._seal_transaction_ids.get(theory)
        if transaction is None:raise ValueError('no active seal edit transaction')
        if status not in ('sealed','abandoned'):
            raise ValueError('invalid seal edit conclusion')
        if status=='sealed' and theory not in self.seals:
            raise ValueError('seal edit cannot finish without a native seal')
        self.ledger.append('seal_edit_end',theory=theory,
            transaction_id=transaction,status=status)
        self.seal_transactions.remove(theory)
        del self._seal_transaction_ids[theory]
        self.pending_seal_receipts.pop(theory,None)
        self.seal_base_manifests.pop(theory,None)

    def abort_seal_transaction(self, theory, transaction_id, artifact, *, snapshot_dir=None):
        """Close a failed seal edit only after accepted proof bodies return."""
        if self._seal_transaction_ids.get(theory)!=transaction_id:
            raise ValueError('seal edit transaction ID is not current')
        self.guard_green_proofs(artifact,snapshot_dir=snapshot_dir,
            enforce_theories={theory})
        self.finish_seal_transaction(theory,'abandoned')

    def _promote_seal_candidates(self, theory, source_identity):
        """Atomically replace old contract versions only after native seal."""
        staged=self.pending_seal_receipts.get(theory,{})
        if not staged:return
        ids={c['id'] for c in self.bench.get('contracts',()) if c['module']==theory}
        if not ids<=staged.keys():
            raise ValueError('sealed theory lacks native candidate receipts for all contracts')
        if any(staged[uid]['source_identity']!=source_identity for uid in ids):
            raise ValueError('sealed source differs from native candidate source')
        seal_event=next((row['event_id'] for row in reversed(self.ledger.rows)
            if row['kind']=='seal_result' and row['data'].get('theory')==theory
            and row['data'].get('status')=='sealed'
            and row['data'].get('source_identity')==source_identity),None)
        if seal_event is None:raise ValueError('native seal result is absent')
        rows=deepcopy(self.nodes.get(theory,[]))
        for node in rows:
            uid=node.get('uid')
            if uid not in ids:continue
            candidate=staged[uid]
            node['status']='green'
            node['accepted_receipt']=deepcopy(candidate['receipt'])
            node['accepted_evidence_event_id']=candidate['evidence_event_id']
            node['disposition']='accepted_after_native_seal'
            node.pop('seal_candidate_receipt',None)
        commit=self.ledger.append('proof_commit',theory=theory,nodes=rows,
            commit_id=digest({'theory':theory,'seal_event_id':seal_event,
                'candidate_receipts':staged}),
            authority='native-seal-candidate-promotion',
            source_identity=source_identity,seal_evidence_event_id=seal_event)
        self.nodes[theory]=rows
        for uid in ids:
            self.accepted_receipts[uid]=deepcopy(staged[uid]['receipt'])
            self.accepted_origins[uid]=staged[uid]['evidence_event_id']
        self.pending_seal_receipts[theory]={}
        return commit

    def reconcile_contracts(self, requested_theory, receipts_by_theory):
        """Revoke certificates only when their accepted-source projection changes.

        The adapter supplies candidates from the canonically rendered original
        workspace, not its local check's masked siblings. A seal transaction
        may temporarily change the target's own body while preserving its old
        independently accepted version for a failed candidate. Earlier OPEN
        proof slots are drafts, never facts in the accepted version.
        """
        if not isinstance(receipts_by_theory,dict):
            raise ValueError('canonical contract receipts absent')
        for uid,old in sorted(list(self.accepted_receipts.items())):
            theory=self._owner(uid)
            if theory not in receipts_by_theory:continue
            candidate=receipts_by_theory[theory].get(uid)
            if candidate==old:continue
            if not isinstance(candidate,dict):reason='receipt_missing'
            elif candidate.get('context_identity')!=old.get('context_identity'):
                reason='import_or_support_context_changed'
            elif theory in self.seal_transactions:
                # The entire theory candidate is speculative until the native
                # seal succeeds. Retain the immutable old version through a
                # failed candidate; checked() replaces it only on new native
                # evidence. External imports/support are never speculative.
                continue
            elif self._same_accepted_proof(old,candidate):
                continue
            elif (candidate.get('proof_body_sha256')!=old.get('proof_body_sha256')
                    or candidate.get('polarity')!=old.get('polarity')
                    or candidate.get('filled')!=old.get('filled')):
                self.ledger.append('green_proof_edit_rejected',target_ids=[uid],
                    theory=theory,requested_theory=requested_theory,
                    reason='accepted proof body changed outside trusted seal transaction')
                raise ValueError('accepted GREEN proof body changed outside seal: '+uid)
            elif candidate.get('pre_proof_sha256')!=old.get('pre_proof_sha256'):
                reason='checked_prefix_before_proof_changed'
            elif candidate.get('prefix_sha256')!=old.get('prefix_sha256'):
                self.ledger.append('green_proof_edit_rejected',target_ids=[uid],
                    theory=theory,requested_theory=requested_theory,
                    reason='accepted proof body changed outside trusted seal transaction')
                raise ValueError('accepted GREEN proof body changed outside seal: '+uid)
            else:reason='receipt_changed'
            self.ledger.append('contract_green_invalidated',theory=theory,
                target_id=uid,requested_theory=requested_theory,reason=reason,
                old_receipt_sha256=digest(old),current_receipt_sha256=(
                    digest(candidate) if isinstance(candidate,dict) else None),
                old_receipt=deepcopy(old),current_receipt=deepcopy(candidate))
            del self.accepted_receipts[uid]
            self.accepted_origins.pop(uid,None)
            for node in self.nodes.get(theory,()):
                if node.get('uid')==uid:node['status']='open'

    def invalidate_contract_receipts(self, requested_theory, candidates, *, reason):
        """Reject only the illegally edited accepted proofs, preserving siblings.

        The adapter calls this after comparing proof bodies from a fresh,
        canonically rendered raw snapshot. Later contracts are reconciled by
        their checked-prefix receipts in a separate pass. Every revocation is
        durable and replayed like ordinary context invalidation.
        """
        if (not isinstance(candidates,dict) or
                reason!='accepted_proof_body_changed_outside_seal'):
            raise ValueError('invalid trusted contract revocation request')
        for uid,candidate in sorted(candidates.items()):
            old=self.accepted_receipts.get(uid)
            if old is None:continue
            theory=self._owner(uid)
            if theory is None or theory in self.seal_transactions:
                raise ValueError('cannot revoke an unknown or active seal candidate')
            if (isinstance(candidate,dict) and all(
                    candidate.get(key)==old.get(key) for key in
                    ('polarity','filled','proof_body_sha256'))):
                raise ValueError('candidate does not show a changed accepted proof body')
            self.ledger.append('contract_green_invalidated',theory=theory,
                target_id=uid,requested_theory=requested_theory,reason=reason,
                old_receipt_sha256=digest(old),current_receipt_sha256=(
                    digest(candidate) if isinstance(candidate,dict) else None),
                old_receipt=deepcopy(old),current_receipt=deepcopy(candidate))
            del self.accepted_receipts[uid]
            self.accepted_origins.pop(uid,None)
            for node in self.nodes.get(theory,()):
                if node.get('uid')==uid:node['status']='open'

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
        if theory in self.seal_transactions:
            self._promote_seal_candidates(theory,identity)
        mark=getattr(self.stop,'mark_sealed',None)
        if not callable(mark):
            raise ValueError('seal state owner lacks mark_sealed')
        mark(theory)
        # The durable seal_result is appended before this callback. Cache only
        # the identity here; recovery reconstructs full evidence from the ledger.
        self.seals[theory]={'source_identity':identity,
            'evidence':deepcopy(evidence),'event_id':None,
            'source_manifest':result.get('source_manifest')}
        if theory in self.seal_transactions:
            self.finish_seal_transaction(theory,'sealed')

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
            if not isinstance(manifest,dict) or any(
                    not isinstance(name,str) or not isinstance(value,str)
                    for name,value in manifest.items()):
                raise ValueError('sealed theory source manifest is invalid')
            own=theory+'.thy'
            # Every generated source in the frozen closure must have an old
            # receipt. A missing key is corrupt evidence, not a source edit.
            if any(name not in manifest for name in source_hashes
                    if name!='Vero_Support.thy'):
                raise ValueError('sealed theory source manifest is incomplete')
            changed=sorted(name for name,value in source_hashes.items()
                if manifest.get(name)!=value)
            if not changed:
                raise ValueError('sealed theory identity changed without a source change')
            reason=('own_source_changed' if own in changed else
                'support_source_changed' if 'Vero_Support.thy' in changed else
                'import_source_changed')
            self.invalidate_seal(theory,source_identity,reason=reason,
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
        # Audit every existing receipt, not only the selected theory's
        # predecessors: an edited parent may have sealed consumers elsewhere
        # in the frozen DAG. Parents first, so a stale child cannot authorize
        # an already invalidated parent.
        ordered=sorted(self.seals,key=lambda name:(len(closure(name)),name))
        hashes={name:hashlib.sha256(data).hexdigest() for name,data in snapshot.items()}
        for member in ordered:
            current_identity=identity_for(member,snapshot)
            member_closure=(set(imports)|{'Vero_All'} if member=='Vero_All'
                else closure(member))
            scoped={name+'.thy':hashes[name+'.thy'] for name in member_closure}
            # An optional support source can also disappear between checks.
            scoped['Vero_Support.thy']=hashes.get('Vero_Support.thy')
            self.sealed_result(member,current_identity,source_hashes=scoped)
            if member in self.seals:
                missing=sorted(member_closure-{member}-self.seals.keys())
                if missing:
                    self.invalidate_seal(member,current_identity,
                        reason='unsealed_imports',missing_imports=missing)
        return sorted(members-{theory}-self.seals.keys())

    def source_missing(self, snapshot, imports, requested_theory=None, *,
                       support_required=True):
        """Durably revoke validity when a planned or additive-import source disappears.

        This is an observation of the current rendered workspace, not a proof
        check. Historical native evidence and first-GREEN costs remain intact.
        The adapter calls it before source-identity construction can fail.
        """
        modules=set(imports)|{c['module'] for c in self.bench['contracts']}
        modules.update(self.seals)
        if requested_theory is not None:modules.add(requested_theory)
        support_missing=support_required and 'Vero_Support.thy' not in snapshot
        affected=[];missing=set();preserved=[]
        current_hashes={name:hashlib.sha256(data).hexdigest()
            for name,data in snapshot.items() if name.endswith('.thy')}
        local_candidates=set(self._candidate_theories_for_hashes(current_hashes))
        for theory in sorted(modules):
            closure=set();pending=[theory]
            while pending:
                current=pending.pop()
                if current in closure:continue
                closure.add(current);pending.extend(imports.get(current,()))
            absent={name+'.thy' for name in closure
                if name+'.thy' not in snapshot}
            if support_missing:absent.add('Vero_Support.thy')
            if absent:
                affected.append(theory);missing.update(absent)
                if (theory in local_candidates and absent=={theory+'.thy'}):
                    preserved.append(theory)
                if theory in self.seals:
                    identity=hashlib.sha256(json.dumps({
                        'theory':theory,'missing_sources':sorted(absent),
                        'present_sources':{name:hashlib.sha256(data).hexdigest()
                            for name,data in sorted(snapshot.items())
                            if name.endswith('.thy')}},sort_keys=True,
                        separators=(',',':')).encode()).hexdigest()
                    self.invalidate_seal(theory,identity,reason='source_missing',
                        changed_sources=sorted(absent))
        if affected:
            self.ledger.append('source_missing',requested_theory=requested_theory,
                affected_theories=affected,missing_sources=sorted(missing),
                preserved_contract_theories=sorted(preserved),
                observation='effective generated import snapshot; no native check')
            for uid in list(self.accepted_receipts):
                if self._owner(uid) in affected and self._owner(uid) not in preserved:
                    del self.accepted_receipts[uid]
                    self.accepted_origins.pop(uid,None)
                    for node in self.nodes.get(self._owner(uid),()):
                        if node.get('uid')==uid:node['status']='open'
        return sorted(missing)

    def _affected_by_failed_sources(self, failed, imports, planned):
        """Use a complete trusted graph, or fail closed to repository scope."""
        if (not isinstance(imports,dict) or not planned<=imports.keys() or
                not failed or not failed<=imports.keys() or
                not all(isinstance(name,str) and isinstance(parents,(list,tuple,set))
                    and all(isinstance(parent,str) and parent in imports
                        for parent in parents)
                    for name,parents in imports.items())):
            return set(planned)
        affected=set(failed)
        while True:
            consumers={theory for theory,parents in imports.items()
                if theory not in affected and set(parents)&affected}
            if not consumers:break
            affected.update(consumers)
        return affected&planned

    def source_invalid(self, *, reason, requested_theory=None,
                       candidate_theories=(), invalid_theories=(), imports=None):
        """Revoke current authority when edited imports cannot form a valid DAG.

        A trusted caller may identify a malformed source and supply a complete
        graph for its consumers. Unknown culprit or graph invalidity falls back
        to all planned theories. Historical first-GREEN and usage remain.
        """
        planned={c['module'] for c in self.bench['contracts']} | set(self.seals)
        failed=(set(invalid_theories) if isinstance(invalid_theories,(list,tuple,set))
            and all(isinstance(name,str) for name in invalid_theories) else set())
        affected=sorted(self._affected_by_failed_sources(failed,imports,planned))
        speculative=set(candidate_theories)
        if not speculative<=self.seal_transactions:
            raise ValueError('only active host seal transactions may retain speculative candidates')
        preserved=sorted(speculative & (failed or set(affected)) & set(affected))
        current_identity=hashlib.sha256(json.dumps({
            'invalid_source_reason':reason,'failed_theories':sorted(failed),
            'affected_theories':affected,
        },sort_keys=True,separators=(',',':')).encode()).hexdigest()
        for theory in affected:
            if theory in self.seals:
                self.invalidate_seal(theory,current_identity,
                    reason='source_invalid')
        self.ledger.append('source_invalid',requested_theory=requested_theory,
            affected_theories=affected,preserved_contract_theories=preserved,
            invalid_theories=sorted(failed),reason=reason,
            observation='invalid generated import graph; no native check')
        for uid in list(self.accepted_receipts):
            if self._owner(uid) in affected and self._owner(uid) not in preserved:
                del self.accepted_receipts[uid]
                self.accepted_origins.pop(uid,None)
                for node in self.nodes.get(self._owner(uid),()):
                    if node.get('uid')==uid:node['status']='open'
        return affected

    def source_extraction_invalid(self, file_errors, imports, *, snapshot_dir=None,
                                  reason='raw_extraction_failed'):
        """Revoke certificates when VERO cannot extract a source for checking.

        A failed extraction has no canonical prefix to compare. Every failed
        theory and its generated-import consumers lose current authority,
        except the immutable old version of a host-owned local seal candidate.
        An unknown error owner/import graph is conservatively repository-wide.
        """
        if not isinstance(file_errors,dict):
            raise ValueError('VERO extraction errors must identify theories')
        if not file_errors:return []
        if not isinstance(reason,str) or not reason:
            raise ValueError('source invalidation reason is required')
        planned={c['module'] for c in self.bench.get('contracts',())}
        planned.update(self.seals)
        failed={name[:-4] if name.endswith('.thy') else name
            for name in file_errors if isinstance(name,str)}
        if len(failed)!=len(file_errors):failed=set()
        affected=self._affected_by_failed_sources(failed,imports,planned)
        local=(set(self.candidate_theories_for_snapshot(snapshot_dir))
            if snapshot_dir is not None else set())
        preserved=sorted(failed & affected & local)
        try:
            current_manifest=(self._source_manifest(snapshot_dir)
                if snapshot_dir is not None else None)
        except (OSError,ValueError):
            current_manifest=None
        identity=digest({'failed_theories':sorted(failed),'reason':reason,
            'affected_theories':sorted(affected),'source_manifest':current_manifest})
        for theory in sorted(affected & self.seals.keys()):
            self.invalidate_seal(theory,identity,reason='source_extraction_invalid')
        self.ledger.append('source_extraction_invalid',
            failed_theories=sorted(failed),affected_theories=sorted(affected),
            preserved_contract_theories=preserved,
            file_errors=deepcopy(file_errors),source_manifest=current_manifest,
            reason=reason,observation=('raw workspace changed after native publication'
                if reason=='source_changed_after_native_publication' else
                'raw extraction failed before native checker; no new GREEN'))
        for uid in list(self.accepted_receipts):
            theory=self._owner(uid)
            if theory in affected and theory not in preserved:
                del self.accepted_receipts[uid]
                self.accepted_origins.pop(uid,None)
                for node in self.nodes.get(theory,()):
                    if node.get('uid')==uid:node['status']='open'
        return sorted(affected)

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
        candidates=result.get('contract_receipts',{})
        strict=self.bench.get('schema_version',1)>=2
        native_evidence=result.get('native_evidence')
        if strict:
            if not isinstance(candidates,dict):
                raise ValueError('native check lacks per-contract candidate receipts')
            # The adapter already reconciles the canonical full workspace;
            # repeat the owner's scoped comparison here so a direct checked()
            # invocation cannot bypass context invalidation or the edit guard.
            self.reconcile_contracts(theory,{theory:candidates})
        rows=[]
        for uid in sorted(ids):
            candidate=candidates.get(uid)
            native_green=(uid+':prove' in audited and
                result.get('contract_status',result['status'])=='ok' and
                isinstance(native_evidence,dict) and native_evidence.get('returncode')==0 and
                not result.get('certification_blocked_by'))
            if strict and native_green and not self._valid_receipt(candidate):
                raise ValueError('audited GREEN lacks a filled, scoped proof receipt: '+uid)
            prior=self.accepted_receipts.get(uid)
            same_external=(prior is not None and isinstance(candidate,dict) and
                candidate.get('context_identity')==prior.get('context_identity'))
            exact=same_external and self._same_accepted_proof(prior,candidate)
            seal_draft=same_external and theory in self.seal_transactions
            retained=not native_green and (exact or seal_draft)
            status='green' if native_green or retained else 'open'
            node={'uid':uid,'kind':'contract','theory':theory,'status':status}
            staged=(native_green and prior is not None and
                theory in self.seal_transactions)
            if staged:
                node.update(accepted_receipt=deepcopy(prior),
                    accepted_evidence_event_id=self.accepted_origins[uid],
                    seal_candidate_receipt=self._native_receipt(candidate),
                    disposition='native_green_pending_theory_seal')
            elif native_green and isinstance(candidate,dict):
                node.update(accepted_receipt=self._native_receipt(candidate),
                    disposition='accepted_native_green')
            elif retained:
                node.update(accepted_receipt=deepcopy(prior),
                    accepted_evidence_event_id=self.accepted_origins[uid],
                    disposition=('retained_prior_native_green_during_seal' if seal_draft and not exact
                        else 'retained_prior_native_green'))
            rows.append(node)
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
            commit=self.ledger.append('proof_commit',theory=theory,nodes=rows,commit_id=commit_id,
                source_identity=result.get('source_identity'),
                evidence=result['native_evidence'],seal_evidence=result.get('build_evidence'))
            if theory in self.seal_transactions:
                self.pending_seal_receipts[theory]={n['uid']:{
                    'receipt':deepcopy(n['seal_candidate_receipt']),
                    'evidence_event_id':commit['event_id'],
                    'source_identity':result.get('source_identity')}
                    for n in rows if n.get('kind')=='contract' and
                        isinstance(n.get('seal_candidate_receipt'),dict)}
            for node in rows:
                if node['kind']!='contract':continue
                uid=node['uid']
                if node.get('disposition')=='accepted_native_green':
                    self.accepted_receipts[uid]=deepcopy(node['accepted_receipt'])
                    self.accepted_origins[uid]=commit['event_id']
                elif node['status']=='open':
                    self.accepted_receipts.pop(uid,None)
                    self.accepted_origins.pop(uid,None)
            self._observe_published(theory,rows)
        retained_ids=[n['uid'] for n in rows if n.get('disposition','').startswith(
            'retained_prior_native_green')]
        if retained_ids:
            self.ledger.append('green_retained',theory=theory,target_ids=retained_ids,
                failed_check_commit_id=commit['event_id'],
                accepted_evidence_event_ids={uid:self.accepted_origins[uid]
                    for uid in retained_ids})
        self.ledger.append('check_completed',theory=theory,target_ids=[r['uid'] for r in rows],
            elapsed_seconds=native_elapsed,
            outcome='blocked_dependency' if result.get('certification_blocked_by')
                else result.get('contract_status',result['status']),
            native_elapsed_seconds=native_elapsed,seal_elapsed_seconds=0,
            timing_basis='native cold contract check only; automatic seal is a separate seal_result event')

    def evaluated(self, report, modules=None, *, preflight_source_invalidated=False):
        """Report draft status without overwriting native accepted GREEN proofs."""
        if self.stop.event.is_set():
            self.ledger.append('late_evaluation',report_sha256=digest(report));return
        if report.get('context_error') and not preflight_source_invalidated:
            # The original VERO runner also evaluates at turn end and during
            # recovery, without visiting the Service tool endpoint. A rejected
            # effective import graph invalidates current seals on that path.
            frozen=getattr(self,'audit_workspace_snapshot',None)
            self.source_invalid(reason='context_error',requested_theory=None,
                candidate_theories=(self.candidate_theories_for_snapshot(frozen)
                    if frozen is not None else ()))
        scope=set(modules) if modules is not None else {c['module'] for c in self.bench['contracts']}
        # Hygiene/extraction rejection and blocked imports skip checker.check,
        # including its source-identity reconciliation. Invalidate their old
        # seals and sealed consumers before publishing open proof rows.
        module_results=report.get('modules',{})
        contract_results=report.get('contracts',{})
        rejected=set()
        for theory in scope:
            module=module_results.get(theory)
            if module is not None:
                if module.get('status') in ('rejected','blocked_dependency'):
                    rejected.add(theory)
            elif any(contract_results.get(c['id'],{}).get('status') not in ('proved','disproved')
                     for c in self.bench['contracts'] if c['module']==theory):
                rejected.add(theory)
        if rejected and self.seals and not (report.get('context_error')
                and preflight_source_invalidated):
            imports=report.get('effective_imports')
            if not isinstance(imports,dict) or not scope<=imports.keys():
                affected=set(self.seals)
            else:
                affected=set(rejected)
                while True:
                    consumers={theory for theory,parents in imports.items()
                        if theory not in affected and set(parents)&affected}
                    if not consumers:break
                    affected.update(consumers)
            report_sha256=digest(report)
            for theory in sorted(affected&self.seals.keys()):
                identity=digest({'theory':theory,'evaluator_report_sha256':report_sha256,
                    'rejected_theories':sorted(rejected)})
                self.invalidate_seal(theory,identity,reason='evaluator_rejected_or_blocked')
        for theory in sorted(scope):
            rows=[]
            for contract in self.bench['contracts']:
                if contract['module']!=theory:continue
                uid=contract['id'];receipt=self.accepted_receipts.get(uid)
                if self.bench.get('schema_version',1)>=2:
                    # The evaluator may have checked a RED draft or skipped a
                    # module. It cannot create native GREEN by reporting proved.
                    node={'uid':uid,'kind':'contract','theory':theory,
                        'status':'green' if receipt is not None else 'open'}
                    if receipt is not None:
                        node.update(accepted_receipt=deepcopy(receipt),
                            accepted_evidence_event_id=self.accepted_origins[uid],
                            disposition='retained_prior_native_green')
                else:
                    node={'uid':uid,'kind':'contract','theory':theory,
                        'status':'green' if report['contracts'][uid]['status']=='proved' else 'open'}
                rows.append(node)
            module=report['modules'].get(theory,{})
            ok=module.get('status')=='ok'
            if (self.bench.get('schema_version',1)<2 and
                    module.get('contract_status')=='ok' and
                    module.get('seal_status')=='seal_failed'):
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
