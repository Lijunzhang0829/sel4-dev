#!/usr/bin/env python3
"""Post-freeze comparison of an M4 schedule with held-out human Proof imports."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from refine_campaign import read_json, sha256_json, write_immutable_json
from refine_m2_preflight import theory_imports


REPO = Path(__file__).resolve().parent.parent



def project_proof_paths(
    successors: dict[str, set[str]], proof_theories: set[str],
) -> dict[tuple[str, str], list[str]]:
    projected_paths: dict[tuple[str, str], list[str]] = {}
    for provider in sorted(proof_theories):
        queue = [(successor, [provider, successor]) for successor in sorted(successors[provider])]
        best_support_depth: dict[str, int] = {}
        while queue:
            current, path = queue.pop(0)
            if current in proof_theories:
                if current != provider:
                    key = (provider, current)
                    previous = projected_paths.get(key)
                    if previous is None or (len(path), path) < (len(previous), previous):
                        projected_paths[key] = path
                continue
            depth = len(path)
            if current in best_support_depth and best_support_depth[current] <= depth:
                continue
            best_support_depth[current] = depth
            for successor in sorted(successors[current]):
                if successor not in path:
                    queue.append((successor, [*path, successor]))
    return projected_paths


def build_comparison(repo: Path, campaign_dir: Path) -> dict[str, Any]:
    gate = read_json(campaign_dir / "M4/m4-gate.json")
    if gate.get("verdict") != "pass":
        raise ValueError("post-freeze comparison requires an M4 PASS gate")
    file_list = read_json(campaign_dir / "M4/file-generation-list.json")
    if file_list.get("list_payload_sha256") != gate.get("file_generation_list_sha256"):
        raise ValueError("M4 list/gate identity mismatch")

    classification = read_json(campaign_dir / "M1/private/proof-support-classification.json")
    private = read_json(campaign_dir / "M1/private/contract-manifest.json")
    source_by_theory = {
        item["theory"]: item["source_path"]
        for item in private["trusted_source_inventory"]
    }
    class_by_theory = {
        item["theory"]: item["classification"] for item in classification["theories"]
    }
    slot_by_theory = {
        item["theory"]: item["file_slot"] for item in classification["theories"]
    }
    theory_by_stem = {
        theory.rsplit(".", 1)[-1]: theory for theory in class_by_theory
    }
    proof_theories = {
        theory for theory, theory_class in class_by_theory.items() if theory_class == "Proof"
    }

    successors: dict[str, set[str]] = {theory: set() for theory in class_by_theory}
    for consumer in sorted(class_by_theory):
        source = (repo / source_by_theory[consumer]).read_text(
            encoding="utf-8", errors="replace",
        )
        for imported in theory_imports(source):
            provider = theory_by_stem.get(imported.rsplit("/", 1)[-1].rsplit(".", 1)[-1])
            if provider and provider != consumer:
                successors[provider].add(consumer)

    direct_edges = {
        (provider, consumer)
        for provider in proof_theories
        for consumer in successors[provider]
        if consumer in proof_theories
    }
    projected_paths = project_proof_paths(successors, proof_theories)

    order = file_list["serial_fallback"]
    position = {slot: index for index, slot in enumerate(order)}

    def edge_record(provider: str, consumer: str, path: list[str]) -> dict[str, Any]:
        provider_slot = slot_by_theory[provider]
        consumer_slot = slot_by_theory[consumer]
        return {
            "provider_file": provider_slot,
            "consumer_file": consumer_slot,
            "provider_position": position[provider_slot],
            "consumer_position": position[consumer_slot],
            "schedule_agrees": position[provider_slot] < position[consumer_slot],
            "relation": "direct-proof-import" if len(path) == 2 else "support-collapsed-import-path",
            "theory_path": path,
        }

    direct_records = [
        edge_record(provider, consumer, [provider, consumer])
        for provider, consumer in sorted(direct_edges)
    ]
    projected_records = [
        edge_record(provider, consumer, projected_paths[(provider, consumer)])
        for provider, consumer in sorted(projected_paths)
    ]
    direct_agrees = sum(item["schedule_agrees"] for item in direct_records)
    projected_agrees = sum(item["schedule_agrees"] for item in projected_records)
    result = {
        "schema_version": 2,
        "artifact_type": "m4-post-freeze-human-import-comparison",
        "campaign_id": gate["campaign_id"],
        "comparison_contaminated": True,
        "model_visible": False,
        "must_not_feed_back_into_generation": True,
        "interpretation": (
            "Human imports are a held-out engineering comparison, not a ground-truth "
            "dependency graph under Setting B. Support-collapsed paths stop at the first "
            "downstream Proof theory and are never generation inputs."
        ),
        "file_generation_list_sha256": file_list["list_payload_sha256"],
        "direct_proof_import": {
            "edge_count": len(direct_records),
            "schedule_agrees_count": direct_agrees,
            "schedule_violates_count": len(direct_records) - direct_agrees,
            "agreement_ratio": 0.0 if not direct_records else direct_agrees / len(direct_records),
            "edges": direct_records,
        },
        "support_collapsed_proof_projection": {
            "edge_count": len(projected_records),
            "schedule_agrees_count": projected_agrees,
            "schedule_violates_count": len(projected_records) - projected_agrees,
            "agreement_ratio": (
                0.0 if not projected_records else projected_agrees / len(projected_records)
            ),
            "edges": projected_records,
        },
    }
    result["comparison_payload_sha256"] = sha256_json(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    campaign_dir = args.campaign_dir if args.campaign_dir.is_absolute() else repo / args.campaign_dir
    output = args.output or campaign_dir / "M4-comparison/human-import-comparison.json"
    if not output.is_absolute():
        output = repo / output
    result = build_comparison(repo, campaign_dir)
    disposition = write_immutable_json(output, result)
    print({
        "disposition": disposition,
        "campaign_id": result["campaign_id"],
        "direct": result["direct_proof_import"],
        "support_collapsed": result["support_collapsed_proof_projection"],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
