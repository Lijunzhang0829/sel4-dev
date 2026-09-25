"""Read names/types/abbreviation expansions from the legal Isabelle context, never proofs."""
import json
import re
from regen.execution.proof_commits import digest, durable, freeze_evidence

IDENTIFIER=re.compile(r"[A-Za-z][A-Za-z0-9_'.]*")
MAX_SYMBOLS = 16
TERM_LOOKUP_NOTICE = ('Term namespace only; this does not query facts or establish that a fact is absent. '
                      'For a lemma use query with mode=exact, then read_facts for recalled records.')


def resolve_symbols(checker, names, context):
    from regen.execution.isabelle_node_checker import wrap_context, parse_hex_tsv
    if (not isinstance(names,(list,tuple)) or not 1 <= len(names) <= MAX_SYMBOLS
            or any(not isinstance(n,str) or len(n)>256 or not IDENTIFIER.fullmatch(n) for n in names)):
        raise ValueError('symbol lookup requires 1-16 plain Isabelle term identifiers (constants or abbreviations)')
    identity={'imports':checker.imports(),'context':context,'names':list(names)}
    key=digest(identity)
    cache=getattr(checker,'_symbol_cache',None)
    if cache is None: cache={};checker._symbol_cache=cache
    if key in cache:return cache[key]
    directory=checker.work_dir/'symbol-resolution'/key
    directory.mkdir(parents=True,exist_ok=True)
    block=r'''ML \<open>
local
  val ctxt = @{context} |> Config.put show_markup false
    |> Config.put Proof_Context.show_abbrevs false
    |> Config.put show_types true
    |> Config.put show_sorts true
    |> Config.put Name_Space.names_long true;
  val digits = "0123456789abcdef";
  fun hex c = let val b = Char.ord c in String.implode
    [String.sub (digits, b div 16), String.sub (digits, b mod 16)] end;
  val stream = TextIO.openOut "/symbol-output/resolution.tsv";
  fun row tag fields = TextIO.output (stream,
    space_implode "\t" (tag :: map (String.translate hex) fields) ^ "\n");
  fun inspect name =
    let
      val parsed = Exn.capture (fn () =>
        let
          val term = Syntax.read_term ctxt name;
          val constants = Term.add_const_names term [];
          val _ = if null constants then error "Identifier resolved only as a free variable" else ();
          val printed = Print_Mode.setmp [] (Syntax.string_of_term ctxt) term;
          val typ = Print_Mode.setmp [] (Syntax.string_of_typ ctxt) (fastype_of term);
        in
          ("RESOLVED", [name, printed, typ, space_implode "\n" constants]) ::
          map (fn (n,T) => ("CONSTANT", [name,n,Print_Mode.setmp [] (Syntax.string_of_typ ctxt) T])) (Term.add_consts term [])
        end) ();
    in
      (* Keep output I/O outside capture: infrastructure errors must fail the check. *)
      case parsed of
        Exn.Res rows => List.app (fn (tag, fields) => row tag fields) rows
      | Exn.Exn exn =>
          if Exn.is_interrupt exn then Exn.reraise exn
          else row "UNRESOLVED" [name, Runtime.exn_message exn]
    end;
in
  val _ = List.app inspect NAMES;
  val _ = TextIO.closeOut stream;
end
\<close>'''.replace('NAMES','['+','.join(json.dumps(n) for n in names)+']')
    label='Symbol_Lookup_'+key[:16]
    source='\n'.join(['theory '+label,'imports',*[json.dumps(i) for i in checker.imports()],
                      'begin','unbundle l4v_word_context',wrap_context(context,block),'end',''])
    check=checker._check(source,label,writable_mounts=[(directory,'/symbol-output')],
                         timeout=min(checker.timeout,120))
    evidence=freeze_evidence(check)
    durable(directory/'check.json',evidence)
    evidence_id=digest({'source_sha256':evidence.get('source_sha256'),'log_sha256':evidence.get('log_sha256')})
    if check['verdict']!='green':
        raise RuntimeError('Isabelle symbol-resolution check failed: '+check.get('diagnostic',''))
    result={}
    for tag,fields in parse_hex_tsv(directory/'resolution.tsv'):
        if tag=='RESOLVED':
            name,term,typ,constants=fields
            result[name]={'query':name,'status':'resolved','expanded_term':term,'type':typ,
                          'constant_names':constants.splitlines(),'evidence_id':evidence_id,
                          'authority':'Isabelle elaboration in frozen imports/context',
                          'is_fact':False}
        elif tag=='CONSTANT':
            name,constant,typ=fields
            result[name].setdefault('constant_types',[]).append({'name':constant,'type':typ})
        elif tag=='UNRESOLVED':
            name,diagnostic=fields
            result[name]={'query':name,'status':'unresolved','diagnostic':diagnostic,'evidence_id':evidence_id,'is_fact':False}
        else:raise RuntimeError('Unexpected symbol-resolution record')
    if set(result)!=set(names):raise RuntimeError('Incomplete symbol-resolution evidence')
    for resolution in result.values():
        resolution.update(lookup_kind='term', fact_lookup_performed=False, notice=TERM_LOOKUP_NOTICE)
    cache[key]=result
    return result
