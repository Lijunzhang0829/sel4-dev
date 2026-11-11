"""Audit an instance of the exact exported C environment, outside model input.

Opaque generated constants are substituted only in this maintenance witness.
The witness does not prove the original constants satisfy the environment.
"""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
import xml.etree.ElementTree as ET

from regen.execution.proof_commits import durable, digest


SUPPORT = r'''
val digits = "0123456789abcdef";
fun digit c = the (List.find (fn i => String.sub (digits, i) = c) (0 upto 15));
fun unhex s = String.implode (map (fn i => Char.chr
  (16 * digit (String.sub(s,2*i)) + digit (String.sub(s,2*i+1))))
  (0 upto (size s div 2 - 1)));
fun hex s = String.translate (fn c => let val n=Char.ord c in
  String.implode [String.sub(digits,n div 16),String.sub(digits,n mod 16)] end) s;
fun term_hex t = hex (YXML.string_of_body (Term_XML.Encode.term_raw t));
val ctxt = @{context};
val axioms = sort_strings (map #1 (Thm.all_axioms_of (Proof_Context.theory_of ctxt)));
val template = Thm.prop_of @{thm binomial_heap_input_global_addresses_def}
  |> Logic.dest_equals |> snd;
val addresses = Term.add_consts template []
  |> filter (fn (n,T) => String.isPrefix "Binomial_Heap_C.binomial_heap_input." n
       andalso T = @{typ "unit ptr"}) |> sort_by fst;
val _ = if length addresses = 19 then () else error "Global address inventory changed";
val replacements = (@{term "Binomial_Heap_C.\<S>"}, @{term audit_stack_addresses}) ::
  (@{term "Binomial_Heap_C.\<G>"}, @{term "{} :: addr set"}) ::
  map_index (fn (i,(n,T)) => (Const(n,T),
    @{term "Ptr :: addr \<Rightarrow> unit ptr"} $ HOLogic.mk_number @{typ addr} (8192+16*i))) addresses;
val defs = maps (fn name => the_default [] (try (Proof_Context.get_thms ctxt) name))
  ["binomial_heap_input_global_addresses_def", "Calg_Binomial_Reviewed_Stack_def",
   "Calg_Binomial_Reviewed_Stack_axioms_def"];
fun field name value = XML.Elem ((name,[]),[XML.Text value]);
fun check_instance (identity, raw) =
  let
    val source = Term_XML.Decode.term_raw (YXML.parse_body (unhex raw));
    val rewrite = Thm.cterm_of ctxt source
      |> Simplifier.rewrite (put_simpset HOL_basic_ss ctxt |> Simplifier.add_simps defs);
    val (original,unfolded) = Thm.prop_of rewrite |> Logic.dest_equals;
    val _ = if original aconv source then () else error "Environment expansion source changed";
    val instance = Term.subst_atomic replacements unfolded;
    val _ = if null (Term.add_frees instance []) andalso null (Term.add_vars instance [])
      andalso not (exists (fn (n,_) => String.isPrefix "Binomial_Heap_C." n)
        (Term.add_consts instance [])) then () else error "Uninstantiated environment parameter";
    val th = Goal.prove ctxt [] [] instance
      (fn {context,...} => simp_tac (context |> Simplifier.add_simps
        @{thms c_fnptr_guard_def audit_stack_addresses_def}) 1);
    val checked = [rewrite,th];
    val _ = Thm.consolidate checked;
    val _ = if Thm.prop_of th aconv instance andalso forall (null o Thm.hyps_of) checked
      andalso not (Thm_Deps.has_skip_proof checked) andalso null (Thm_Deps.all_oracles checked)
      andalso axioms = sort_strings (map #1 (Thm.all_axioms_of (Proof_Context.theory_of ctxt)))
      then () else error "Unsafe environment instance witness";
    val record = XML.Elem (("parameter_instance",[("environment_sha256",identity)]),
      [field "source_term" raw,field "unfolded_term" (term_hex unfolded),
       field "rewrite_proposition" (term_hex (Thm.prop_of rewrite)),
       field "instance_term" (term_hex instance), field "proved_term" (term_hex (Thm.prop_of th)),
       field "skip_proof" "false",field "oracles" "0",field "hypotheses" "0",
       field "changed_axioms" "false",field "original_constants_established" "false"]);
  in writeln ("REGEN_ENVIRONMENT_INSTANCE " ^ hex (XML.string_of record)) end;
'''


def check_parameter_instances(checker, scan, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    if not scan.get('passed'):raise ValueError('requires successful owned-environment export')
    unique={r['environment_term_sha256']:r['environment_term'] for r in scan['records'].values()}
    label='Environment_Instance_'+uuid4().hex
    cases='['+',\n'.join('('+json.dumps(k)+','+json.dumps(v)+')' for k,v in sorted(unique.items()))+']'
    source=('theory '+label+'\nimports '+' '.join(json.dumps(x) for x in checker.imports())+
            '\nbegin\nML \\<open>\n'+SUPPORT+'\nval _ = List.app check_instance '+cases+';\n\\<close>\nend\n')
    native=checker._check(source,label)
    log=Path(native['log']).read_text()
    if Path(native['source']).read_text()!=source:raise ValueError('environment audit source changed')
    records={}
    for raw in re.findall(r'^REGEN_ENVIRONMENT_INSTANCE ([0-9a-f]+)$',log,re.M):
        xml=ET.fromstring(bytes.fromhex(raw).decode())
        key=xml.attrib['environment_sha256'];fields={c.tag:c.text or '' for c in xml}
        names={'source_term','unfolded_term','rewrite_proposition','instance_term','proved_term',
               'skip_proof','oracles','hypotheses','changed_axioms','original_constants_established'}
        if (xml.tag!='parameter_instance' or set(xml.attrib)!={'environment_sha256'}
                or len(xml)!=len(names) or set(fields)!=names or key in records or key not in unique
                or fields.get('source_term')!=unique[key]
                or fields.get('instance_term')!=fields.get('proved_term')
                or any(not re.fullmatch('(?:[0-9a-f]{2})+',fields[k]) for k in
                       ('source_term','unfolded_term','rewrite_proposition','instance_term','proved_term'))
                or any(fields.get(k)!=v for k,v in {'skip_proof':'false','oracles':'0','hypotheses':'0',
                    'changed_axioms':'false','original_constants_established':'false'}.items())):
            raise ValueError('invalid parameter-instance audit')
        records[key]={**fields,'evidence_kind':'native-checked-parameter-instance',
                      'fixed_environment_status':'not_established'}
    passed=native.get('verdict')=='green' and native.get('returncode')==0 and set(records)==set(unique)
    result={'passed':passed,'records':records,'unique_environments':len(unique),
            'verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'log_sha256':hashlib.sha256(log.encode()).hexdigest(),
            'native_check':native,'model_calls':0,'benchmark_proofs_added':0,
            'meaning':'An instance after definition expansion and substitution of 19 function addresses, stack and globals; not a theorem of the original opaque constants.'}
    result['evidence_sha256']=digest(result)
    durable(output/'result.json',result,immutable=True)
    return result
