"""Native observations at explicit consumer locations, never proof authority.

Selectors identify fact expressions in the unchanged consumer. The private
renderer inserts read-only ML_val diagnostics; ordinary proofs retain their
original bytes. Resolution/automation may instantiate a fact further, so the
observation reports remaining schematic variables rather than guessing them.
"""
import json
import re
import xml.etree.ElementTree as ET


def validate(group, nodes, proof):
    if 'use_sites' not in group:
        return  # Historical groups remain readable and replayable.
    sites = group['use_sites']
    if not isinstance(sites, list) or not sites:
        raise ValueError('use_sites must be a nonempty list')
    lines = proof.splitlines()
    seen = set()
    for site in sites:
        if not isinstance(site, dict) or set(site) != {'helper_id', 'consumer_line', 'fact'}:
            raise ValueError('each use site has helper_id, consumer_line and fact only')
        key, line, fact = site['helper_id'], site['consumer_line'], site['fact']
        if not isinstance(key, str) or key not in group['helper_ids']:
            raise ValueError('use site must select a group helper')
        if type(line) is not int or not 1 <= line <= len(lines):
            raise ValueError('consumer_line is a 1-based line of the parent proof body')
        name = nodes[key]['name']
        if (not isinstance(fact, str) or '\n' in fact or '\r' in fact
                or not re.fullmatch(re.escape(name) + r'(?:\([^\n]*\))?(?:\[[^\n]*\])?', fact)):
            raise ValueError('fact must name the helper, with optional fact selection and attributes')
        from regen.execution.proof_batches import _safe_text
        _safe_text(fact)
        text = lines[line - 1].strip()
        if not re.match(r'^(using|from|with|apply|by)\b', text):
            raise ValueError('use site must precede a using/from/with/apply/by command; put it on its own line')
        # This is a locator check, NOT an assertion about theorem dependencies.
        # Reject comment-only / quoted mentions; Isabelle evaluates the fact below.
        if (fact not in text or '(*' in text or '*)' in text
                or '"' in text[:text.index(fact)]):
            raise ValueError('fact expression must occur at the selected consumer command')
        if not re.search(r'(?<![\w\x27.])' + re.escape(fact) + r'(?![\w\x27.])', text):
            raise ValueError('fact expression must occur as a complete reference')
        identity = (key, line, fact)
        if identity in seen:
            raise ValueError('duplicate helper use site')
        seen.add(identity)
    if set(group['helper_ids']) - {s['helper_id'] for s in sites}:
        raise ValueError('use_sites must cover every group helper')


def instrument(proof, group, signature):
    sites = group.get('use_sites', [])
    if not sites:
        return proof
    before = {}
    for index, site in enumerate(sites):
        # All strings are ML literals; no model text becomes executable ML.
        attrs = ', '.join('(' + json.dumps(k) + ', ' + json.dumps(str(v)) + ')'
                          for k, v in {**site, 'index': index}.items())
        ml = r'''ML_val \<open>let
          val ctxt = \<^context>;
          val fact_text = %s;
          val tokens = Token.explode (Thy_Header.get_keywords (Proof_Context.theory_of ctxt))
            Position.none fact_text |> filter Token.is_proper;
          val refs = (case Scan.read Token.stopper (Parse.thms1 --| Scan.ahead Parse.eof) tokens of
            SOME refs => refs | NONE => error "Invalid helper use fact expression");
          val facts = Attrib.eval_thms ctxt refs;
          val {goal, facts = chained_facts, ...} = @{Isar.goal};
          fun term t = YXML.content_of (Syntax.string_of_term (Config.put show_types true ctxt) t);
          fun element name text = XML.Elem ((name, []), [XML.Text text]);
          fun fact thm = XML.Elem (("fact", []),
            [element "proposition" (term (Thm.prop_of thm)),
             element "schematic_variable_count" (Int.toString (length (Term.add_vars (Thm.prop_of thm) []))),
             element "schematic_type_variable_count" (Int.toString (length (Term.add_tvars (Thm.prop_of thm) [])))]);
          val payload = XML.string_of (XML.Elem (("helper_use", [%s]),
            [XML.Elem (("facts", []), map fact facts),
             XML.Elem (("goals", []), map (element "goal" o term) (Thm.prems_of goal)),
             XML.Elem (("chained_facts", []), map (element "proposition" o term o Thm.prop_of) chained_facts),
             XML.Elem (("local_assumptions", []), map (element "assumption" o term o Thm.term_of)
               (Assumption.all_assms_of ctxt))]));
        in writeln (%s ^ payload ^ %s) end\<close>''' % (
            json.dumps(site['fact']), attrs,
            json.dumps('REGEN_HELPER_USE_BEGIN ' + signature + '\n'),
            json.dumps('\nREGEN_HELPER_USE_END ' + signature))
        before.setdefault(site['consumer_line'], []).append(ml)
    return '\n'.join('\n'.join([*before.get(i, []), line])
                     for i, line in enumerate(proof.splitlines(), 1))


def observations(log, group, signature):
    pattern = (r'REGEN_HELPER_USE_BEGIN ' + re.escape(signature) + r'\n(.*?)\n'
               + r'REGEN_HELPER_USE_END ' + re.escape(signature))
    result = []
    for payload in re.findall(pattern, log, re.S):
        element = ET.fromstring(payload)
        index = int(element.attrib['index'])
        expected = group['use_sites'][index]
        if element.tag != 'helper_use' or any(element.attrib[k] != str(v) for k, v in expected.items()):
            raise ValueError('native helper use selector binding differs')
        facts = [{'proposition': f.findtext('proposition'),
                  'schematic_variable_count': int(f.findtext('schematic_variable_count')),
                  'schematic_type_variable_count': int(f.findtext('schematic_type_variable_count'))}
                 for f in element.findall('./facts/fact')]
        result.append({**expected, 'index': index, 'facts': facts,
                       'goals': [e.text or '' for e in element.findall('./goals/goal')],
                       'chained_facts': [e.text or '' for e in element.findall('./chained_facts/proposition')],
                       'local_assumptions': [e.text or '' for e in element.findall('./local_assumptions/assumption')],
                       'evidence_kind': 'native-fact-at-consumer-site',
                       'actual_dependency_certified': False,
                       'difficulty_certified': False})
    return result


def coverage(group, uses):
    observed = {u['helper_id'] for u in uses}
    return {'observed_use_sites': len(uses),
            'unobserved_helper_ids': [k for k in group['helper_ids'] if k not in observed],
            'full_observations_available': bool(uses)}
