"""Export authoritative fact certificates from an already sealed Isabelle context."""
import argparse
import json
import subprocess
import uuid
from pathlib import Path

from regen.preparation.checker_context import load_context
from regen.preparation.semantic_base_builder import SESSION_CATALOGS

SOURCE = r'''theory Premise_Source_Evidence imports "Semantic_Base.Semantic_Root" begin
ML \<open>
local
 val thy = Thy_Info.get_theory "Semantic_Base.Semantic_Root";
 val ctxt = Proof_Context.init_global thy;
 val plain = Config.put show_markup false ctxt;
 val stream = TextIO.openOut "/audit-output/facts.tsv";
 fun emit fields = TextIO.output (stream, space_implode "\t" fields ^ "\n");
 fun inspect (name, thms) = List.app (fn (index, th) =>
   let val prop = Thm.full_prop_of th;
       val printed = Print_Mode.setmp [] (Syntax.string_of_term plain) prop;
       val encoded = YXML.string_of_body (Term_XML.Encode.term_raw prop)
   in emit ["FACT", name, string_of_int index, SHA1.rep (SHA1.digest printed),
            Thm.theory_name {long=true} th,
            Bool.toString (Facts.is_dynamic (Global_Theory.facts_of thy) name),
            string_of_int (length (Thm.hyps_of th)), SHA1.rep (SHA1.digest encoded)]
   end) (map_index I thms);
in
 val _ = List.app (fn name => emit ["THEORY", name, Resources.theory_qualifier name]) (Thy_Info.get_names ());
 val _ = List.app inspect (Facts.dest_all (Context.Proof ctxt) false [] (Global_Theory.facts_of thy));
 val _ = TextIO.closeOut stream;
end
\<close>
end
'''


def export_evidence(dataset, output):
    from regen.preparation.premise_database import digest
    c = load_context(Path(dataset))
    if c['base_session'] != 'Semantic_Base' or c['base_import'] != 'Semantic_Base.Semantic_Root':
        raise ValueError('unsupported evidence context')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'Premise_Source_Evidence.thy'
    source.write_text(SOURCE)
    cmd = ['docker', 'run', '--rm', '--network', 'none', '--name', 'premise-evidence-' + uuid.uuid4().hex]
    for src, dst, ro in [(c['l4v'], '/sel4-project/verification/l4v', True),
                         (c['isabelle'], '/workspace/verification/isabelle', True),
                         (c['semantic_workspace'], '/semantic-base', True),
                         (c['heaps'], '/root/.isabelle/heaps', True), (output, '/audit-output', False)]:
        cmd += ['--mount', f'type=bind,src={src},dst={dst}' + (',readonly' if ro else '')]
    cmd += ['-e', 'L4V_ARCH=' + c['architecture'], c['image'],
            '/workspace/verification/isabelle/bin/isabelle', 'process', '-l', c['base_session']]
    for catalog in SESSION_CATALOGS:
        cmd += ['-d', '/sel4-project/verification/l4v/' + catalog]
    cmd += ['-d', '/semantic-base', '-T', '/audit-output/Premise_Source_Evidence']
    with (output / 'check.log').open('w') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError(f'Isabelle evidence export failed; see {output}/check.log')
    manifest = {'schema_version': 1, 'checker_context_id': c['checker_context_id'],
                'image': c['image'], 'isabelle_sha256': c['isabelle_sha256'],
                'facts_sha256': digest(output / 'facts.tsv'), 'source_sha256': digest(source)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_evidence(args.dataset, args.output), indent=2))
