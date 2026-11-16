"""Real Isabelle controls for the common protocol; no model or target proofs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.check_protocol import descriptor, enabled
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import durable, freeze_evidence
from regen.execution.repository_execution import validator, context_graph
from regen.planning.independent_document_checks import run_checks
from regen.planning.proof_graph import build_graph
from regen.planning.sketch_check import render_check
from regen.planning.theory_document import render
from regen.preparation.checker_context import load_context
from regen.tools.document_acceptance_validation import readonly_heaps


def run(dataset, output):
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    ctx=load_context(Path(dataset));checker=None
    resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
    token=CURRENT_RESOURCES.set(resources)
    report=dict(passed=False,model_calls=0,benchmark_proofs=0,cases=[],
                checker_context_id=ctx['checker_context_id'],protocol=descriptor(),
                reduced_deadline_controls='1 second for intentional startup timeouts only')
    def passed(name, **fields):
        report['cases'].append(dict(name=name,passed=True,**fields))
        durable(root/'report.json',report)
        print(json.dumps(report['cases'][-1]),flush=True)
    def require(value,message):
        if not value:raise AssertionError(message)
    try:
        checker=IsabelleChecker(root,root/'checks',image=ctx['image'],architecture=ctx['architecture'],
            timeout=300,generated_dir=root/'generated',heaps_dir=ctx['heaps'],l4v_dir=ctx['l4v'],
            semantic_workspace=ctx['semantic_workspace'],base_session=ctx['base_session'],
            base_import=ctx['base_import'],base_catalog=ctx['base_catalog'],isabelle_dir=ctx['isabelle'])
        original=checker._command
        checker._command=lambda *a,**kw:readonly_heaps(original(*a,**kw))
        require(enabled(checker) and checker.batch_incremental is False,'shared cold protocol not applied')
        imports=' '.join(json.dumps(x) for x in checker.imports())
        def ordinary(label,proof='by simp',statement='True',timeout=300):
            source='theory '+label+'\nimports '+imports+'\nbegin\nlemma control: "'+statement+'"\n'+proof+'\nend\n'
            out=freeze_evidence(checker._check(source,label,timeout=timeout))
            durable(root/(label+'.json'),out,immutable=True)
            return out
        timed=ordinary('Protocol_Startup_Timeout',timeout=1)
        require(timed['returncode']==124 and timed['timeout_scope']=='total-check','startup escaped shared deadline')
        passed('cold_startup_is_inside_total_deadline',wall_ms=timed['wall_ms'])
        good=ordinary('Protocol_Ordinary_Green')
        require(good['verdict']=='green' and good['incremental'] is False,'fresh check after timeout failed')
        require(checker._batch_worker is None,'formal proof retained an incremental worker')
        passed('fresh_green_after_timeout',wall_ms=good['wall_ms'])
        package=dict(theory='Protocol_Conditional',file_slot='Protocol_Conditional.thy',contracts=[
            dict(contract_id='parent',name='control_parent',exact_statement='lemma control_parent: "True"',contract_owned_context=[])])
        graph=build_graph(package,dict(generated_nodes=[dict(id='helper',name='control_helper',
            role='helper',statement='lemma control_helper: "True & True"',context=[],dependencies=[],strategy='')],
            contract_dependencies={'parent':['helper']}),run_id='synthetic-protocol',revision=0)
        state=dict(graph=graph,proofs={},accepted_order=[])
        source,prefix,admitted=render_check(state,{n['id']:n for n in graph['nodes']},
            {'parent':'using control_helper by simp'},dict(target_entry_id='parent',helper_ids=['helper']),
            checker.imports(),'a'*64)
        timed=freeze_evidence(checker.check_sketch_source(source,prefix,admitted,timeout=1))
        durable(root/'conditional-timeout.json',timed,immutable=True)
        require(timed['returncode']==124 and not timed['publishable'],'conditional startup timeout accepted')
        passed('conditional_prefix_is_inside_same_total_deadline',wall_ms=timed['wall_ms'])
        conditional=freeze_evidence(checker.check_sketch_source(source,prefix,admitted,timeout=300))
        durable(root/'conditional-green.json',conditional,immutable=True)
        require(conditional['verdict']=='green' and conditional['publishable'] is False,
                'conditional control failed or became publishable')
        require(checker._batch_worker is None,'conditional worker survived')
        passed('fresh_conditional_check_is_discarded',wall_ms=conditional['wall_ms'])
        leaked=ordinary('Protocol_No_Admission_Leak',proof='using control_helper by simp')
        require(leaked['verdict']=='red','conditional helper leaked into a formal process')
        passed('conditional_helper_not_available_to_formal_check')
        # Exercise real independent-entry failure/acceptance, with no model.
        package=dict(theory='Protocol_Independent',file_slot='Protocol_Independent.thy',
            repository_projection={'nodes':[]},contracts=[
            dict(contract_id=k,name=k,exact_statement='lemma '+k+': "'+prop+'"',contract_owned_context=[])
            for k,prop in [('control_false','False'),('control_true','True')]])
        graph=context_graph(build_graph(package,{},run_id='synthetic-protocol',revision=0),[])
        state=dict(graph=graph,proofs={},accepted_order=[],status='generating',rounds={},revisions={},seal_id=None)
        source=render(package,state,checker.imports()).replace('oops','by simp')
        commits=[]
        def commit(new,evidence):
            validator(package)(commits[-1] if commits else state,new,'document',evidence)
            commits.append(deepcopy(new));return new
        result=run_checks(package,state,source,checker,root/'independent',commit=commit,
                          evidence_origin={'sealable_generation':True,'synthetic_author_not_model':True})
        require(result['new_green']==['control_true'] and len(commits)==1,
                'independent RED blocked later TRUE or false goal was accepted')
        require(state['proofs']=={},'original state changed')
        passed('independent_rejection_continues_and_only_green_is_committed')
        report['passed']=True
        return report
    except BaseException as error:
        report['failure']=dict(error_type=type(error).__name__,diagnostic=str(error));raise
    finally:
        try:
            if checker is not None:checker.close_prefix()
            resources.close()
        finally:
            CURRENT_RESOURCES.reset(token);durable(root/'report.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(run(args.dataset,args.output)))
