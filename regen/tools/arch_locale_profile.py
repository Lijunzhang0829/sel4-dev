"""Read-only metadata and native runtime sampling for the isolated Arch study."""
import re

METADATA = r'''
ML \<open>
let
  val thy = @{theory};
  val arch = Locale.intern thy "Arch";
  val deps = Locale.dest_dependencies [] thy;
  fun closure seen [] = seen
    | closure seen (name :: pending) =
        if member (op =) seen name then closure seen pending
        else closure (name :: seen)
          (map #source (filter (fn (d: Locale.locale_dependency) => #target d = name) deps) @ pending);
  fun report name =
    let
      val facts: Attrib.fact list = maps #2 (Locale.locale_notes thy name);
      val thms = maps (fn (_, groups) => maps #1 groups) facts;
      val attrs = fold (fn ((_, atts), groups) => fn n =>
        n + length atts + fold (fn (_, a) => fn k => k + length a) groups 0) facts 0;
    in writeln ("REGEN_ARCH_LOCALE " ^ name ^ " " ^ string_of_int (length facts) ^
      " " ^ string_of_int (length thms) ^ " " ^ string_of_int attrs) end;
  val _ = List.app report (closure [] [arch]);
in () end
\<close>
'''


def profiling_command(command):
    result = list(command)
    if result.count('-T') != 1:
        raise ValueError('profiling requires exactly one frozen theory load')
    at = result.index('-T')
    if result[at+1] != '/candidate/Timing_Scheduler_Prefix':
        raise ValueError('profiling may only load the fixed diagnostic theory')
    # isabelle process -T expands to the same use_thy call in ml_process.scala.
    result[at:at+2] = ['-e', 'ML_Profiling.profile_time '
                      '(fn () => use_thy "/candidate/Timing_Scheduler_Prefix") ()']
    return result


def parse_profile(log):
    plain = re.sub(r'\x05[^\x05]*\x05', '', log)
    match = re.search(r'profile_time:\s*\n(.*?)^\s*(\d+) TOTAL\s*$', plain, re.M | re.S)
    if not match:
        raise ValueError('native runtime profile missing')
    entries = [{'samples': int(n), 'name': name.strip()} for n, name in
               re.findall(r'^\s*(\d+) (.+)$', match[1], re.M)]
    total = int(match[2])
    if total <= 0 or sum(e['samples'] for e in entries) != total:
        raise ValueError('native runtime profile totals do not match')
    locales = [{'name': name, 'fact_bindings': int(bindings), 'theorem_slots': int(thms),
                'attribute_sources': int(attrs)} for name, bindings, thms, attrs in
               re.findall(r'^REGEN_ARCH_LOCALE (\S+) (\d+) (\d+) (\d+)$', plain, re.M)]
    if not locales:
        raise ValueError('Arch locale metadata missing')
    return {'total_samples': total, 'entries': sorted(entries, key=lambda e: -e['samples']),
            'locales': locales, 'scope': 'Whole three-node theory CPU sampling, not a call graph '
            'or per-command wall time; metadata collected after all measured proof nodes.'}
