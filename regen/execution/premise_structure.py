"""Bounded, native typed premise matching; observations never establish proofs.

Only a harness-owned complete declaration is elaborated. Printed historical
residuals cannot reconstruct a native local context and are not reparsed here.
Candidates come from retrieval; matching cannot introduce additional facts.
"""
import json
import hashlib
import re
from pathlib import Path
from uuid import uuid4

from regen.execution.proof_commits import digest, durable, freeze_evidence


POLICY = 'native-typed-premise-match-v1'
MAX_CANDIDATES = 500

MATCH_ML = r'''ML_val \<open>
local
  val {context = original_ctxt, goal, ...} = @{Isar.goal};
  val thy = Proof_Context.theory_of original_ctxt;
  val ctxt = original_ctxt |> Config.put show_markup false
    |> Config.put show_types true |> Config.put show_sorts true;
  val digits = "0123456789abcdef";
  fun hex c = let val b = Char.ord c in String.implode
    [String.sub (digits, b div 16), String.sub (digits, b mod 16)] end;
  val stream = TextIO.openOut "/symbol-output/structure.tsv";
  fun emit fields = TextIO.output (stream,
    space_implode "\t" ("MATCH" :: map (String.translate hex) fields) ^ "\n");
  fun printed term = Print_Mode.setmp [] (Syntax.string_of_term ctxt) term;
  fun encode term = SHA1.rep (SHA1.digest
    (YXML.string_of_body (Term_XML.Encode.term_raw term)));
  val goals = map (fn term => let val ((_, body), _) = Variable.focus NONE term ctxt
    in Logic.strip_imp_concl body end) (Thm.prems_of goal);
  fun matches pattern target = SOME (Pattern.match thy (pattern, target)
    (Sign.typ_match thy (fastype_of pattern, fastype_of target) Vartab.empty, Vartab.empty))
    handle Pattern.MATCH => NONE | Pattern.Pattern => NONE | Type.TYPE_MATCH => NONE | TYPE _ => NONE;
  fun subterms term = term :: (case term of a $ b => subterms a @ subterms b
    | Abs _ => [] | _ => []);
  fun first_match pattern terms = get_first (fn term => matches pattern term) terms;
  fun equation term = SOME (Logic.dest_equals term)
    handle TERM _ => (SOME (HOLogic.dest_eq (HOLogic.dest_Trueprop term)) handle TERM _ => NONE);
  fun rewrite_match conclusion =
    (case equation conclusion of NONE => NONE | SOME (left, _) =>
      (case Term.head_of left of Var _ => NONE | _ => first_match left (maps subterms goals)));
  fun inspect (identity, name, index, expected_digest) =
    let val result = Exn.capture (fn () =>
      let
        val thm = nth (Proof_Context.get_thms ctxt name) index;
        val prop = Thm.full_prop_of thm;
        val prop_digest = encode prop;
        val conclusion = Logic.strip_imp_concl prop;
        val premises = Logic.strip_imp_prems prop;
        val checked = expected_digest = "" orelse expected_digest = prop_digest;
        val direct = if checked then first_match conclusion goals else NONE;
        val rewrite = if checked andalso is_none direct then rewrite_match conclusion else NONE;
        val matched = (case direct of SOME env => SOME env | NONE => rewrite);
        val status = if not checked then "catalog_mismatch"
          else if is_some direct then "conclusion_match"
          else if is_some rewrite then "rewrite_match" else "no_direct_match";
        val required = case matched of SOME env => map (Envir.subst_term env) premises | NONE => premises;
      in [identity, status, prop_digest, printed conclusion,
          space_implode "," (map (String.translate hex o printed) required), ""] end) ();
    in case result of Exn.Res fields => emit fields
      | Exn.Exn exn => if Exn.is_interrupt exn then Exn.reraise exn
        else emit [identity, "unresolved", "", "", "", Runtime.exn_message exn]
    end;
in
  val _ = List.app inspect CANDIDATES;
  val _ = TextIO.closeOut stream;
end
\<close>'''


def _candidate(item):
    name = item.get('declaration_name') or item['canonical_name']
    index = item.get('fact_index', 0)
    if 'declaration_name' not in item:
        match = re.fullmatch(r'(.+)\(([1-9][0-9]*)\)', name)
        if match:
            name, index = match[1], int(match[2]) - 1
    if type(index) is not int or index < 0:
        raise ValueError('invalid native fact index')
    value = item.get('proposition_digest', '')
    expected = value.removeprefix('isabelle-term-yxml-sha1:') if value.startswith(
        'isabelle-term-yxml-sha1:') else ''
    if expected and not re.fullmatch('[0-9a-f]{40}', expected):
        raise ValueError('invalid native proposition digest')
    return [item['record_id'], name, index, expected]


