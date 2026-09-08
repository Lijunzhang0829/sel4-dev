#!/usr/bin/env python3
"""Materialize and Isabelle-check the command-level Refine Semantic Base."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from typing import Any, Iterable


SESSION_CATALOGS = (
    "lib/Word_Lib", "lib/ML_Utils", "lib/Basics", "lib/Eisbach_Tools",
    "lib/Monads", "lib/sep_algebra", "lib", "tools/c-parser",
    "tools/autocorres", "tools/asmrefine", "spec", "proof",
)
DOCUMENT_COMMANDS = {"chapter", "section", "subsection", "subsubsection", "paragraph", "text", "txt"}
SEMANTIC_CORE_COMMANDS = {
    "abbreviation", "coinductive", "coinductive_set", "constants", "consts",
    "datatype", "definition", "defs", "fun", "inductive", "inductive_set",
    "locale", "named_theorems", "no_notation", "no_syntax", "notation",
    "primrec", "record", "syntax", "translations", "type_synonym", "unbundle",
}
SHELL_NAME_RE = re.compile(
    r"^(\s*(?:lemma|theorem|corollary)\s+)([A-Za-z0-9_']+)", re.M,
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def docker_image_identity(image: str) -> dict[str, Any]:
    """Record the local image identity without making it a build prerequisite."""
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"],
        text=True, capture_output=True,
    )
    if result.returncode != 0:
        return {
            "reference": image,
            "inspect_verdict": "unavailable",
            "diagnostic": (result.stdout + result.stderr)[-2000:],
        }
    payload = json.loads(result.stdout)
    return {
        "reference": image,
        "inspect_verdict": "pass",
        "image_id": payload.get("Id"),
        "repo_digests": sorted(payload.get("RepoDigests") or []),
    }


def _subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def blank_like(value: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in value)


def initial_retained_command_ids(inventory: dict[str, Any]) -> set[str]:
    """Return the deterministic semantic-core seed.

    Mixed/certificate commands are excluded initially. A green build of this
    seed plus every exact contract shell proves that the excluded commands are
    unnecessary for the frozen contracts as a group.
    """
    core = [
        command
        for theory in inventory["theories"]
        for command in theory["commands"]
        if command["role"] == "semantic_candidate"
        and command["kind"] in SEMANTIC_CORE_COMMANDS
    ]
    contaminated = [
        command["command_id"]
        for command in core
        if command.get("internal_proof_syntax_present", False)
    ]
    if contaminated:
        raise ValueError(
            "semantic-core command spans contain proof syntax: "
            + ", ".join(contaminated[:5])
        )
    return {command["command_id"] for command in core}


def all_semantic_candidate_ids(inventory: dict[str, Any]) -> set[str]:
    return {
        command["command_id"]
        for theory in inventory["theories"]
        for command in theory["commands"]
        if command["role"] == "semantic_candidate"
    }


def render_theory(theory: dict[str, Any], retained: set[str]) -> str:
    source_lines = (theory["header"]["raw_text"] + "\n").splitlines(keepends=True)
    max_line = max(
        [theory["header"]["source_end_line"]]
        + [item["source_end_line"] for item in theory["commands"]]
    )
    if len(source_lines) < max_line:
        source_lines.extend("\n" for _ in range(max_line - len(source_lines)))

    rendered = [blank_like(line) for line in source_lines]
    header_lines = theory["header"]["raw_text"].splitlines(keepends=True)
    rendered[:len(header_lines)] = header_lines
    commands_by_id = {item["command_id"]: item for item in theory["commands"]}
    needed_scopes = {
        scope_id
        for command in theory["commands"]
        if command["command_id"] in retained
        for scope_id in command.get("enclosing_scope_command_ids", [])
    }
    needed_scope_ends = {
        commands_by_id[scope_id]["matching_end_command_id"]
        for scope_id in needed_scopes
        if commands_by_id[scope_id].get("matching_end_command_id")
    }
    needed_scope_preludes = {
        commands_by_id[scope_id]["scope_prelude_command_id"]
        for scope_id in needed_scopes
        if commands_by_id[scope_id].get("scope_prelude_command_id")
    }
    for command in theory["commands"]:
        keep = (
            command["command_id"] in retained
            or command["command_id"] in needed_scopes
            or command["command_id"] in needed_scope_ends
            or command["command_id"] in needed_scope_preludes
            or command.get("closes_theory", False)
            or command["role"] == "structure"
            and command["kind"] not in DOCUMENT_COMMANDS
            and not command.get("matching_end_command_id")
            and not command.get("matching_start_command_id")
            and not command.get("matching_begin_command_id")
        )
        if not keep:
            continue
        raw_lines = command["raw_text"].splitlines(keepends=True)
        start = command["source_start_line"] - 1
        rendered[start:start + len(raw_lines)] = raw_lines
    value = "".join(rendered)
    if not value.endswith("\n"):
        value += "\n"
    return value


def _header_imports(header: str) -> list[str]:
    match = re.search(r"\bimports\b(.*?)\bbegin\b", header, re.S)
    if not match:
        return []
    return re.findall(r'"([^"]+)"|([^\s]+)', match.group(1))


def terminal_theories(inventory: dict[str, Any]) -> list[str]:
    basenames = {item["theory"].split(".")[-1] for item in inventory["theories"]}
    imported: set[str] = set()
    for theory in inventory["theories"]:
        for quoted, plain in _header_imports(theory["header"]["raw_text"]):
            token = quoted or plain
            basename = token.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
            if basename in basenames:
                imported.add(basename)
    terminals = sorted(basenames - imported)
    if not terminals:
        raise ValueError("could not determine any terminal Refine theory")
    return terminals


def _shell_statement(contract: dict[str, Any], index: int) -> str:
    statement = contract["exact_statement"]
    replacement = rf"\1contract_shell_{index:04d}"
    renamed, count = SHELL_NAME_RE.subn(replacement, statement, count=1)
    if count != 1:
        raise ValueError(f"cannot rename contract shell: {contract['contract_id']}")
    blocks = [*contract.get("contract_owned_context", []), renamed, "  oops"]
    blocks.extend("end" for _ in contract.get("contract_owned_context", []))
    return "\n".join(blocks)


def materialize_workspace(
    inventory: dict[str, Any], workspace: Path, retained: Iterable[str],
) -> dict[str, Any]:
    retained_set = set(retained)
    known = {
        command["command_id"]
        for theory in inventory["theories"] for command in theory["commands"]
    }
    unknown = sorted(retained_set - known)
    if unknown:
        raise ValueError(f"unknown retained command IDs: {unknown[:5]}")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "contract-shells").mkdir(parents=True, exist_ok=True)
    theory_records = []
    directories: set[str] = {"contract-shells"}
    shell_names = []
    for theory in inventory["theories"]:
        relative = Path(theory["workspace_path"])
        if str(relative.parent) != ".":
            directories.add(str(relative.parent))
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_theory(theory, retained_set)
        target.write_text(rendered, encoding="utf-8")
        theory_records.append({
            "theory": theory["theory"],
            "workspace_path": str(relative),
            "sha256": sha256_text(rendered),
            "retained_semantic_commands": sum(
                command["command_id"] in retained_set for command in theory["commands"]
            ),
        })
        if theory["contracts"]:
            stem = relative.stem
            shell_name = f"Contract_Shell_{stem}"
            shell_names.append(shell_name)
            shell_source = "\n\n".join(
                _shell_statement(contract, index)
                for index, contract in enumerate(theory["contracts"], 1)
            )
            (workspace / "contract-shells" / f"{shell_name}.thy").write_text(
                "\n".join([
                    f"theory {shell_name}",
                    "imports",
                    f'  "{stem}"',
                    "begin",
                    "",
                    shell_source,
                    "",
                    "end",
                    "",
                ]),
                encoding="utf-8",
            )

    terminals = terminal_theories(inventory)
    (workspace / "Semantic_Root.thy").write_text("\n".join([
        "theory Semantic_Root",
        "imports",
        *(f'  "{item}"' for item in terminals),
        "begin",
        "end",
        "",
    ]), encoding="utf-8")
    (workspace / "ROOT").write_text("\n".join([
        'session Semantic_Base in "." = BaseRefine +',
        "  sessions",
        "    Lib",
        "    CorresK",
        "    AInvs",
        "  directories",
        *(f'    "{item}"' for item in sorted(directories)),
        "  theories",
        "    Semantic_Root",
        *(f"    {item}" for item in sorted(shell_names)),
        "",
    ]), encoding="utf-8")
    return {
        "schema_version": 1,
        "artifact_type": "semantic-base-workspace-manifest",
        "campaign_id": inventory["campaign_id"],
        "session": "Semantic_Base",
        "base_import": "Semantic_Base.Semantic_Root",
        "retained_command_count": len(retained_set),
        "contract_shell_count": inventory["counts"]["contracts"],
        "theories": theory_records,
        "terminal_theories": terminals,
        "contract_shell_theories": sorted(shell_names),
    }


def run_isabelle_build(
    workspace: Path, l4v: Path, heaps: Path, *, timeout_seconds: int = 2400,
    image: str = "sel4-public:latest", isabelle: Path = Path("/home/lijun/seL4-docker-main/verification/isabelle"),
) -> dict[str, Any]:
    workspace = workspace.resolve()
    l4v = l4v.resolve()
    heaps = heaps.resolve()
    heaps.mkdir(parents=True, exist_ok=True)
    container_name = f"semantic-base-{os.getpid()}-{int(time.time())}"
    command = [
        "docker", "run", "--rm", "--network", "none", "--name", container_name,
        "--mount", f"type=bind,src={l4v},dst=/sel4-project/verification/l4v,readonly",
        "--mount", f"type=bind,src={isabelle.resolve()},dst=/workspace/verification/isabelle,readonly",
        "--mount", f"type=bind,src={workspace},dst=/semantic-base,readonly",
        "--mount", f"type=bind,src={heaps},dst=/root/.isabelle/heaps",
        "-e", "L4V_ARCH=ARM", image,
        "/workspace/verification/isabelle/bin/isabelle", "build", "-b",
    ]
    for catalog in SESSION_CATALOGS:
        command.extend(["-d", f"/sel4-project/verification/l4v/{catalog}"])
    command.extend(["-d", "/semantic-base", "Semantic_Base"])
    image_identity = docker_image_identity(image)
    started = time.time()
    try:
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=timeout_seconds,
        )
        diagnostic = result.stdout + result.stderr
        infrastructure_markers = (
            "Cannot connect to the Docker daemon", "permission denied while trying to connect",
            "Unable to find image", "invalid mount config", "another Isabelle tool is already running",
        )
        if result.returncode == 0:
            verdict = "pass"
        elif result.returncode in {125, 126, 127} or any(
            marker in diagnostic for marker in infrastructure_markers
        ):
            verdict = "infrastructure"
        else:
            verdict = "fail"
        returncode = result.returncode
    except KeyboardInterrupt:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        raise
    except subprocess.TimeoutExpired as error:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        diagnostic = _subprocess_text(error.stdout) + _subprocess_text(error.stderr)
        verdict = "timeout"
        returncode = None
    build_log = workspace / "build.log"
    build_log.write_text(diagnostic, encoding="utf-8")
    return {
        "schema_version": 1,
        "artifact_type": "semantic-base-isabelle-build",
        "verdict": verdict,
        "returncode": returncode,
        "wall_ms": int((time.time() - started) * 1000),
        "session": "Semantic_Base",
        "container_name": container_name,
        "effective_command": command,
        "container_image": image_identity,
        "build_log": {
            "path": "build.log",
            "sha256": sha256_path(build_log),
            "bytes": build_log.stat().st_size,
        },
        "diagnostic": diagnostic[-20000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--retained", type=Path)
    parser.add_argument("--l4v", type=Path)
    parser.add_argument("--heaps", type=Path)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    retained = initial_retained_command_ids(inventory)
    if args.retained:
        payload = json.loads(args.retained.read_text(encoding="utf-8"))
        retained = set(payload["retained_command_ids"])
    manifest = materialize_workspace(inventory, args.workspace, retained)
    result: dict[str, Any] = {"workspace": manifest}
    if args.l4v and args.heaps:
        result["isabelle"] = run_isabelle_build(
            args.workspace, args.l4v, args.heaps, timeout_seconds=args.timeout,
        )
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("isabelle", {"verdict": "pass"})["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
