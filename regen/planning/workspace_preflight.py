"""Native declaration/import check of a plan; oops never establishes facts."""
from pathlib import Path
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.preparation.checker_context import load_context


def render(payload, proposal, destination, base_import):
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    contracts={c['contract_id']:c for c in payload['contracts']}
    for theory in proposal['theories']:
        imports=[base_import,*[p['theory'] for p in theory['suggested_predecessors']]]
        blocks=[]; active_context=[]
        for cid in theory['contract_ids']:
            c=contracts[cid]; context=c.get('contract_owned_context',[])
            # All declarations end in oops, so they introduce no new facts.
            # Reuse adjacent identical contexts rather than repeatedly executing
            # the same expensive architecture interpretation for every goal.
            if context != active_context:
                blocks.extend(['end']*len(active_context)); blocks.extend(context)
                active_context=context
            blocks.extend([c['exact_statement'],'oops'])
        blocks.extend(['end']*len(active_context))
        text='theory '+theory['name']+'\nimports '+' '.join('"'+n+'"' for n in imports)+'\nbegin\n\nunbundle l4v_word_context\n\n'+'\n\n'.join(blocks)+'\n\nend\n'
        (destination/(theory['name']+'.thy')).write_text(text)
    return 'theory Planner_Preflight_All\nimports '+' '.join('"'+t['name']+'"' for t in proposal['theories'])+'\nbegin\nend\n'


def native_checker(dataset, timeout=900):
    ctx=load_context(Path(dataset))
    def check(payload, proposal, output):
        output=Path(output); source=output/'source'
        hub=render(payload,proposal,source,ctx['base_import'])
        checker=IsabelleChecker(output,source,image=ctx['image'],architecture=ctx['architecture'],timeout=timeout,
            generated_dir=output/'unused-generated',heaps_dir=ctx['heaps'],l4v_dir=ctx['l4v'],
            semantic_workspace=ctx['semantic_workspace'],base_session=ctx['base_session'],base_import=ctx['base_import'],
            base_catalog=ctx['base_catalog'],isabelle_dir=ctx['isabelle'])
        original=checker._command
        def readonly_command(*args,**kwargs):
            command=original(*args,**kwargs)
            at=command.index("process")+1
            command[at:at]=["-o","threads=4"]
            return [s+',readonly' if s.startswith('type=bind,') and s.endswith('dst=/root/.isabelle/heaps') else s for s in command]
        checker._command=readonly_command
        result=checker._check(hub,'Planner_Preflight_All')
        return {'valid':result['verdict']=='green','returncode':result['returncode'],
                'diagnostic':result.get('diagnostic',''),'statements_only':True,'proved_contracts':0}
    return check
