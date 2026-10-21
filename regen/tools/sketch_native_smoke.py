"""Real HOL check of groups, isolation and suffix checkpoints; no model calls.

The transport alone is replaced with a local Isabelle process. All rendering,
worker ML, native audits and document evidence validators are production code.
Only generic synthetic statements are used, never removed benchmark proofs.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from regen.execution.batch_checker import BatchWorker
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable
from regen.planning.declaration_index import initial_index
from regen.planning.sketch_check import authorize, check_groups
from regen.planning.theory_document import (DocumentStore, block, candidate,
    check_document, selected_source, validate_document_commit)


class LocalHOLWorker(BatchWorker):
    def _launch(self, command):
        server = self.root / 'server.ML'
        server.write_text(server.read_text().replace('Path.explode "/worker"',
            'Path.explode ' + json.dumps(str(self.root))))
        command = list(command)
        at = command.index('--name'); del command[at:at+2]
        command[-1] = ('use_thy ' + json.dumps(str(self.checker.fixture_base)) + '; '
                       + command[-1].replace('/worker/server.ML', str(server)))
        self._log = (self.root / 'worker.log').open('w')
        self.process = subprocess.Popen(command, stdout=self._log, stderr=subprocess.STDOUT)
        try:
            self.wait(self.root / 'ready', timeout=45)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        (self.root / 'stop').write_text('stop')
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=5)
        finally:
            self._log.close(); self.closed = True


class LocalHOLChecker(IsabelleChecker):
    def __init__(self, root, executable):
        super().__init__(root, root/'isabelle', heaps_dir=root, l4v_dir=root,
            semantic_workspace=root, base_import='Draft.Sketch_Base', base_session='HOL', timeout=30)
        self.executable = str(executable)
        self.fixture_base = root/'Sketch_Base'
        self.fixture_base.with_suffix('.thy').write_text(
            'theory Sketch_Base imports Main begin\nbundle l4v_word_context = [[simp_trace = false]]\nend\n')
        self.batch_candidate_timeout = 30

    def _command(self, source, **kwargs):
        return [self.executable, 'process', '-o', 'threads=2', '-l', 'HOL', '-T', str(source)]

    def _check(self, source_text, label, **kwargs):
        source = self.work_dir/(label+'.thy'); source.write_text(source_text)
        command = [self.executable, 'process', '-o', 'threads=2', '-l', 'HOL', '-e',
            'use_thy ' + json.dumps(str(self.fixture_base)) + '; use_thy ' + json.dumps(str(source.with_suffix('')))]
        result = subprocess.run(command, text=True, capture_output=True, timeout=45)
        log = self.work_dir/(label+'.log'); log.write_text(result.stdout+result.stderr)
        return {'verdict':'green' if result.returncode==0 else 'red',
                'returncode':result.returncode,'source':str(source),'log':str(log),
                'diagnostic':result.stdout+result.stderr}


def check_specialization(checker, output):
    """A legal local premise preserves closure; an unsupported premise does not."""
    package = {'theory':'Specialization', 'file_slot':'Specialization.thy', 'contracts':[
        {'contract_id':'demand','name':'demand','exact_statement':'lemma demand: "P (a::nat) ==> Q a"',
         'contract_owned_context':[]}]}
    state = {'graph':initial_index(package,run_id='synthetic-specialization'),
             'proofs':{},'accepted_order':[],'status':'generating'}
    store = DocumentStore(output/'specialization', package, state, checker.imports(),
                          require_sketch=True, require_use_sites=True)
    target = state['graph']['nodes'][0]

    def save(statement, expression):
        helper = dict(id='general',role='helper',context=[],statement=statement)
        proof = ('proof -\n  assume pa: "P a"\n  show "Q a"\n    using ' + expression + ' pa by blast\nqed')
        from regen.planning.theory_document import render
        source = render(package,state,checker.imports()).replace(block(target),
            block(helper) + block(target,proof))
        return store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
            'source':source,'sketches':[{'target_entry_id':'demand','helper_ids':['general'],
                'use_sites':[{'helper_id':'general','consumer_line':4,'fact':expression}]}]}, {})

    saved = save('lemma general: "(Q::nat => bool) a"', 'general[where Q=Q and a=a]')
    first = check_groups(checker,store,saved['source_sha256'])
    assert first['status'] == 'conditional_closed', first
    saved = save('lemma general: "(P::nat => bool) a ==> (Q::nat => bool) a"',
                 'general[where P=P and Q=Q and a=a]')
    assert store.read()['sketch_groups'][0]['status'] == 'needs_sketch_check'
    second = check_groups(checker,store,saved['source_sha256'])
    assert second['status'] == 'conditional_closed', second
    from regen.planning.sketch_check import read_receipt
    uses = read_receipt(store,second['groups'][0]['receipt_id'],'full')['helper_uses']
    assert uses[0]['local_assumptions']
    saved = save('lemma general: "False ==> (Q::nat => bool) a"', 'general[where Q=Q and a=a]')
    third = check_groups(checker,store,saved['source_sha256'])
    assert third['status'] == 'sketch_rejected', third
    assert not state['proofs']
    return {'legal_local_premise_restored_and_sketch_rechecked':True,
            'unsupported_premise_rejected_by_consumer':True}


def run(output, executable):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    checker = LocalHOLChecker(output, Path(executable).resolve())
    package = {'theory':'Sketch_Demo','file_slot':'Sketch_Demo.thy','contracts':[
        {'contract_id':key,'name':key,'exact_statement':statement,'contract_owned_context':[]}
        for key,statement in [
            ('target1','lemma target1: "(n::nat) + 0 = n & (m::nat) = m"'),
            ('target2','lemma target2: "P --> P"'),
            ('target_false','lemma target_false: "False"')]]}
    state = {'graph':initial_index(package,run_id='synthetic-sketch-native'),
             'proofs':{},'accepted_order':[],'status':'generating'}
    store = DocumentStore(output/'document',package,state,checker.imports(),require_sketch=True)
    nodes = {n['id']:n for n in state['graph']['nodes']}
    helpers = [dict(id='identity',role='helper',context=[],
                    statement='lemma identity: fixes x :: "\'a" shows "x = x"'),
               dict(id='zero',role='helper',context=[],
                    statement='lemma zero: "(n::nat) + 0 = n"')]
    target1 = block(nodes['target1'])
    consumer = '''proof -
  have z: "(n::nat) + 0 = n"
    using zero[where n=n] by assumption
  have i: "(m::nat) = m"
    using identity[where x=m] by assumption
  show ?thesis using z i by blast
qed'''
    source = store.read()['source'].replace(target1,
        ''.join(block(h,'by simp') for h in helpers) +
        block(nodes['target1'],consumer)).replace(
            block(nodes['target2']),block(nodes['target2'],'by fail'))
    broken=block(dict(id='broken',role='helper',context=[],statement='lemma broken: "(0::nat) = True"'))
    source=source.replace(block(nodes['target_false']),broken+
                         block(nodes['target_false'],'using broken by blast'))
    saved = store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
        'source':source,'sketches':[{'target_entry_id':'target1','helper_ids':['identity','zero'],
            'use_sites':[{'helper_id':'zero','consumer_line':3,'fact':'zero[where n=n]'},
                         {'helper_id':'identity','consumer_line':5,'fact':'identity[where x=m]'}]},
            {'target_entry_id':'target_false','helper_ids':['broken']}]}, {})
    report = {'pass':False,'model_calls':0,'benchmark_contracts_proved':0,
              'transport':'local-HOL-only','native_worker':'production batch_checker.ML',
              'scope':'generic synthetic validation, not a repository experiment'}
    try:
        with patch('regen.execution.batch_checker.BatchWorker',LocalHOLWorker):
            report.update(check_specialization(checker, output))
            conditional = check_groups(checker,store,saved['source_sha256'],['target1'])
            if conditional['status']!='conditional_closed':
                raise AssertionError(conditional)
            worker = checker._batch_worker
            assert 'lemma identity' not in worker.base and worker.pending is None
            report['conditional_group_closed']=True
            report['conditional_trial_discarded']=True
            from regen.planning.sketch_check import read_receipt
            receipt = read_receipt(store, conditional['groups'][0]['receipt_id'], 'full')
            uses = receipt['helper_uses']
            assert len(uses) == 2
            assert all(u['facts'] and u['goals'] for u in uses)
            assert all(f['schematic_variable_count'] == 0 for u in uses for f in u['facts'])
            assert 'ML_val' not in store.read()['source']
            report['native_instantiated_facts_and_local_demands_recorded']=True
            from regen.planning.sketch_workflow import probe_helpers, review_sketch
            store.require_review=True
            try:
                authorize(store,source,['identity','zero'])
            except ValueError as error:
                assert 'decomposition review' in str(error)
            else:
                raise AssertionError('unchecked decomposition authorized helper proofs')
            probes=probe_helpers(checker,store,saved['source_sha256'],['identity','zero'])
            assert all(r['status']=='proved_in_probe' for r in probes['results']), probes
            assert not state['proofs'] and checker._batch_worker.pending is None
            probe_ids={r['entry_id']:r['probe_id'] for r in probes['results']}
            rows=[{'helper_id':key,'use_indices':[u['index'] for u in uses if u['helper_id']==key],
                   'domain_scope':'local','premises':'local','conclusion':'local','generalization':'none',
                   'burden':'isolated','reduction':'Isolate the required equality.',
                   'probe_id':probe_ids[key]} for key in ['identity','zero']]
            review_sketch(store,saved['source_sha256'],'target1',receipt['receipt_id'],rows)
            authorize(store,source,['identity','zero'])
            report['native_leaf_probes_discarded_and_review_gate_enforced']=True
            from regen.planning.sketch_workflow import diagnose_failure, workflow_view, record_formal_result
            bad_consumer=source.replace('show ?thesis using z i by blast',
                                         'show ?thesis using z i by assumption')
            bad_edit=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                                 'source':bad_consumer}, {})
            bad_sketch=check_groups(checker,store,bad_edit['source_sha256'],['target1'])
            assert bad_sketch['status']=='sketch_rejected', bad_sketch
            parent_failure=next(r['failure_id'] for r in workflow_view(store)['pending_failures']
                                if r['entry_id']=='target1')
            diagnose_failure(store,parent_failure,'script','Combine the two equalities by conjunction introduction.')
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],'source':source}, {})
            repaired=check_groups(checker,store,saved['source_sha256'],['target1'])
            assert repaired['status']=='conditional_closed' and repaired['groups'][0]['reused_sketch_check']
            assert not workflow_view(store)['pending_failures'] and not state['proofs']
            report['native_consumer_failure_repaired_without_parent_green']=True
            failure_source=source.replace(block(helpers[0],'by simp'),block(helpers[0],'by assumption'))
            failed_edit=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                                   'source':failure_source}, {})
            probe_failure=probe_helpers(checker,store,failed_edit['source_sha256'],['identity'])
            assert probe_failure['results'][0]['status']=='proof_failed', probe_failure
            failure_id=probe_failure['results'][0]['failure_id']
            diagnose_failure(store,failure_id,'known_fact','Use reflexivity for the existing equality goal.')
            try:
                store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                            'source':failure_source.replace('shows "x = x"','shows "True"')}, {})
            except ValueError as error:
                assert 'local proof repair' in str(error)
            else:
                raise AssertionError('decomposition rewrite bypassed the local repair gate')
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                              'source':source}, {})
            store.require_review=False
            name = worker.name
            rejected=check_groups(checker,store,saved['source_sha256'],['target_false'])
            assert rejected['status']=='sketch_rejected'
            authorize(store,source,['identity','zero'])
            projected=selected_source(package,state,source,checker.imports(),['identity','zero'])
            new=candidate(package,state,projected,checker.imports())
            evidence=check_document(checker,state,new,projected)
            validate_document_commit(package,state,new,evidence)
            record_formal_result(store,projected,evidence)
            assert not workflow_view(store)['pending_failures']
            report['native_known_fact_failure_repaired_before_decomposition']=True
            durable(output/'helpers-check.json',evidence,immutable=True)
            state=new;store.state=state
            assert state['accepted_order']==['identity','zero']
            assert store.read()['source']==source
            report['two_helpers_proved_in_one_incremental_batch']=True
            report['unselected_ill_typed_group_does_not_block_proofs']=True
            assert checker._batch_worker.name==name
            corrected=source.replace('by fail','by blast')
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                              'source':corrected}, {})
            projected=selected_source(package,state,corrected,checker.imports(),['target1','target2'])
            new=candidate(package,state,projected,checker.imports())
            evidence=check_document(checker,state,new,projected)
            validate_document_commit(package,state,new,evidence)
            durable(output/'parents-check.json',evidence,immutable=True)
            state=new;store.state=state
            assert state['accepted_order']==['identity','zero','target1','target2']
            assert checker._batch_worker.name==name
            report['two_parents_checked_without_replaying_helpers']=True
            rewritten=corrected.replace(block(nodes['target2'],'by blast'),block(nodes['target2'],'by simp'))
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                              'source':rewritten}, {})
            projected=selected_source(package,state,rewritten,checker.imports(),['target2'])
            new=candidate(package,state,projected,checker.imports())
            evidence=check_document(checker,state,new,projected)
            validate_document_commit(package,state,new,evidence)
            durable(output/'suffix-check.json',evidence,immutable=True)
            assert evidence['document_check_mode']=='suffix'
            assert checker._batch_worker.name==name
            assert evidence['check']['audited_facts']==['target2']
            state=new;store.state=state
            report['suffix_rewrite_restores_same_worker_checkpoint']=True
            first_rewrite=rewritten.replace(block(helpers[0],'by simp'),block(helpers[0],'by blast'))
            store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                        'source':first_rewrite}, {})
            projected=selected_source(package,state,first_rewrite,checker.imports(),['identity'])
            new=candidate(package,state,projected,checker.imports())
            evidence=check_document(checker,state,new,projected)
            validate_document_commit(package,state,new,evidence)
            durable(output/'first-entry-check.json',evidence,immutable=True)
            assert evidence['document_check_mode']=='full'
            assert checker._batch_worker.name==name
            state=new;store.state=state;rewritten=first_rewrite
            report['first_entry_rewrite_reuses_imports_checkpoint']=True
            impossible=block(dict(id='impossible',role='helper',context=[],
                                  statement='lemma impossible: "False"'),'by fail')
            bad=rewritten.replace(broken,'').replace(block(nodes['target_false'],'using broken by blast'),impossible+
                block(nodes['target_false'],'using impossible by blast'))
            saved=store.edit({'action':'edit_theory','base_sha256':store.read()['source_sha256'],
                'source':bad,'sketches':[{'target_entry_id':'target_false','helper_ids':['impossible']}]}, {})
            conditional=check_groups(checker,store,saved['source_sha256'],['target_false'])
            assert conditional['status']=='conditional_closed'
            projected=selected_source(package,state,bad,checker.imports(),['impossible'])
            new=candidate(package,state,projected,checker.imports())
            failed=check_document(checker,state,new,projected)
            durable(output/'false-helper-check.json',failed,immutable=True)
            assert failed['check']['verdict']=='red'
            try:
                validate_document_commit(package,state,new,failed)
            except ValueError:
                pass
            else:
                raise AssertionError('conditional admission became a formal proof')
            assert 'impossible' not in state['proofs']
            assert 'lemma impossible' not in checker._batch_worker.base
            report['false_helper_conditionally_useful_but_not_accepted']=True
            report['pass']=True
    finally:
        checker.close_prefix()
        durable(output/'result.json',report,immutable=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--isabelle',type=Path,default=Path(__file__).resolve().parents[2]/'verification/isabelle/bin/isabelle')
    args=parser.parse_args()
    print(json.dumps(run(args.output,args.isabelle),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
