"""Prepare the approved C heap benchmark for the existing repository harness.

Native translation is rebuilt from the frozen C file. Preparation witnesses are
excluded; no functional target or target-specific proof helper enters the base.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from regen.execution.native_runtime import CALG_BASE as BASE, AFP_CACHE, PROFILE
from regen.preparation.intervaltree_dataset import save
from regen.preparation.premise_database import SCHEMA, digest, verify_database
from regen.preparation.refine_campaign import sha256_json
from regen.preparation.planning_input import contract_card

IMPORT = BASE + '.Project_Input'
UPSTREAM = {'Pure', 'HOL', 'HOL-Library', 'HOL-Eisbach', 'HOL-ex', 'Word_Lib',
            'Simpl', 'AutoCorres2', BASE}
PROFILES = {
    'binary': {
        'targets': ('bh_count_exact', 'bh_insert_correct', 'bh_insert_without_growth',
                    'bh_pop_correct', 'bh_free_owned_storage', 'bh_new_empty_or_failure'),
        'environment': 'Heap_Environment', 'translation': 'Binary_Heap_C',
        'model': 'Binary_Heap_Model', 'target_theory': 'Binary_Heap_Targets',
        'foundation': 'Calg_Binary_Foundation', 'input_c': 'binary_heap_input.c',
        'locale': 'binary_heap_input_global_addresses',
    },
    'binomial': {
        'targets': ('bn_count_exact', 'bn_insert_correct', 'bn_pop_correct',
                    'bn_free_owned_storage', 'bn_new_empty_or_failure', 'bn_ref_null',
                    'bn_ref_increment', 'bn_merge_leaves_nonfailure'),
        'environment': 'Binomial_Environment', 'translation': 'Binomial_Heap_C',
        'model': 'Binomial_Heap_Model', 'target_theory': 'Binomial_Heap_Targets',
        'foundation': 'Calg_Binomial_Foundation', 'input_c': 'binomial_heap_input.c',
        'locale': 'binomial_heap_input_global_addresses',
    },
}


def benchmark_profile(catalog):
    names = tuple(t['id'] for t in catalog['targets'])
    for kind, profile in PROFILES.items():
        if names == profile['targets'] and catalog['target_count'] == len(names):
            if any(t['isabelle_context'] != profile['locale'] for t in catalog['targets']):
                raise ValueError('contract locale differs from benchmark profile')
            return {'kind': kind, **profile}
    raise ValueError('unsupported C heap contract inventory')

EXPORT = r'''let
  val thy = Build.get_theory "Calg_Project_Base.Project_Input";
  val ctxt = Config.put show_markup false (Proof_Context.init_global thy);
  val digits = "0123456789abcdef";
  fun hex s = String.translate (fn c => let val n = Char.ord c in
    String.implode [String.sub (digits, n div 16), String.sub (digits, n mod 16)] end) s;
  val out = TextIO.openOut "/audit-output/premises.xml";
  fun fact (name, thms) = map_index (fn (index, th) =>
    let val prop = Thm.full_prop_of th;
        val owner = Thm.theory_name {long=true} th;
        val printed = Print_Mode.setmp [] (Syntax.string_of_term ctxt) prop;
        val encoded = YXML.string_of_body (Term_XML.Encode.term_raw prop);
        val item = XML.Elem (("fact", [("name", name), ("index", string_of_int index),
          ("owner", owner), ("session", Resources.theory_qualifier owner),
          ("dynamic", "false"), ("hyps", string_of_int (length (Thm.hyps_of th))),
          ("term_yxml_hex", hex encoded)]), [XML.Text printed]);
    in TextIO.output (out, XML.string_of item ^ "\n") end) thms |> K ();
  val _ = TextIO.output (out, "<premises>\n");
  val _ = List.app fact (Facts.dest_static true [] (Global_Theory.facts_of thy));
  val _ = TextIO.output (out, "</premises>\n");
in TextIO.closeOut out end'''


def validate_approval(source, catalog, *, require_generation_flag=True):
    profile = benchmark_profile(catalog)
    approval = json.loads((source / 'review-approval.json').read_text())
    comparison = json.loads((source / 'evidence/readback-comparison.json').read_text())
    review_status = ('consistent_with_approved_scope' if profile['kind'] == 'binary'
                     else 'comparison_complete_with_correction_preparation_incomplete')
    if ((require_generation_flag and not catalog.get('generation_allowed'))
            or approval.get('status') != 'approved_by_user'
            or approval['approved_semantic_binding'] != catalog['semantic_binding']
            or comparison['semantic_binding'] != catalog['semantic_binding']
            or comparison['status'] != review_status
            or digest(source / comparison['readback_path']) != comparison['readback_sha256']):
        raise ValueError('approved semantic binding/read-back required')
    binding = hashlib.sha256(json.dumps(catalog['source_closure'], sort_keys=True).encode()).hexdigest()
    if binding != catalog['semantic_binding']:
        raise ValueError('semantic closure binding mismatch')
    required = {'ROOT', 'source-lock.json', 'environment-lock.json',
                'c/' + profile['input_c'], 'theories/' + profile['environment'] + '.thy',
                'theories/' + profile['translation'] + '.thy',
                'targets-session/' + profile['model'] + '.thy',
                'targets-session/' + profile['target_theory'] + '.thy'}
    if not required <= set(catalog['source_closure']):
        raise ValueError('incomplete semantic closure')
    for name, expected in catalog['source_closure'].items():
        if not (source / name).resolve().is_relative_to(source.resolve()) or digest(source / name) != expected:
            raise ValueError('source drift: ' + name)
    declarations = re.findall(r'(?ms)^lemma (\w+):\s*"(.*?)"\s*oops',
        (source / ('targets-session/' + profile['target_theory'] + '.thy')).read_text())
    if declarations != [(t['id'], t['proposition']) for t in catalog['targets']]:
        raise ValueError('catalog propositions differ from frozen declarations')
    if profile['kind'] == 'binomial':
        evidence = approval.get('approved_evidence', {})
        if not {'evidence/readback-comparison.json', 'evidence/satisfiability.json'} <= set(evidence):
            raise ValueError('binomial approval must bind readback and satisfiability evidence')
        for name, expected in evidence.items():
            if not (source / name).resolve().is_relative_to(source.resolve()) or digest(source / name) != expected:
                raise ValueError('approved evidence changed: ' + name)
        sat = json.loads((source / 'evidence/satisfiability.json').read_text())
        if (sat['semantic_binding'] != binding or sat['status'] != 'concrete_environment_satisfiability_passed'
                or sat['target_count'] != len(profile['targets'])
                or any(sat['audit'][key] != '0' for key in
                       ('oracles', 'skipped_proofs', 'hidden_hypotheses', 'unproved_targets_exported'))):
            raise ValueError('checked binomial premise witnesses required')
        for name, expected in sat['files'].items():
            if not (source / name).resolve().is_relative_to(source.resolve()) or digest(source / name) != expected:
                raise ValueError('satisfiability evidence changed: ' + name)
    return catalog


def approved_catalog(source):
    return validate_approval(source, json.loads((source / 'targets.json').read_text()))


def distribution_identity(root):
    """Bind shipped files and symlinks, including dangling generated-source links.

    The official package contains a link to picosat's ungenerated config.h.
    Resolving every link would reject an otherwise usable distribution. Record
    the link itself, while refusing links that escape this distribution tree.
    """
    rows = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {'heaps', 'browser_info', '.git', '__pycache__'})
        for name in sorted(set(dirs + files)):
            path = Path(base) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                if not path.resolve(strict=False).is_relative_to(root):
                    raise ValueError('distribution link escapes root: ' + relative)
                rows.append([relative, 'link', os.readlink(path)])
            elif path.is_file():
                rows.append([relative, 'file', digest(path), path.stat().st_mode & 0o777])
    return sha256_json(sorted(rows))


def sanitize(source, semantic, profile=None):
    profile = profile or {'kind': 'binary', **PROFILES['binary']}
    if semantic.exists() and any(semantic.iterdir()):
        raise ValueError('sanitized semantic destination must be empty')
    (semantic / 'theories').mkdir(parents=True, exist_ok=True)
    (semantic / 'c').mkdir(exist_ok=True)
    (semantic / 'model').mkdir(exist_ok=True)
    shutil.copyfile(source / 'c' / profile['input_c'], semantic / 'c' / profile['input_c'])
    for name in (profile['environment'], profile['translation']):
        text = (source / f'theories/{name}.thy').read_text()
        if name == 'Heap_Environment':
            text = text[:text.index('lemma malloc_failure_available:')] + '\nend\n'
        text = re.sub(r'text\s+\\<open>.*?\\<close>', '', text, flags=re.S)
        (semantic / f'theories/{name}.thy').write_text(text)
    text = (source / ('targets-session/' + profile['model'] + '.thy')).read_text()
    text = re.sub(r'text\s+\\<open>.*?\\<close>', '', text, flags=re.S)
    (semantic / ('model/' + profile['model'] + '.thy')).write_text(text)
    (semantic / 'ROOT').write_text(f'''session {profile['foundation']} = AutoCorres2 +
  options [document = false, timeout = 600]
  directories "theories"
  theories {profile['environment']} {profile['translation']}
session Calg_Project_Base in "model" = {profile['foundation']} +
  options [document = false, timeout = 300]
  theories {profile['model']} Project_Input
''')
    (semantic / 'model/Project_Input.thy').write_text('theory Project_Input imports ' + profile['model'] + ''' begin
bundle l4v_word_context begin end
ML \\<open>
structure Thy_Info = struct val get_theory = Build.get_theory end;
structure SHA1 = struct open SHA1 val rep = Message_Digest.rep end;
\\<close>
end
''')
    from regen.execution import calg_process
    shutil.copyfile(calg_process.__file__, semantic / 'native_process.py')
    save(semantic / 'native-runtime.json', PROFILE)


def prepare(source, output, environment, resume=False):
    source, output, environment = (Path(p).resolve() for p in (source, output, environment))
    catalog = approved_catalog(source)
    profile = benchmark_profile(catalog)
    upstream = UPSTREAM | {profile['foundation']}
    lock = json.loads((source / 'environment-lock.json').read_text())
    image = lock['container_image_id']
    if resume:
        if not output.is_dir() or (output / 'preparation.json').exists() or (output.parent / 'proof').exists():
            raise ValueError('resume is only for an incomplete native preparation, never a frozen dataset')
        backup = output / 'preparation-attempts' / str(time.time_ns()); backup.mkdir(parents=True)
        for directory in ('native-evidence', 'checker-assets/semantic-base'):
            shutil.copytree(output / directory, backup / Path(directory).name)
        shutil.rmtree(output / 'checker-assets/semantic-base')
        for path in output.glob('*.json'): shutil.copyfile(path, backup / path.name)
        if (output / 'parent-premises.sqlite').exists():
            (output / 'parent-premises.sqlite').rename(backup / 'parent-premises.sqlite')
    else:
        output.mkdir(parents=True, exist_ok=False)
    semantic = output / 'checker-assets/semantic-base'
    sanitize(source, semantic, profile)
    assets = output / 'checker-assets'
    isabelle = environment / 'Isabelle2026'
    if not (assets / 'isabelle').exists(): (assets / 'isabelle').symlink_to(isabelle, target_is_directory=True)
    l4v = assets / 'l4v'; l4v.mkdir(exist_ok=resume)
    afp = environment / AFP_CACHE.removeprefix('/screen/')
    if not (l4v / 'afp').exists(): (l4v / 'afp').symlink_to(afp, target_is_directory=True)
    heaps = assets / 'heaps'; heaps.mkdir(exist_ok=resume)
    cache = environment / 'isolated-isabelle-home/Isabelle2026/heaps'
    # Copy just native dependencies; never reuse Screen_Smoke or target heaps.
    for p in cache.rglob('*'):
        if p.is_file() and (p.name in {'Simpl', 'AutoCorres2'} or p.name.startswith(('Simpl.', 'AutoCorres2.'))):
            dest = heaps / p.relative_to(cache)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists(): shutil.copyfile(p, dest)
    evidence = output / 'native-evidence'; evidence.mkdir(exist_ok=resume)
    exporter = EXPORT
    (evidence / 'export.ML').write_text(exporter)
    from regen.execution.isabelle_node_checker import IsabelleChecker
    checker = IsabelleChecker(output, output / 'native-work', image=image, architecture='X64',
        generated_dir=output / 'unused-generated', heaps_dir=heaps, l4v_dir=l4v,
        semantic_workspace=semantic, isabelle_dir=isabelle, base_session=BASE, base_import=IMPORT)
    # Reuse the actual runtime command, so preparation cannot accept another layout.
    command = checker._command(checker.work_dir / 'Unused.thy', session=BASE,
                               writable_mounts=[(evidence, '/audit-output')])
    # The build needs the real tool, while ML exports use the compatibility
    # process protocol (implemented by process_theories, with Scala services).
    export_prefix = command[:command.index('process')]
    command = command[:command.index('python3')] + ['/workspace/verification/isabelle/bin/isabelle']
    env = ['-e', 'ISABELLE_TOOL_JAVA_OPTIONS=-Djava.awt.headless=true -Xms128m -Xmx2g -Xss8m']
    at = command.index('-e'); command[at:at] = env
    build_command = command + ['build', '-b', '-j', '1', '-o', 'threads=2', '-o', 'document=false',
                               '-d', AFP_CACHE, '-d', '/semantic-base', BASE]
    export_command = export_prefix + ['process', '-l', BASE, '-d', AFP_CACHE, '-d', '/semantic-base',
                                 '-f', '/audit-output/export.ML']
    try:
        with (evidence / 'build.log').open('w') as log:
            for operation, timeout in ((build_command, 900), (export_command, 300)):
                name = 'calg-preparation-' + uuid.uuid4().hex
                operation[2:2] = ['--name', name]
                try:
                    subprocess.run(operation, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
                finally:
                    from regen.execution.native_container import remove_owned
                    remove_owned(name)
    finally:
        subprocess.run(['docker', 'run', '--rm', '--network', 'none',
            '-v', f'{output}:/output', image, 'chown', '-R', f'{os.getuid()}:{os.getgid()}', '/output'], check=True)
    records, quarantine = [], []
    for _, element in ET.iterparse(evidence / 'premises.xml'):
        if element.tag != 'fact': continue
        a = element.attrib; name = a['name']; index = int(a['index'])
        if (name.rsplit('.', 1)[-1] in {t['id'] for t in catalog['targets']}
                or any(marker in a['owner'] for marker in ('Environment_Sanity', 'Preparation_Audit', 'Satisfiability'))):
            raise ValueError('target or preparation fact leaked into native parent: ' + name)
        if (a['dynamic'] != 'false' or a['hyps'] != '0' or a['session'] not in upstream
                or '??.' in name or '__internal__' in name or not a['owner']):
            quarantine.append({k:v for k,v in a.items() if k != 'term_yxml_hex'}); element.clear(); continue
        records.append({'record_id': 'fact:' + sha256_json([name, index])[:24],
            'canonical_name': name if index == 0 else f'{name}({index + 1})',
            'declaration_name': name, 'fact_index': index, 'exact_statement': element.text or '',
            'proposition_digest': 'isabelle-term-yxml-sha1:' + hashlib.sha1(bytes.fromhex(a['term_yxml_hex'])).hexdigest(),
            'owner_theory': a['owner'], 'owner_session': a['session'],
            'source_class': 'frozen_parent', 'attributes_json': '[]'})
        element.clear()
    database = output / 'parent-premises.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO premises VALUES (?,?,?,?,?,?,?,?,?,?)', [tuple(r.values()) for r in records])
        db.execute("INSERT INTO premise_search(premise_search) VALUES ('rebuild')")
    save(output / 'parent-premises-manifest.json', {'schema_version': 2,
        'database_sha256': digest(database), 'record_count': len(records),
        'native_export_sha256': digest(evidence / 'premises.xml'),
        'exporter_sha256': digest(evidence / 'export.ML'), 'source_policy': 'calg-approved-native-parent-v1'})
    save(evidence / 'quarantine.json', quarantine)
    contracts, mapping = [], {}
    for raw in catalog['targets']:
        statement = 'lemma ' + raw['id'] + ':\n  "' + raw['proposition'] + '"'
        context = ['context ' + raw['isabelle_context'] + ' begin']
        cid = 'goal:' + sha256_json([statement, context])[:24]
        mapping[cid] = raw['id']
        contracts.append({'contract_id': cid, 'name': raw['id'], 'kind': 'lemma',
            'owner_theory': 'Calg.Fixed_Targets', 'exact_statement': statement,
            'contract_owned_context': context, 'contract_owned_attributes': [],
            'contract_owned_attributes_raw': '', 'normalized_statement_sha256': sha256_json(statement)})
    payload = {'schema_version': 1, 'artifact_type': 'repository-planning-input',
        'benchmark': {'id': f"calg-{profile['kind']}-{len(contracts)}", 'target_session': 'Calg', 'architecture': 'X64',
            'independent_experiment': True, 'cross_experiment_inputs': []},
        'contracts': contracts, 'contract_cards': [contract_card(c) for c in contracts],
        'contract_visibility': 'owner-blind-v1', 'owner_groups': {}, 'session_graph': {},
        'theory_import_graph': {}, 'upstream_session_closure': sorted(upstream),
        'contract_relation_candidates': [],
        'context_profiles': [{'context_profile_id': f"calg-{profile['kind']}-fixed-parent-v1",
            'target_session': 'Calg', 'upstream_sessions': sorted(upstream), 'generated_import': IMPORT}],
        'parent_premises': {'database': 'parent-premises.sqlite', 'manifest': 'parent-premises-manifest.json',
            'record_count': len(records), 'database_sha256': digest(database),
            'record_ids_sha256': sha256_json(sorted(r['record_id'] for r in records)),
            'query_interface': 'proof-state-recall-v2'},
        'planner_interfaces': {'inspect_contracts': {'max_ids': 32},
            'search_parent_premises': {'max_results': 50}, 'inspect_parent_premises': {'max_ids': 50},
            'resolve_symbols': {'max_symbols': 64}, 'validate_repository_plan': {}}}
    payload['planning_input_id'] = 'calg-planning-input:' + sha256_json(payload)[:20]
    save(output / 'repository-planning-input.json', payload)
    save(output / 'contract-map.json', mapping)
    save(output / 'premise-audit-context.json', {'schema_version': 1,
        'artifact_type': 'calg-parent-premise-audit-context',
        'campaign_id': 'independent-calg:' + payload['planning_input_id'],
        'semantic_base_seal_id': 'native:' + digest(evidence / 'premises.xml'), 'records': []})
    core = {'schema_version': 1, 'artifact_type': 'calg-checker-context',
        'target_session': 'Calg', 'architecture': 'X64',
        'paths': {'l4v': 'checker-assets/l4v', 'semantic_workspace': 'checker-assets/semantic-base',
            'heaps': 'checker-assets/heaps', 'isabelle': 'checker-assets/isabelle'},
        'premise_database': 'parent-premises.sqlite', 'premise_audit_context': 'premise-audit-context.json',
        'premise_audit_context_sha256': digest(output / 'premise-audit-context.json'),
        'premise_database_sha256': digest(database), 'base_session': BASE, 'base_import': IMPORT,
        'base_catalog': '/semantic-base', 'image': image,
        'isabelle_sha256': distribution_identity(isabelle),
        'isabelle_identity_kind': 'installed-distribution-files-and-links-v1',
        'checker_policy': 'fixed-prefix-cold-green-v1',
        'budgets': {'graph_revisions_per_generation': 16, 'model_call_timeout_seconds': 600,
                   'whole_proof_attempts_per_logical_round': 5}}
    core['checker_context_id'] = 'checker-context:' + sha256_json(core)[:20]
    save(output / 'checker-context.json', core)
    from regen.preparation.checker_context import load_context
    from regen.planning.owner_blind import OwnerBlindView
    load_context(output); verify_database(database); OwnerBlindView(payload)
    approved_catalog(source)  # Reject edits during preparation.
    save(output / 'preparation.json', {'status': 'prepared', 'target_count': len(contracts),
        'benchmark_kind': profile['kind'],
        'semantic_binding': catalog['semantic_binding'], 'fixed_statements_and_contexts': True,
        'excluded': ['preparation witnesses and satisfiability sessions', 'review prose', 'old experiments', 'Screen_Smoke heap'],
        'target_proofs_added': 0, 'parent_facts': len(records), 'quarantined_facts': len(quarantine),
        'semantic_files': {str(p.relative_to(semantic)): digest(p) for p in semantic.rglob('*') if p.is_file()},
        'heap_files': {str(p.relative_to(heaps)): digest(p) for p in heaps.rglob('*') if p.is_file()}})
    return {'dataset': str(output), 'contracts': len(contracts), 'premises': len(records)}


def audit_prepared(source, output):
    """Recheck fixed contracts, the source allowlist and parent caches after use."""
    from regen.preparation.checker_context import load_context
    source, output = Path(source).resolve(), Path(output).resolve()
    catalog = approved_catalog(source); profile = benchmark_profile(catalog)
    prepared = json.loads((output / 'preparation.json').read_text())
    if prepared['semantic_binding'] != catalog['semantic_binding']:
        raise ValueError('prepared semantic binding changed')
    context = load_context(output)
    for field, directory in [('semantic_files', context['semantic_workspace']), ('heap_files', context['heaps'])]:
        actual = {str(p.relative_to(directory)): digest(p) for p in directory.rglob('*') if p.is_file()}
        if actual != prepared[field]: raise ValueError('prepared files changed: ' + field)
    with tempfile.TemporaryDirectory(prefix='calg-input-audit-') as tmp:
        expected = Path(tmp) / 'semantic'
        sanitize(source, expected, profile)
        fresh = {str(p.relative_to(expected)): digest(p) for p in expected.rglob('*') if p.is_file()}
        if fresh != prepared['semantic_files']:
            raise ValueError('prepared source differs from approved source allowlist')
    allowed_heaps = {'Simpl', 'AutoCorres2', BASE, profile['foundation']}
    if any(Path(p).name.split('.')[0] not in allowed_heaps for p in prepared['heap_files']):
        raise ValueError('unexpected heap in prepared parent')
    payload = json.loads((output / 'repository-planning-input.json').read_text())
    expected = []
    for t in catalog['targets']:
        statement = 'lemma ' + t['id'] + ':\n  "' + t['proposition'] + '"'
        owned = ['context ' + t['isabelle_context'] + ' begin']
        expected.append(('goal:' + sha256_json([statement, owned])[:24], t['id'], statement, owned))
    actual = [(t['contract_id'], t['name'], t['exact_statement'], t['contract_owned_context'])
              for t in payload['contracts']]
    if actual != expected: raise ValueError('prepared contracts differ from approved source')
    database = output / 'parent-premises.sqlite'
    verify_database(database)
    manifest = json.loads((output / 'parent-premises-manifest.json').read_text())
    if (digest(database) != manifest['database_sha256']
            or digest(output / 'native-evidence/premises.xml') != manifest['native_export_sha256']
            or (output / 'native-evidence/export.ML').read_text() != EXPORT):
        raise ValueError('native parent export changed')
    with sqlite3.connect(database) as db:
        rows = db.execute('SELECT declaration_name, owner_theory, owner_session FROM premises').fetchall()
    for name, owner, session in rows:
        if (name.rsplit('.', 1)[-1] in profile['targets'] or session not in UPSTREAM | {profile['foundation']}
                or any(marker in owner for marker in ('Satisfiability', 'Preparation_Audit', 'Environment_Sanity'))):
            raise ValueError('unexpected parent fact: ' + name)
    if len(rows) != manifest['record_count'] or len(rows) != prepared['parent_facts']:
        raise ValueError('native parent fact count changed')
    return {'status': 'pass', 'semantic_binding': catalog['semantic_binding'], 'target_count': len(expected),
        'parent_facts': len(rows), 'exact_statements_and_contexts': True,
        'source_allowlist_matches': True, 'parent_heaps_unchanged': True,
        'preparation_and_target_facts_absent': True, 'model_calls': 0, 'target_proofs_added': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--environment')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        if args.resume: parser.error('--verify-only cannot resume preparation')
        result = audit_prepared(args.source, args.output)
    else:
        if args.environment is None: parser.error('--environment is required for preparation')
        result = prepare(args.source, args.output, args.environment, args.resume)
    print(json.dumps(result, indent=2))