def source(node, imports, candidates, label):
    from regen.execution.isabelle_node_checker import wrap_context
    tuples = ['(' + ','.join(str(v) if type(v) is int else json.dumps(v, ensure_ascii=False)
                             for v in _candidate(item)) + ')' for item in candidates]
    block = MATCH_ML.replace('CANDIDATES', '[' + ','.join(tuples) + ']')
    return '\n'.join(['theory ' + label, 'imports', *[json.dumps(x) for x in imports],
                      'begin', 'unbundle l4v_word_context',
                      wrap_context(node.get('context', []), node['statement'] + '\n' + block + '\noops'),
                      'end', ''])


def rerank(checker, context, candidates):
    """Return only a permutation plus native matching evidence for these IDs."""
    ids = [r['record_id'] for r in candidates]
    if len(ids) != len(set(ids)) or len(ids) > MAX_CANDIDATES:
        raise ValueError('native reranking requires unique bounded candidate IDs')
    if context.get('observation') is not None:
        return {'ordered_record_ids': ids, 'evidence': {rid: {
            'status': 'unsupported_historical_residual', 'policy': POLICY,
            'notice': 'A printed residual does not reconstruct its native local context.',
            'is_proof_authority': False} for rid in ids}}
    if not ids:
        return {'ordered_record_ids': [], 'evidence': {}}
    node = context['node']
    if not re.match(r'^\s*(lemma|theorem|corollary|proposition)\b', node['statement']):
        raise ValueError('native reranking requires the complete harness-owned declaration')
    binding = {'policy': POLICY, 'node': {k: node.get(k) for k in ('id', 'statement', 'context')},
               'imports': list(checker.imports()), 'candidates': [_candidate(r) for r in candidates]}
    key = digest(binding)
    directory = checker.work_dir / 'premise-structure' / key / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    label = 'Premise_Structure_' + key[:16]
    rendered = source(node, binding['imports'], candidates, label)
    observe = checker._check_observation if getattr(type(checker), '_check_observation', None) else checker._check
    options = {'output_files': ('structure.tsv',)} if getattr(type(checker), '_check_observation', None) else {}
    check = freeze_evidence(observe(rendered, label, writable_mounts=[(directory, '/symbol-output')],
                                    timeout=min(checker.timeout, 120), **options))
    if check.get('verdict') == 'green':
        from regen.execution.observation_outputs import validate_source
        validate_source(check, rendered, ('structure.tsv',))
    durable(directory / 'check.json', check)
    output = directory / 'structure.tsv'
    output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest() if check.get('verdict') == 'green' and output.is_file() else None
    receipt = {'binding': binding, 'source_sha256': check.get('source_sha256'),
               'log_sha256': check.get('log_sha256'), 'output_sha256': output_sha256}
    if check.get('observation_output_binding') is not None:
        receipt['observation_output_binding'] = check['observation_output_binding']
    evidence_id = digest(receipt)
    durable(directory / 'observation.json', receipt)
    if check.get('verdict') != 'green':
        # A diagnostic failure must not erase otherwise useful lexical results.
        return {'ordered_record_ids': ids, 'evidence': {rid: {
            'policy': POLICY, 'status': 'native_observation_failed', 'evidence_id': evidence_id,
            'diagnostic': check.get('diagnostic', ''), 'is_proof_authority': False} for rid in ids}}
    from regen.execution.isabelle_node_checker import parse_hex_tsv
    observed = {}
    expected = {r['record_id']: _candidate(r)[3] for r in candidates}
    for tag, fields in parse_hex_tsv(output):
        if tag != 'MATCH' or len(fields) != 6:
            raise ValueError('invalid native structure observation')
        rid, status, prop_digest, conclusion, premises, diagnostic = fields
        if rid not in ids or rid in observed or status not in {
                'catalog_mismatch', 'conclusion_match', 'rewrite_match', 'no_direct_match', 'unresolved'}:
            raise ValueError('unexpected native candidate match record')
        if status != 'unresolved':
            if not re.fullmatch('[0-9a-f]{40}', prop_digest):
                raise ValueError('invalid native proposition digest in observation')
            if expected[rid] and (status == 'catalog_mismatch') != (prop_digest != expected[rid]):
                raise ValueError('native candidate status contradicts catalog identity')
        observed[rid] = {'policy': POLICY, 'status': status, 'evidence_id': evidence_id,
            'binding_sha256': key, 'native_proposition_digest': prop_digest or None,
            'catalog_digest_verified': bool(expected[rid] and expected[rid] == prop_digest),
            'native_name_resolved': status != 'unresolved',
            'conclusion': conclusion,
            'required_premises': [bytes.fromhex(value).decode() for value in premises.split(',')] if premises else [],
            'diagnostic': diagnostic, 'applicability_proved': False, 'is_proof_authority': False,
            'notice': 'Typed matching is advisory. Required premises are not discharged; '
                      'no direct match does not exclude indirect use or reverse rewriting.'}
    if set(observed) != set(ids):
        raise ValueError('incomplete native structure observation')
    priority = {'conclusion_match': 0, 'rewrite_match': 1}
    ordered = sorted(ids, key=lambda rid: priority.get(observed[rid]['status'], 2))
    return {'ordered_record_ids': ordered, 'evidence': observed}
