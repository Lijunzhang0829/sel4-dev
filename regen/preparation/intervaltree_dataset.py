"""Adapt sanitized intervaltree inputs to the existing project proof harness.

Never reads target proofs or earlier experiments. Adds only an empty renderer
compatibility bundle to a separate conservative parent session.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from regen.preparation.refine_campaign import sha256_json
from regen.preparation.premise_database import SCHEMA, digest, verify_database
from regen.preparation.planning_input import contract_card
from regen.infrastructure.path_identity import source_identity
from regen.preparation.semantic_base_builder import SESSION_CATALOGS

BASE = 'Intervaltree_Project_Base'
IMPORT = BASE + '.Project_Input'
EXPORT = r'''let
  val thy = Thy_Info.get_theory "Intervaltree_Project_Base.Project_Input";
  val ctxt = Config.put show_markup false (Proof_Context.init_global thy);
  fun fact (name, thms) = map_index (fn (index, th) =>
    let val prop = Thm.full_prop_of th;
        val owner = Thm.theory_name {long=true} th;
        val printed = Print_Mode.setmp [] (Syntax.string_of_term ctxt) prop;
        val encoded = YXML.string_of_body (Term_XML.Encode.term_raw prop);
    in XML.Elem (("fact", [("name", name), ("index", string_of_int index),
       ("owner", owner), ("session", Resources.theory_qualifier owner),
       ("dynamic", Bool.toString (Facts.is_dynamic (Global_Theory.facts_of thy) name)),
       ("hyps", string_of_int (length (Thm.hyps_of th))),
       ("digest", SHA1.rep (SHA1.digest encoded))]), [XML.Text printed]) end) thms;
  val facts = maps fact (Facts.dest_all (Context.Proof ctxt) false [] (Global_Theory.facts_of thy));
in File.write (Path.explode "/audit-output/premises.xml")
  (XML.string_of (XML.Elem (("premises", []), facts))) end'''


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def premise_audit_context(planning_input_id, export_sha256):
    return {'schema_version':1, 'artifact_type':'intervaltree-parent-premise-audit-context',
            'campaign_id':'independent-intervaltree:' + planning_input_id,
            'semantic_base_seal_id':'native:' + export_sha256, 'records':[]}


def prepare(source, output, isabelle, image):
    source, output, isabelle = (Path(p).resolve() for p in (source, output, isabelle))
    manifest = json.loads((source/'input-manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if digest(source/name) != expected:
            raise ValueError('sanitized input changed: '+name)
    if manifest['target_count'] != 91 or manifest['reference_proofs_included']:
        raise ValueError('unexpected benchmark')
    output.mkdir(parents=True, exist_ok=False)
    semantic = output/'checker-assets/semantic-base'
    shutil.copytree(source, semantic)
    (semantic/'ROOTS').write_text('compat\n')
    compat = semantic/'compat'; compat.mkdir()
    (compat/'ROOT').write_text('session '+BASE+' = Intervaltree_Definitions +\n  theories Project_Input\n')
    (compat/'Project_Input.thy').write_text(
        'theory Project_Input imports "Intervaltree_Definitions.Interval_Input" begin\n'
        'bundle l4v_word_context begin end\nend\n')
    l4v = output/'checker-assets/l4v'; l4v.mkdir()
    for catalog in SESSION_CATALOGS:
        directory = l4v/catalog; directory.mkdir(parents=True, exist_ok=True)
        (directory/'ROOT').write_text('(* Empty compatibility catalog, no seL4 input. *)\n')
    (output/'checker-assets/isabelle').symlink_to(isabelle, target_is_directory=True)
    heaps = output/'checker-assets/heaps'; heaps.mkdir()
    evidence = output/'native-evidence'; evidence.mkdir()
    (evidence/'export.ML').write_text(EXPORT)
    def docker(mounts):
        command = ['docker','run','--rm','--network','none','--cpus','4','--memory','8g']
        for src, dst, ro in mounts:
            command += ['--mount',f'type=bind,src={src},dst={dst}' + (',readonly' if ro else '')]
        return command
    # The image also has seL4 heaps. Copy ONLY Pure and HOL.
    script = '''set -eu
platform=$(basename /root/.isabelle/heaps/polyml-*)
mkdir -p /heap-output/"$platform"/log
for session in Pure HOL; do
  cp /root/.isabelle/heaps/"$platform"/"$session" /heap-output/"$platform"/
  for file in /root/.isabelle/heaps/"$platform"/log/"$session".*; do
    test ! -f "$file" || cp "$file" /heap-output/"$platform"/log/
  done
done
chown -R "$1:$2" /heap-output
'''
    subprocess.run(docker([(heaps,'/heap-output',False)]) +
        ['--entrypoint','bash',image,'-c',script,'prepare',str(os.getuid()),str(os.getgid())], check=True)
    mounts = [(heaps,'/root/.isabelle/heaps',False),(semantic,'/semantic-base',True),
              (isabelle,'/workspace/verification/isabelle',True),(evidence,'/audit-output',False)]
    prefix = docker(mounts) + ['--entrypoint','bash',image,'-c']
    build = ('set -eu\n'
        '/workspace/verification/isabelle/bin/isabelle build -b -j 1 -o threads=2 '
        '-o document=false -d /semantic-base '+BASE+'\n'
        '/workspace/verification/isabelle/bin/isabelle process -l '+BASE+
        ' -d /semantic-base -f /audit-output/export.ML\n')
    with (evidence/'build.log').open('w') as log:
        result = subprocess.run(prefix+[build],stdout=log,stderr=subprocess.STDOUT,timeout=900)
    subprocess.run(prefix+[f'chown -R {os.getuid()}:{os.getgid()} /root/.isabelle/heaps /audit-output'],check=True)
    if result.returncode:
        raise RuntimeError('native preparation failed: '+str(evidence/'build.log'))
    records, quarantine = [], []
    upstream = {'Pure','HOL','Intervaltree_Definitions',BASE}
    for element in ET.parse(evidence/'premises.xml').getroot():
        a = element.attrib; name = a['name']; index = int(a['index'])
        if (a['dynamic'] != 'false' or a['hyps'] != '0' or a['session'] not in upstream
                or '??.' in name or '__internal__' in name or not a['owner']):
            quarantine.append(a); continue
        canonical = name if index == 0 else f'{name}({index+1})'
        records.append({'record_id':'fact:'+sha256_json([name,index])[:24],
            'canonical_name':canonical,'declaration_name':name,'fact_index':index,
            'exact_statement':element.text or '',
            'proposition_digest':'isabelle-term-yxml-sha1:'+a['digest'],
            'owner_theory':a['owner'],'owner_session':a['session'],
            'source_class':'frozen_parent','attributes_json':'[]'})
    database = output/'parent-premises.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)',[tuple(r.values()) for r in records])
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
    save(output/'parent-premises-manifest.json',{'schema_version':2,
        'database_sha256':digest(database),'record_count':len(records),
        'native_export_sha256':digest(evidence/'premises.xml'),
        'exporter_sha256':digest(evidence/'export.ML'),'source_policy':'static-frozen-intervaltree-parent-v1'})
    save(evidence/'quarantine.json',quarantine)
    contracts, mapping = [], {}
    for raw in map(json.loads,(source/'contracts.jsonl').read_text().splitlines()):
        cid = 'goal:'+sha256_json(raw['exact_statement'])[:24]
        mapping[cid] = raw['contract_id']
        contracts.append({**raw,'contract_id':cid,'owner_theory':'Intervaltree.Fixed_Targets',
            'kind':'lemma','contract_owned_attributes':[],'contract_owned_attributes_raw':'',
            'normalized_statement_sha256':sha256_json(raw['exact_statement'])})
    if len(contracts) != 91 or len(mapping) != 91:
        raise ValueError('contract identity mismatch')
    profile = 'intervaltree-fixed-parent-v1'
    payload = {'schema_version':1,'artifact_type':'repository-planning-input',
        'benchmark':{'id':'intervaltree-91','target_session':'Intervaltree','architecture':'ARM',
            'independent_experiment':True,'cross_experiment_inputs':[]},
        'contracts':contracts,'contract_cards':[contract_card(c) for c in contracts],
        'contract_visibility':'owner-blind-v1','owner_groups':{},'session_graph':{},
        'theory_import_graph':{},'upstream_session_closure':sorted(upstream),
        'contract_relation_candidates':[],
        'context_profiles':[{'context_profile_id':profile,'target_session':'Intervaltree',
            'upstream_sessions':sorted(upstream),'generated_import':IMPORT}],
        'parent_premises':{'database':'parent-premises.sqlite','manifest':'parent-premises-manifest.json',
            'record_count':len(records),'database_sha256':digest(database),
            'record_ids_sha256':sha256_json(sorted(r['record_id'] for r in records)),
            'query_interface':'proof-state-recall-v2'},
        'planner_interfaces':{'inspect_contracts':{'max_ids':32},
            'search_parent_premises':{'max_results':50},'inspect_parent_premises':{'max_ids':50},
            'resolve_symbols':{'max_symbols':64},'validate_repository_plan':{}}}
    payload['planning_input_id'] = 'intervaltree-planning-input:'+sha256_json(payload)[:20]
    save(output/'repository-planning-input.json',payload)
    save(output/'contract-map.json',mapping)
    save(output/'premise-audit-context.json', premise_audit_context(
        payload['planning_input_id'], digest(evidence/'premises.xml')))
    context = {'schema_version':1,'artifact_type':'intervaltree-checker-context',
        'target_session':'Intervaltree','architecture':'ARM',
        'paths':{'l4v':'checker-assets/l4v','semantic_workspace':'checker-assets/semantic-base',
                 'heaps':'checker-assets/heaps','isabelle':'checker-assets/isabelle'},
        'premise_database':'parent-premises.sqlite','premise_audit_context':'premise-audit-context.json',
        'premise_audit_context_sha256':digest(output/'premise-audit-context.json'),
        'premise_database_sha256':digest(database),'base_session':BASE,'base_import':IMPORT,
        'base_catalog':'/semantic-base','image':image,'isabelle_sha256':source_identity(isabelle,allow_links=True),
        'checker_policy':'fixed-prefix-cold-green-v1',
        'budgets':{'graph_revisions_per_generation':16,'model_call_timeout_seconds':900,
                   'whole_proof_attempts_per_logical_round':5}}
    context['checker_context_id'] = 'checker-context:'+sha256_json(context)[:20]
    save(output/'checker-context.json',context)
    from regen.preparation.checker_context import load_context
    from regen.planning.owner_blind import OwnerBlindView
    load_context(output); verify_database(database); OwnerBlindView(payload)
    save(output/'preparation.json',{'status':'prepared','target_count':91,
        'source_manifest_sha256':digest(source/'input-manifest.json'),
        'same_fixed_statements':True,'compatibility_additions':['empty bundle l4v_word_context'],
        'target_proofs_added':0,'parent_facts':len(records),'quarantined_facts':len(quarantine),
        'heap_files':{str(p.relative_to(heaps)):digest(p) for p in heaps.rglob('*') if p.is_file()}})
    return {'dataset':str(output),'contracts':len(contracts),'premises':len(records)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','isabelle','image'):
        parser.add_argument('--'+name,required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source,args.output,args.isabelle,args.image),indent=2))
