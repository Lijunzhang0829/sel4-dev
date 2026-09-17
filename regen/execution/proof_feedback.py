"""Shared cold/warm diagnostics and bounded cross-round proof evidence."""
import re


def normalized_diagnostic(text):
    return re.sub(r'^\*\*\* ?', '', str(text), flags=re.M)


def semantic_goal(text):
    text = normalized_diagnostic(text)
    match = re.search(r'^goal(?:\s+[^\n:]+)?:\s*(.*?)(?=\nAt command|\n### theory|\nException-|\Z)', text, re.M | re.S)
    return ' '.join(match.group(1).split()) if match else None


def checked_history(feedback):
    feedback = feedback or {}
    items = feedback.get('checked_history', []) + feedback.get('attempts', [])
    items += [{**x, 'check': {'diagnostic': x.get('diagnostic', '')}}
              for x in feedback.get('proof_candidates', [])]
    result, seen = [], set()
    for item in items:
        proof = item.get('proof_body')
        check = item.get('check', {})
        if not isinstance(proof, str) or not proof.strip() or check.get('isabelle_called') is False:
            continue
        identity = (proof, check.get('diagnostic', ''))
        if identity in seen:
            continue
        seen.add(identity)
        result.append({k: item[k] for k in ('attempt', 'proof_body', 'used_premise_ids', 'check') if k in item})
    # Keep semantic evidence even if recent outputs are syntax failures.
    semantic = [x for x in result if semantic_goal(x['check'].get('diagnostic', ''))]
    chosen = semantic[-4:] + result[-4:]
    return [x for i, x in enumerate(chosen) if x not in chosen[:i]]
