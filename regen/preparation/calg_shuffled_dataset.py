"""Adapt a frozen numbered C dataset without importing goals or past proofs.

The trusted preparation reads only the input manifest and an audited native
parent. Background helper declarations remain uncertified and are not imported.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import uuid
import xml.etree.ElementTree as ET

from regen.preparation import calg_dataset as calg
from regen.preparation.premise_database import SCHEMA, digest, verify_database
from regen.preparation.refine_campaign import sha256_json
from regen.preparation.intervaltree_dataset import save
from regen.preparation.planning_input import contract_card

IMPORT = 'Calg_Project_Base.Shuffled_Semantics'


def read(path):
    return json.loads(Path(path).read_text())


def contracts_source(manifest):
    name = manifest.get('contract_source_file', 'isabelle/Shuffled_Contracts.thy')
    if not re.fullmatch(r'isabelle/[A-Za-z][A-Za-z0-9_]*\.thy', name):
        raise ValueError('invalid numbered contract source')
    return Path(name)


def without_comments(text):
    """Frozen inputs do not contain comment delimiters inside quoted terms."""
    out = []; pos = depth = 0
    while pos < len(text):
        if text.startswith('(*', pos):
            depth += 1; pos += 2
        elif text.startswith('*)', pos):
            if not depth:
                raise ValueError('unmatched Isabelle comment')
            depth -= 1; pos += 2
        else:
            if not depth or text[pos] == '\n':
                out.append(text[pos])
            pos += 1
    if depth:
        raise ValueError('unclosed Isabelle comment')
    return ''.join(out)


def inventory(source):
    source = Path(source)
    manifest = read(source/'manifest.json')
    files = {str(p.relative_to(source)): digest(p) for p in source.rglob('*')
             if p.is_file() and p.name != 'manifest.json'}
    if files != manifest['input_files_sha256'] or hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest() != manifest['dataset_binding']:
        raise ValueError('numbered dataset input drift')
    entries = read(source/'specs.json')['specs']
    targets = read(source/'targets.json')['targets']
    background = read(source/'background.json')['helpers']
    expected = tuple(manifest.get(k) for k in
                     ('entry_count', 'verification_target_count', 'background_helper_count'))
    disabled = manifest.get('disabled_target_ids', [])
    if (any(type(n) is not int or n < 0 for n in expected)
            or expected != (len(entries), len(targets), len(background))
            or len(entries) != len(targets) + len(background)
            or [x for x in entries if x['generation_required']] != targets
            or [x for x in entries if not x['generation_required']] != background
            or len({x['name'] for x in entries}) != len(entries)
            or len({x['id'] for x in entries}) != len(entries)
            or not isinstance(disabled, list) or len(disabled) != len(set(disabled))
            or set(disabled) & {x['id'] for x in entries}
            or any(x['available_as_premise'] is not False for x in entries)):
        raise ValueError('numbered target/background partition changed')
    selection = source/'contract-selection.json'
    if selection.is_file():
        selected = read(selection)
        if (selected.get('active_target_ids') != [x['id'] for x in targets]
                or selected.get('background_declaration_ids') != [x['id'] for x in background]
                or selected.get('disabled_target_ids') != disabled):
            raise ValueError('numbered active/disabled selection changed')
    contract_file = source/contracts_source(manifest)
    text = without_comments(contract_file.read_text())
    declarations = re.findall(r'(?m)^\s*lemma\s+([A-Za-z_][A-Za-z0-9_\']*)\s*:', text)
    if (declarations != [x['name'] for x in entries]
            or len(re.findall(r'(?m)^\s*oops\s*$', text)) != len(entries)
            or any(text.count(x['statement']) != 1 for x in entries)
            or re.search(r'(?m)^\s*(?:theorem|lemmas|proof|qed|by|axiomatization|axioms|oracle|ML|SML|setup|local_setup)\b|\bsorry\b', text)):
        raise ValueError('numbered declarations differ from active proof-free selection')
    for p in (source/'isabelle').glob('*.thy'):
        if p == contract_file:
            continue
        text = without_comments(p.read_text())
        if re.search(r'(?m)^\s*(lemma|theorem|lemmas|oops|proof|qed|by|axiomatization|axioms|oracle|ML\w*|SML\w*|setup|local_setup|method_setup|attribute_setup|simproc_setup|declaration)\b|\bsorry\b', text):
            raise ValueError('non-definition command in numbered semantic input: '+p.name)
    return manifest, targets, background


def contracts_for(targets):
    return [dict(contract_id='goal:'+sha256_json([t['statement'],t['context']])[:24],
        name=t['name'],kind='lemma',owner_theory='Calg.Fixed_Targets',
        exact_statement=t['statement'],
        contract_owned_context=[] if t['context']=='global' else ['context '+t['context']+' begin'],
        contract_owned_attributes=[],contract_owned_attributes_raw='',
        normalized_statement_sha256=sha256_json(t['statement'])) for t in targets]


def comparison_groups(manifest, targets, descriptor):
    """Evaluation groups contain no outcomes, proofs or proof dependencies."""
    value=read(descriptor)
    ids=[t['id'] for t in targets]
    helpers=value.get('fixed_helper_target_ids', [])
    if (value.get('dataset_binding') != manifest['dataset_binding']
            or not isinstance(helpers,list) or len(set(helpers))!=len(helpers)
            or not set(helpers)<=set(ids)):
        raise ValueError('comparison groups differ from frozen dataset')
    contracts=contracts_for(targets)
    return dict(schema_version=1,dataset_binding=manifest['dataset_binding'],
        targets=[dict(target_id=t['id'],contract_id=c['contract_id'],
            group='fixed_helper' if t['id'] in helpers else 'main')
            for t,c in zip(targets,contracts)],
        counts=dict(main=len(ids)-len(helpers),fixed_helper=len(helpers)),
        dynamic_helpers='report separately; not fixed dataset targets',
        background_declarations=manifest['background_helper_count'],
        expected_outcomes_supplied=False)


def prepare(source, parent, output, groups=None):
    from regen.preparation.checker_context import load_context
    from regen.infrastructure.resource_budget import clone_heaps
    from regen.execution.isabelle_node_checker import IsabelleChecker
    source,parent,output=(Path(p).resolve() for p in (source,parent,output))
    manifest,targets,background=inventory(source)
    classification=comparison_groups(manifest,targets,groups) if groups is not None else None
    contract_file=source/contracts_source(manifest)
    extensions=manifest.get('additional_semantic_imports', [])
    if (not isinstance(extensions,list) or len(extensions)!=len(set(extensions))
            or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', x)
                   or not (source/'isabelle'/(x+'.thy')).is_file() for x in extensions)):
        raise ValueError('invalid definition-only semantic extensions')
    result_scope=manifest.get('result_scope')
    from regen.execution.result_scope import OWNED_CONTEXT_POLICY
    if result_scope not in (None, OWNED_CONTEXT_POLICY):
        raise ValueError('unsupported dataset result scope')
    ctx=load_context(parent)
    if ctx['base_session'] != calg.BASE:raise ValueError('requires the audited C parent')
    for p in (source/'parent').rglob('*'):
        if p.is_file() and digest(p)!=digest(ctx['semantic_workspace']/p.relative_to(source/'parent')):
            raise ValueError('numbered parent differs from the audited native parent')
    output.mkdir(parents=True,exist_ok=False)
    assets=output/'checker-assets';assets.mkdir()
    semantic=assets/'semantic-base'
    shutil.copytree(source/'parent',semantic)
    for name in ('native_process.py','native-runtime.json'):
        shutil.copyfile(ctx['semantic_workspace']/name,semantic/name)
    local=semantic/'local';local.mkdir()
    for p in (source/'isabelle').glob('*.thy'):
        if p!=contract_file:shutil.copyfile(p,local/p.name)
    (local/'Shuffled_Semantics.thy').write_text('theory Shuffled_Semantics\nimports Frozen_Model Environment_Spec_Review Refutation_Helper_Definitions '+ ' '.join(extensions)+'\nbegin\nend\n')
    root=(semantic/'ROOT').read_text()
    root=root.replace('  theories Binomial_Heap_Model Project_Input',
        '  directories "../local"\n  theories Binomial_Heap_Model Project_Input Shuffled_Semantics')
    if 'Shuffled_Semantics' not in root:raise ValueError('unsupported numbered parent ROOT')
    (semantic/'ROOT').write_text(root)
    (assets/'l4v').mkdir();(assets/'l4v/afp').symlink_to(ctx['l4v']/'afp',target_is_directory=True)
    (assets/'isabelle').symlink_to(ctx['isabelle'],target_is_directory=True)
    heaps=assets/'heaps';clone_heaps(ctx['heaps'],heaps)
    # Rebuild our own parent with the fixed extra definitions. Never retain a
    # target/session heap or mutate the source parent's cache.
    for p in list(heaps.rglob('*')):
        if p.is_file() and (p.name==calg.BASE or p.name.startswith(calg.BASE+'.')):p.unlink()
    evidence=output/'native-evidence';evidence.mkdir()
    exporter=calg.EXPORT.replace('Calg_Project_Base.Project_Input',IMPORT)
    (evidence/'export.ML').write_text(exporter)
    checker=IsabelleChecker(output,output/'native-work',image=ctx['image'],architecture='X64',
        generated_dir=output/'unused-generated',heaps_dir=heaps,l4v_dir=assets/'l4v',
        semantic_workspace=semantic,isabelle_dir=ctx['isabelle'],base_session=calg.BASE,base_import=IMPORT)
    cmd=checker._command(checker.work_dir/'Unused.thy',session=calg.BASE,
        writable_mounts=[(evidence,'/audit-output')])
    export=cmd[:cmd.index('process')]+['process','-l',calg.BASE,'-d',calg.AFP_CACHE,
        '-d','/semantic-base','-f','/audit-output/export.ML']
    build=cmd[:cmd.index('python3')]+['/workspace/verification/isabelle/bin/isabelle','build',
        '-b','-j','1','-o','threads=4','-o','document=false','-d',calg.AFP_CACHE,'-d','/semantic-base',calg.BASE]
    try:
        for label,command,timeout in [('build',build,900),('export',export,300)]:
            name='calg-numbered-prepare-'+uuid.uuid4().hex
            command[2:2]=['--name',name]
            try:
                with (evidence/(label+'.log')).open('w') as log:
                    subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
            finally:
                from regen.execution.native_container import remove_owned
                remove_owned(name)
    finally:
        subprocess.run(['docker','run','--rm','--network','none','-v',str(output)+':/output',
            ctx['image'],'chown','-R',str(os.getuid())+':'+str(os.getgid()),'/output'],check=True)
    upstream=set(read(parent/'repository-planning-input.json')['upstream_session_closure'])
    forbidden={t['name'] for t in targets+background}
    rows=[]
    for _,e in ET.iterparse(evidence/'premises.xml'):
        if e.tag!='fact':continue
        a=e.attrib;name=a['name'];index=int(a['index'])
        if (name.rsplit('.',1)[-1] in forbidden or re.search(r'\b(?:bn_(?:count_exact|insert_correct|pop_correct|free_owned_storage|new_empty_or_failure|ref_null|ref_increment|merge_leaves_nonfailure))\b',name)
                or any(s in a['owner'] for s in ('Satisfiability','Preparation_Audit','Environment_Sanity','Shuffled_Contracts'))):
            raise ValueError('goal or preparation fact leaked: '+name)
        if a['dynamic']!='false' or a['hyps']!='0' or a['session'] not in upstream or '??.' in name or '__internal__' in name:
            e.clear();continue
        rows.append(dict(record_id='fact:'+sha256_json([name,index])[:24],
            canonical_name=name if index==0 else f'{name}({index+1})',declaration_name=name,fact_index=index,
            exact_statement=e.text or '',proposition_digest='isabelle-term-yxml-sha1:'+hashlib.sha1(bytes.fromhex(a['term_yxml_hex'])).hexdigest(),
            owner_theory=a['owner'],owner_session=a['session'],source_class='frozen_parent',attributes_json='[]'))
        e.clear()
    database=output/'parent-premises.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)',[tuple(r.values()) for r in rows])
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
    save(output/'parent-premises-manifest.json',dict(schema_version=2,database_sha256=digest(database),
        record_count=len(rows),native_export_sha256=digest(evidence/'premises.xml'),
        exporter_sha256=digest(evidence/'export.ML'),source_policy='calg-numbered-definition-only-parent-v1'))
    payload=deepcopy(read(parent/'repository-planning-input.json'))
    benchmark_id=('calg-'+manifest['dataset']+'-'+str(len(targets)) if result_scope
                  else 'calg-binomial-shuffled-'+str(len(targets)))
    payload['benchmark'].update(id=benchmark_id,independent_experiment=True,cross_experiment_inputs=[])
    contracts=contracts_for(targets)
    payload.update(contracts=contracts,contract_cards=[contract_card(c) for c in contracts])
    payload['context_profiles']=[dict(context_profile_id='calg-numbered-fixed-parent-v1',target_session='Calg',
        upstream_sessions=sorted(upstream),generated_import=IMPORT)]
    payload['parent_premises'].update(record_count=len(rows),database_sha256=digest(database),
        record_ids_sha256=sha256_json(sorted(r['record_id'] for r in rows)))
    payload.pop('planning_input_id',None)
    payload['planning_input_id']='calg-numbered-input:'+sha256_json(payload)[:20]
    save(output/'repository-planning-input.json',payload)
    save(output/'contract-map.json',{c['contract_id']:t['id'] for c,t in zip(contracts,targets)})
    audit=deepcopy(read(parent/'premise-audit-context.json'))
    audit.update(campaign_id='independent-calg:'+payload['planning_input_id'],semantic_base_seal_id='native:'+digest(evidence/'premises.xml'))
    save(output/'premise-audit-context.json',audit)
    core=deepcopy(read(parent/'checker-context.json'));core.pop('checker_context_id')
    # An inherited seal names the old parent's files, not this new parent.
    core.pop('semantic_files_sha256',None)
    core.pop('comparison_groups_sha256',None)
    core.update(base_import=IMPORT,premise_database_sha256=digest(database),premise_audit_context_sha256=digest(output/'premise-audit-context.json'))
    core['semantic_files_sha256']={str(p.relative_to(semantic)):digest(p)
        for p in semantic.rglob('*') if p.is_file()}
    if classification is not None:
        save(output/'comparison-groups.json',classification)
        core['comparison_groups_sha256']=digest(output/'comparison-groups.json')
    if result_scope is not None:
        core['result_scope']=result_scope
    core['checker_context_id']='checker-context:'+sha256_json(core)[:20]
    save(output/'checker-context.json',core)
    if result_scope is not None:
        from regen.execution.contract_refutation import check_export_scan
        from regen.execution.result_scope import environment_bundle
        from regen.preparation.calg_environment_evidence import check_parameter_instances
        scan=check_export_scan(checker,contracts,evidence/'result-scope-export',result_scope=result_scope)
        instances=check_parameter_instances(checker,scan,evidence/'parameter-instances')
        bundle=environment_bundle(contracts,scan,core['checker_context_id'],parameter_instance_evidence=instances)
        save(output/'result-scope.json',bundle)
    inventory(source);load_context(output);verify_database(database)
    from regen.planning.owner_blind import OwnerBlindView
    OwnerBlindView(payload)
    save(output/'preparation.json',dict(status='prepared',dataset_binding=manifest['dataset_binding'],
        targets=len(targets),background_declarations=len(background),background_facts_imported=0,
        parent_facts=len(rows),model_calls=0,target_proofs_added=0,
        semantic_files={str(p.relative_to(semantic)):digest(p) for p in semantic.rglob('*') if p.is_file()},
        heap_files={str(p.relative_to(heaps)):digest(p) for p in heaps.rglob('*') if p.is_file()},
        **({'result_scope':result_scope,'environment_evidence_sha256':bundle['bundle_sha256'],
            'parameter_instance_evidence_sha256':instances['evidence_sha256']} if result_scope else {})))
    return dict(status='prepared',targets=len(targets),premises=len(rows),background_facts_imported=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','parent','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--comparison-groups',type=Path)
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.parent,args.output,args.comparison_groups)))
