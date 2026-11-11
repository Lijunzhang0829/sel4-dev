"""Tool-level sketch-priority smoke, with real Isabelle and synthetic statements.

No model, native verdict mocks or proof-validator overrides. Fixture setup and
hash-bound scripted provenance are disclosed substitutions. The coverage text is
an accountable script-authored review, never kernel certification of completeness.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import time
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal
from regen.planning.parallel_file_planner import TheoryController, tools
from regen.planning.theory_document import parse, block, header
from regen.preparation.checker_context import load_context
from regen.tools.document_acceptance_validation import MODEL, readonly_heaps, replay_uncached, require
from regen.tools.workspace_switch_short_smoke import fixture, ScriptedActions

THEORY='Switch_A'
HELPER='helper:classification'
LOCAL='helper:local_eval'


def specifications():
    return {
        THEORY:[('parent','(if b then (1::nat) else 0) < 2'),
                ('independent','(n::nat) + 0 = n')],
        'Switch_B':[('other','(p::bool) ==> p')]}


def helper_entries(corrected=False):
    statement=('lemma classify: "(b::bool) = True | b = False"' if corrected else
               'lemma classify: "(b::bool) = True"')
    return [dict(id=HELPER,role='helper',context=[],statement=statement),
            dict(id=LOCAL,role='helper',context=[],statement='lemma local_eval: "(1::nat) < 2"')]


def consumer(corrected=False):
    claim='b = True | b = False' if corrected else 'b = True'
    return ('proof -\n  have branch: "'+claim+'"\n'
            '    using classify[where b=b] .\n'
            '  have one: "(1::nat) < 2"\n'
            '    using local_eval .\n'
            '  show ?thesis using branch one by auto\nqed')


def group():
    return {'target_entry_id':'parent','helper_ids':[HELPER,LOCAL],
            'use_sites':[{'helper_id':HELPER,'consumer_line':3,'fact':'classify[where b=b]'},
                         {'helper_id':LOCAL,'consumer_line':5,'fact':'local_eval'}]}


def review_rows(corrected=False):
    common={'domain_scope':'local','premises':'local','conclusion':'local',
            'generalization':'none','burden':'isolated','proof_mode':'formal'}
    return [dict(common,helper_id=HELPER,use_indices=[0],helper_role='classification',
                 reduction='Isolate the bool representation split before arithmetic; classification must be established first.',
                 proof_plan='Perform cases b; preserve both True and False branches.',
                 coverage={'definition_refs':['bool.exhaust'],
                     'branches':[{'case':'True','status':'handled','evidence':'True is a native bool constructor.'},
                                 {'case':'False','status':'handled' if corrected else 'unknown',
                                  'evidence':('False satisfies the second disjunct.' if corrected else
                                      'False is a native bool constructor; a True-only conclusion does not cover it.')}],
                     'premise_sources':['The fixed parent ranges over unconstrained b::bool.'],
                     'unknowns':[] if corrected else ['False branch remains unverified; formal proof may reject this classification.']}),
            dict(common,helper_id=LOCAL,use_indices=[1],helper_role='local',
                 reduction='An independent literal arithmetic fact; it does not justify semantic coverage.',
                 proof_plan='Use the ordinary simplifier on the literal natural-number inequality.',
                 coverage={'definition_refs':['lessI'],
                     'branches':[{'case':'literal naturals','status':'handled','evidence':'1 < 2 is a direct successor inequality.'}],
                     'premise_sources':[],'unknowns':[]})]


def committed_proofs(root,config,package,*,allow_uninitialized=False):
    """A fresh journal has no state; after any GREEN its state is mandatory."""
    state=replay_uncached(root,config,package)
    if state is None:
        require(allow_uninitialized,'expected committed journal after native GREEN')
        return {}
    return state['proofs']


def run(dataset,output):
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    (root/'validation-source.py').write_bytes(Path(__file__).read_bytes())
    started=time.monotonic()
    report={'pass':False,'model_calls':0,'benchmark_contracts_proved':0,
            'generation_strategy':'sketch-reviewed-v2','cases':[],
            'scope':'scripted public sketch tools, real native checks/journals; no model or seal',
            'substitutions':['synthetic fixture setup','hash-bound scripted action provenance']}
    controller=executor=resources=None
    def passed(name,**details):
        report['cases'].append({'name':name,'pass':True,**details});durable(root/'report.json',report)
        print(json.dumps({'case':name,'pass':True}),flush=True)
    try:
        context=load_context(Path(dataset));config,packages=fixture(root,context,specifications())
        resources=NativeResources(1,4,1,cpus=4,memory_mb=16384)
        executor=CheckExecutor(root/'check-tasks',workers=1)
        report['resource_policy']=resources.policy;report['checker_context_id']=context['checker_context_id']
        schemas={t['name']:Draft202012Validator(t['inputSchema']) for t in tools(True)}
        scripted=ScriptedActions(root)
        def setup(requested,theory,timeout):
            require(Path(requested).resolve()==root and theory==THEORY,'foreign runtime fixture')
            directory=root/'theories'/theory;directory.mkdir(parents=True,exist_ok=True)
            for name,value in [('imports.json',[]),('sealed-premises.json',{'records':[]})]:
                if not (directory/name).exists():durable(directory/name,value,immutable=True)
            checker=IsabelleChecker(root,directory/'isabelle',image=context['image'],architecture=context['architecture'],
                timeout=timeout,heaps_dir=context['heaps'],l4v_dir=context['l4v'],semantic_workspace=context['semantic_workspace'],
                base_session=context['base_session'],base_import=context['base_import'],base_catalog=context['base_catalog'],
                isabelle_dir=context['isabelle'],generated_dir=directory/'generated')
            original=checker._command
            checker._command=lambda *a,**kw:readonly_heaps(original(*a,**kw))
            checker.declaration_environment=digest(config)
            return config,packages[theory],directory,journal(root,config,packages[theory]),checker,[]
        with ExitStack() as stack:
            stack.enter_context(patch('regen.execution.repository_execution.setup',side_effect=setup))
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',side_effect=scripted.record))
            stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact',side_effect=scripted.validate))
            stack.enter_context(patch('regen.planning.batch_file_planner.call_llm',side_effect=AssertionError('model forbidden')))
            controller=TheoryController(root,THEORY,resources=resources,executor=executor,document_mode=True,
                model=MODEL,check_timeout=120,candidate_timeout=30,generation_strategy_name='sketch-reviewed-v2')
            serial=0
            def call(tool,arguments):
                nonlocal serial
                schemas[tool].validate(arguments);serial+=1
                durable(root/'tool-calls'/f'{serial:03d}-request.json',{'tool':tool,'arguments':arguments},immutable=True)
                try:result=controller.handle(tool,arguments,{'thread_id':'synthetic','turn_id':'synthetic','call_id':str(serial)})
                except BaseException as error:
                    durable(root/'tool-calls'/f'{serial:03d}-error.json',{'error_type':type(error).__name__,'diagnostic':str(error)},immutable=True)
                    raise
                durable(root/'tool-calls'/f'{serial:03d}-result.json',result,immutable=True)
                return result
            def action(q):return call('proof_action',{'theory':THEORY,'proposal':q})
            def read():return call('read_theory',{'theory':THEORY})
            def save(corrected=False):
                draft=read()['document'];entries,_=parse(draft['source'],packages[THEORY],draft['draft_imports'])
                nodes={n['id']:n for n in entries};critical,local=helper_entries(corrected)
                source=header(THEORY,draft['draft_imports'])+block(critical,'by (cases b) auto')+block(local,'by simp')
                source+=block(nodes['parent'],consumer(corrected))+block(nodes['independent'],'by simp')+'end\n'
                return action({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source,'sketches':[group()]})
            saved=save()
            conditional=action({'action':'check_sketch','source_sha256':saved['source_sha256'],'target_entry_ids':['parent']})
            require(conditional['status']=='conditional_closed','synthetic overstrong helper did not conditionally close')
            require(not committed_proofs(root,config,packages[THEORY],allow_uninitialized=True),
                    'conditional sketch published a proof')
            passed('false_classification_only_conditionally_closes')
            mixed=action({'action':'check_theory','source_sha256':saved['source_sha256'],'entry_ids':[HELPER,'independent']})
            entries=mixed['feedback']['entry_results']
            statuses={x['entry_id']:x['status'] for x in entries}
            require(statuses[HELPER]=='blocked_workflow' and 'independent' in mixed['feedback']['new_green'],
                    'unreviewed helper blocked an independent fixed contract')
            require(HELPER not in committed_proofs(root,config,packages[THEORY]),'unreviewed helper accepted')
            passed('missing_review_isolated_per_entry',statuses=statuses)
            sha=read()['document']['source_sha256'];receipt=conditional['groups'][0]['receipt_id']
            incomplete=review_rows();incomplete[0].pop('coverage')
            try:
                action({'action':'review_sketch','source_sha256':sha,'target_entry_id':'parent',
                        'receipt_id':receipt,'reviews':incomplete})
            except (ValueError,ValidationError) as error:
                passed('semantic_review_requires_explicit_coverage',diagnostic=str(error))
            else:
                raise AssertionError('v2 review accepted a classification without coverage evidence')
            reviewed=action({'action':'review_sketch','source_sha256':sha,'target_entry_id':'parent',
                             'receipt_id':receipt,'reviews':review_rows()})
            require(reviewed['status']=='decomposition_reviewed','formal risk review failed')
            before=read()['document']['sketch_groups'][0]
            proof_only=action({'action':'edit_proof','entry_id':HELPER,'expected_hash':sha,
                               'proof_body':'by (cases b; simp)'})
            sha=proof_only['source_sha256']
            after=read()['document']['sketch_groups'][0]
            require(before['sketch_sha256']==after['sketch_sha256'] and after['status']=='conditional_closed',
                    'proof-only repair unnecessarily invalidated the consumer certificate')
            passed('helper_proof_only_edit_reuses_conditional_certificate')
            critical=action({'action':'check_theory','source_sha256':sha,'entry_ids':[HELPER,LOCAL]})
            entries=critical['feedback']['entry_results'];byid={x['entry_id']:x for x in entries}
            require(byid[HELPER]['verdict']=='red','overstrong classification was not rejected natively')
            require(byid[LOCAL]['status']=='blocked_workflow','unproved critical helper did not block sibling route spending')
            require('parent' not in committed_proofs(root,config,packages[THEORY]),'helper failure accepted parent')
            passed('critical_formal_attempt_without_mandatory_probe',helper_result='red',local_result='blocked_workflow')
            workflow=read()['document']['workflow']
            failure=next(x['failure_id'] for x in workflow['pending_failures'] if x['entry_id']==HELPER)
            action({'action':'diagnose_failure','failure_id':failure,'classification':'mathematical',
                    'plan':'The helper excludes the legal False representation; add the exhaustive bool disjunction and update the consumer.'})
            corrected=save(True)
            refreshed=action({'action':'check_sketch','source_sha256':corrected['source_sha256'],'target_entry_ids':['parent']})
            require(refreshed['status']=='conditional_closed','corrected exhaustive route did not close')
            action({'action':'review_sketch','source_sha256':corrected['source_sha256'],'target_entry_id':'parent',
                    'receipt_id':refreshed['groups'][0]['receipt_id'],'reviews':review_rows(True)})
            # The critical helper is checked before its local sibling, and all
            # acceptance remains tied to native evidence under the new prefix.
            proved=action({'action':'check_theory','source_sha256':corrected['source_sha256'],'entry_ids':[HELPER,LOCAL]})
            require(set(proved['feedback']['new_green'])=={HELPER,LOCAL},'corrected group did not publish both helpers')
            parent=action({'action':'check_theory','source_sha256':corrected['source_sha256'],'entry_ids':['parent']})
            require(parent['feedback']['status']=='accepted','fixed parent not independently proved')
            final=replay_uncached(root,config,packages[THEORY])
            require(final is not None,'final native GREEN journal is missing')
            require(set(final['proofs'])=={HELPER,LOCAL,'parent','independent'},'unexpected final acceptance')
            passed('corrected_exhaustive_route_and_fixed_parent_green',accepted_ids=list(final['proofs']))
            report['public_tool_calls']=serial;report['synthetic_contracts_green']=2;report['synthetic_helpers_green']=2
            report['pass']=True
    except BaseException as error:
        report['failure']={'error_type':type(error).__name__,'diagnostic':str(error)}
        raise
    finally:
        errors=[]
        for value in (controller,executor,resources):
            if value is not None:
                try:value.close()
                except BaseException as error:errors.append(str(error))
        if errors:report['pass']=False;report['cleanup_errors']=errors
        report['wall_seconds']=time.monotonic()-started;durable(root/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.dataset,args.output)
    print(json.dumps({'pass':result['pass'],'wall_seconds':result['wall_seconds'],'cases':len(result['cases'])}))
