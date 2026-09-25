"""Native declaration/import check of a plan; oops never establishes facts."""
from pathlib import Path
from regen.planning.import_contract import required_imports
import hashlib
import json
from regen.planning.workspace_validation import PREFLIGHT_THEORY
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.preparation.checker_context import load_context


def render(payload, proposal, destination, base_import):
    if any(t['name']==PREFLIGHT_THEORY for t in proposal['theories']):
        raise ValueError('Theory name is reserved for the native preflight entry point.')
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    contracts={c['contract_id']:c for c in payload['contracts']}
    for theory in proposal['theories']:
        imports=[base_import,*[p['theory'] for p in required_imports({**theory, 'schema_version': proposal['schema_version']})]]
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
    return 'theory '+PREFLIGHT_THEORY+'\nimports '+' '.join('"'+t['name']+'"' for t in proposal['theories'])+'\nbegin\nend\n'


def native_checker(dataset, timeout=900, *, threads=4):
    if type(threads) is not int or threads < 1:
        raise ValueError("preflight threads must be positive")
    ctx=load_context(Path(dataset))
    def execute(payload, proposal, output):
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
            from regen.execution.check_executor import CURRENT_RESOURCES
            if CURRENT_RESOURCES.get() is None:
                command[at:at]=["-o","threads="+str(threads)]
            return [s+',readonly' if s.startswith('type=bind,') and s.endswith('dst=/root/.isabelle/heaps') else s for s in command]
        checker._command=readonly_command
        result=checker._check(hub,PREFLIGHT_THEORY)
        return {'valid':result['verdict']=='green','returncode':result['returncode'],
                'diagnostic':result.get('diagnostic',''),'statements_only':True,'proved_contracts':0,
                'checker_context_id':ctx['checker_context_id'],
                'proposal_sha256':hashlib.sha256(json.dumps(proposal,sort_keys=True).encode()).hexdigest(),
                'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.glob('*.thy'))},
                'log_sha256':hashlib.sha256(Path(result['log']).read_bytes()).hexdigest()}
    def check(payload, proposal, output):
        from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
        from regen.execution.proof_commits import digest
        resources = CURRENT_RESOURCES.get()
        owned = resources is None
        resources = resources or NativeResources(1,threads,1)
        marker = CURRENT_RESOURCES.set(resources)
        executor = CheckExecutor(Path(output)/'native-task',workers=1)
        try:
            task = executor.submit(kind='preflight',theory=PREFLIGHT_THEORY,
                binding={'checker_context_id':ctx['checker_context_id'],'payload_sha256':digest(payload)},
                request={'proposal':proposal},execute=lambda _: execute(payload,proposal,output))
            while task['status'] not in {'completed','cancelled','error','interrupted'}:
                task = executor.wait([task['task_id']],timeout=30)[0]
            if task['status'] != 'completed':
                raise RuntimeError('native preflight '+task['status']+': '+task.get('diagnostic',''))
            return task['result']
        finally:
            executor.close()
            if owned:
                resources.close()
            CURRENT_RESOURCES.reset(marker)
    check.identity={'checker_context_id':ctx['checker_context_id'],'timeout_seconds':timeout,'threads':threads}

    return check
