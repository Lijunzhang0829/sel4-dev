#!/usr/bin/env python3
"""Freeze or verify the repo-level proof-regeneration experiment inputs."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from regen.refine_campaign import (
    build_manifest,
    read_json,
    verify_frozen_inputs,
    verify_manifest,
    write_immutable_json,
)


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("regen/config/refine-semantic-base-campaign.json")
DEFAULT_OUTPUT = Path("reports/regen/refine-semantic-base")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.verify:
        path = args.verify if args.verify.is_absolute() else repo / args.verify
        manifest = read_json(path)
        verify_manifest(manifest)
        verify_frozen_inputs(repo, manifest)
        print(f"verified {manifest['campaign_id']}: {path}")
        return 0
    manifest = build_manifest(repo, args.config)
    output_root = args.output_root if args.output_root.is_absolute() else repo / args.output_root
    path = output_root / manifest["campaign_id"] / "Environment-Freeze" / "campaign-manifest.json"
    disposition = write_immutable_json(path, manifest)
    print(f"{disposition} {manifest['campaign_id']}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
