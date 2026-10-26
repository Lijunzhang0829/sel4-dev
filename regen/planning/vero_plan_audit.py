"""Deterministic pre-freeze architecture audit for VERO theory plans.

The audit never changes allocation or imports.  It records whether each declared
shared-helper provider is already visible to all of its intended consumers and
reports seal/fan-out risks for human or planner review.  It does not claim that a
helper is necessary, sufficient, or provable.
"""

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path


POLICY = "frozen-helper-providers-v1"


def _digest(value):
    # Match vero.isabelle.benchmark.digest so both artifacts bind the same
    # plan even when a planner writes Unicode rationale text.
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be nonempty text")


def audit_vero_plan(benchmark, plan, *, require_policy=False):
    """Return source-bound graph facts or reject an invalid provider intention.

    ``require_policy`` is used only for a newly created plan.  Older accepted
    runs remain readable and receive an explicit legacy warning on recovery.
    """
    modules = plan.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("helper audit requires nonempty modules")
    names = [module.get("name") for module in modules]
    if any(not isinstance(name, str) for name in names) or len(names) != len(set(names)):
        raise ValueError("helper audit requires unique module names")
    known = set(names)
    order = plan.get("execution_order")
    if not isinstance(order, list) or Counter(order) != Counter(names):
        raise ValueError("helper audit requires an exact execution order")
    contracts = benchmark.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("helper audit requires benchmark contracts")
    contract_ids = {row.get("id") for row in contracts}
    if None in contract_ids or len(contract_ids) != len(contracts):
        raise ValueError("helper audit requires unique benchmark contract IDs")

    dependencies = {}
    sizes = {}
    owners = {}
    for module in modules:
        name = module["name"]
        imports = module.get("imports")
        assigned = module.get("contracts")
        if (not isinstance(imports, list) or any(item not in known for item in imports)
                or len(imports) != len(set(imports))):
            raise ValueError("helper audit found invalid imports for " + name)
        if not isinstance(assigned, list):
            raise ValueError("helper audit found invalid contracts for " + name)
        dependencies[name] = list(imports)
        sizes[name] = len(assigned)
        for contract_id in assigned:
            if contract_id in owners:
                raise ValueError("helper audit found duplicate contract " + str(contract_id))
            owners[contract_id] = name
    if set(owners) != contract_ids:
        raise ValueError("helper audit requires exact contract coverage")

    closure = {}
    paths = {}

    def visit(name, stack=()):
        if name in stack:
            raise ValueError("helper audit found cyclic imports")
        if name not in closure:
            visible = set()
            best = [name]
            for provider in dependencies[name]:
                provider_visible, provider_path = visit(provider, stack + (name,))
                visible.add(provider)
                visible.update(provider_visible)
                candidate = provider_path + [name]
                if len(candidate) > len(best):
                    best = candidate
            closure[name] = visible
            paths[name] = best
        return closure[name], paths[name]

    for name in names:
        visit(name)

    policy = plan.get("architecture_policy")
    if require_policy and policy != POLICY:
        raise ValueError("new VERO plan requires architecture_policy=" + POLICY)
    raw_intents = plan.get("helper_provider_intents")
    if require_policy and not isinstance(raw_intents, list):
        raise ValueError("new VERO plan requires helper_provider_intents")
    if raw_intents is None:
        raw_intents = []
    if not isinstance(raw_intents, list):
        raise ValueError("helper_provider_intents must be a list")

    seen, intents = set(), []
    for index, intent in enumerate(raw_intents):
        label = f"helper_provider_intents[{index}]"
        expected = {"id", "provider", "consumers", "contract_ids", "rationale"}
        if not isinstance(intent, dict) or set(intent) != expected:
            raise ValueError(label + " fields must be id, provider, consumers, contract_ids, rationale")
        intent_id = intent["id"]
        _nonempty(intent_id, label + ".id")
        _nonempty(intent["rationale"], label + ".rationale")
        if intent_id in seen:
            raise ValueError("duplicate helper provider intent: " + intent_id)
        seen.add(intent_id)
        provider = intent["provider"]
        consumers = intent["consumers"]
        selected = intent["contract_ids"]
        if provider not in known:
            raise ValueError(label + " references unknown provider")
        if (not isinstance(consumers, list) or not consumers
                or len(consumers) != len(set(consumers)) or any(c not in known for c in consumers)):
            raise ValueError(label + " requires unique known consumers")
        if (not isinstance(selected, list) or not selected
                or len(selected) != len(set(selected)) or any(c not in contract_ids for c in selected)):
            raise ValueError(label + " requires unique known contract IDs")
        unreachable = sorted(consumer for consumer in consumers
                             if provider != consumer and provider not in closure[consumer])
        if unreachable:
            raise ValueError(label + " provider is not imported by consumers: " + ", ".join(unreachable))
        unrelated = sorted(contract_id for contract_id in selected
                           if owners[contract_id] not in consumers)
        if unrelated:
            raise ValueError(label + " contracts must belong to declared consumers: " + ", ".join(unrelated))
        intents.append({**intent, "reachable": True,
                        "consumer_contracts": sum(sizes[c] for c in consumers)})

    rows = []
    for name in names:
        direct = [consumer for consumer in names if name in dependencies[consumer]]
        transitive = [consumer for consumer in names if name in closure[consumer]]
        rows.append({
            "theory": name,
            "contracts": sizes[name],
            "required_imports": dependencies[name],
            "direct_consumers": direct,
            "transitive_consumers": transitive,
            "transitive_consumer_contracts": sum(sizes[c] for c in transitive),
            "global_aux_available": benchmark.get("schema_version") == 2,
            "declared_provider_intents": [row["id"] for row in intents if row["provider"] == name],
        })

    warnings = []
    if policy != POLICY:
        warnings.append({"kind": "legacy-plan", "message":
            "Plan predates explicit frozen helper-provider intentions."})
    elif len(names) > 1 and not intents:
        warnings.append({"kind": "no-shared-provider-intents", "message":
            "The multi-theory plan declares no anticipated cross-theory helper provider."})
    broad_limit = max(2, (len(names) * 3 + 3) // 4)
    for row in rows:
        if len(row["transitive_consumers"]) >= broad_limit:
            warnings.append({"kind": "broad-seal-fanout", "theory": row["theory"],
                "consumer_theories": len(row["transitive_consumers"]),
                "consumer_contracts": row["transitive_consumer_contracts"]})
    critical = max((paths[name] for name in names), key=lambda value: (len(value), value))
    if len(critical) > 7:
        warnings.append({"kind": "long-seal-critical-path", "theories": critical})

    return {
        "artifact_type": "vero-plan-architecture-audit",
        "schema_version": 1,
        "verdict": "pass",
        "interpretation": (
            "Mechanical topology and declared-helper reachability only; it does not certify "
            "that an undeclared helper is unnecessary or that a declared helper is provable."
        ),
        "benchmark_id": benchmark.get("benchmark_id"),
        "plan_sha256": _digest(plan),
        "architecture_policy": policy,
        "theory_count": len(names),
        "contract_count": len(contract_ids),
        "local_global_aux_slots": len(names) if benchmark.get("schema_version") == 2 else 0,
        "helper_provider_intents": intents,
        "critical_path": {"theories": critical, "edges": len(critical) - 1},
        "theories": rows,
        "warnings": warnings,
        "mutates_plan": False,
        "model_called": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-policy", action="store_true")
    args = parser.parse_args()
    benchmark = json.loads(args.benchmark.read_text())
    plan = json.loads(args.plan.read_text())
    report = audit_vero_plan(benchmark, plan, require_policy=args.require_policy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "verdict": report["verdict"],
        "theories": report["theory_count"],
        "contracts": report["contract_count"],
        "helper_provider_intents": len(report["helper_provider_intents"]),
        "critical_path_edges": report["critical_path"]["edges"],
        "warnings": len(report["warnings"]),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
