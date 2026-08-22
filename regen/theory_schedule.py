#!/usr/bin/env python3
"""Render and validate the proof-generation order for a regen theory DAG."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = REPO / "reports" / "regen" / "refine-arm-session"


def read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def qualified_theories(names: list[str]) -> set[str]:
    return {
        name if "." in name else f"Refine.{name}"
        for name in names
    }


def build_schedule(campaign_dir: Path) -> dict:
    graph = read_json(campaign_dir / "theory_graph.json")
    state = read_json(campaign_dir / "state.json")
    theories = {item["name"]: item for item in graph["theories"]}
    layers = graph["layers"]
    layer_of = {
        theory: layer_index
        for layer_index, layer in enumerate(layers)
        for theory in layer
    }
    if set(layer_of) != set(theories):
        raise ValueError("theory layers do not cover the target graph exactly")

    for theory, item in theories.items():
        for dependency in item["target_imports"]:
            if dependency not in theories:
                raise ValueError(f"unknown target import: {theory} -> {dependency}")
            if layer_of[dependency] >= layer_of[theory]:
                raise ValueError(
                    f"invalid topological order: {theory} imports {dependency}"
                )

    sealed = qualified_theories(state.get("theories", []))
    records = []
    for layer_index, layer in enumerate(layers):
        for theory in layer:
            item = theories[theory]
            dependencies = set(item["target_imports"])
            if theory in sealed:
                status = "sealed"
            elif dependencies <= sealed:
                status = "ready"
            else:
                status = "blocked"
            records.append({
                "theory": theory,
                "file": Path(item["source_path"]).name,
                "topological_wave": layer_index,
                "status": status,
                "target_imports": sorted(dependencies),
                "waiting_for": sorted(dependencies - sealed),
                "contract_count": len(item["contracts"]),
            })

    ready = [item["theory"] for item in records if item["status"] == "ready"]
    return {
        "schema_version": 1,
        "policy": {
            "edge_meaning": "A target theory may start only after every target_import is sealed.",
            "parallelism": "Theories in one wave are independent and may run concurrently.",
            "barrier": "No global wave barrier: re-evaluate readiness after every theory seal.",
            "serial_fallback": "Sort by topological_wave, then canonical theory name.",
        },
        "summary": {
            "theory_count": len(theories),
            "wave_count": len(layers),
            "sealed_count": len(sealed & set(theories)),
            "ready_count": len(ready),
            "ready_theories": ready,
        },
        "sealed_theories": sorted(sealed & set(theories)),
        "waves": [
            {
                "wave": index,
                "theories": [
                    item for item in records if item["topological_wave"] == index
                ],
            }
            for index in range(len(layers))
        ],
        "serial_fallback": [item["theory"] for item in records],
    }


def render_markdown(schedule: dict) -> str:
    lines = [
        "# Refine proof-generation order",
        "",
        "The DAG defines a partial order. Theories in the same wave may be generated in parallel. ",
        "There is no global wave barrier: after each seal, recompute which direct imports are sealed.",
        "",
        f"- Theories: {schedule['summary']['theory_count']}",
        f"- Waves: {schedule['summary']['wave_count']}",
        f"- Sealed: {schedule['summary']['sealed_count']}",
        f"- Ready now: {', '.join(schedule['summary']['ready_theories'])}",
        "",
        "| Wave | Status | Theory file | Direct Refine dependencies | Contracts |",
        "|---:|---|---|---|---:|",
    ]
    for wave in schedule["waves"]:
        for item in wave["theories"]:
            dependencies = ", ".join(item["target_imports"]) or "-"
            lines.append(
                f"| {wave['wave']} | {item['status']} | `{item['file']}` | "
                f"{dependencies} | {item['contract_count']} |"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.resolve()
    schedule = build_schedule(campaign_dir)
    json_path = campaign_dir / "generation-order.json"
    markdown_path = campaign_dir / "generation-order.md"
    write_json(json_path, schedule)
    markdown_path.write_text(render_markdown(schedule), encoding="utf-8")
    print(json.dumps({
        "json": str(json_path),
        "markdown": str(markdown_path),
        "summary": schedule["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
