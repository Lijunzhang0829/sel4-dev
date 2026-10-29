#!/usr/bin/env python3
"""Build the standalone intervaltree sessions without changing global settings.

The default Docker backend mounts sources read only and keeps heaps inside an
ephemeral container. Logs and a machine-readable result live under build/.
The optional host backend isolates its generated settings and heaps there too.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid


PROJECT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("docker", "host"), default="docker")
    parser.add_argument("--image", default="sel4-public:tuned")
    parser.add_argument("--isabelle", type=Path,
                        default=Path("/home/lijun/seL4-docker-main/verification/isabelle/bin/isabelle")
                        if Path("/home/lijun/seL4-docker-main/verification/isabelle/bin/isabelle").exists()
                        else PROJECT.parent / "verification/isabelle/bin/isabelle")
    parser.add_argument("--directory", type=Path, default=PROJECT,
                        help="ROOT directory (also permits an isolated smoke session)")
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=600,
                        help="Isabelle session timeout, in seconds")
    parser.add_argument("--build-dir", type=Path, default=PROJECT / "build")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    tool = args.isabelle.resolve()
    if not tool.is_file():
        parser.error(f"Isabelle executable does not exist: {tool}")
    if args.threads < 1 or args.timeout < 1:
        parser.error("threads and timeout must be positive")
    if args.backend == "docker":
        return docker_build(args, tool)
    base_env = dict(os.environ)
    # An enclosing isolated build must not silently become the base toolchain.
    for key in ("ISABELLE_SETTINGS_PRESENT",):
        base_env.pop(key, None)
    query = subprocess.run(
        [str(tool), "getenv", "-b", "ISABELLE_HOME_USER", "ISABELLE_HEAPS"],
        check=True, text=True, capture_output=True, env=base_env,
    ).stdout.splitlines()
    if len(query) != 2:
        raise RuntimeError(f"Unexpected Isabelle getenv output: {query!r}")
    base_user, base_heaps = map(Path, query)
    build_dir = args.build_dir.resolve()
    user_home = build_dir / "user"
    # Match the installation identifier (empty in this workspace's snapshot).
    identifier = subprocess.run(
        [str(tool), "getenv", "-b", "ISABELLE_IDENTIFIER"],
        check=True, text=True, capture_output=True, env=base_env,
    ).stdout.strip()
    settings_dir = user_home / ".isabelle" / identifier / "etc"
    settings_dir.mkdir(parents=True, exist_ok=True)
    components_file = base_user / "etc/components"
    components: list[str] = []
    if components_file.exists():
        for line in components_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            component = Path(os.path.expandvars(line)).expanduser()
            if not component.is_absolute():
                component = base_user / component
            components.append(str(component.resolve()))
    (settings_dir / "components").write_text("\n".join(components) + "\n")
    # Isabelle's intended USER_HOME override controls all generated user state.
    (settings_dir / "settings").write_text(
        "isabelle_scala_service isabelle.Tools\n"
        f"ISABELLE_HEAPS_SYSTEM={shlex.quote(str(base_heaps))}\n"
        'ISABELLE_TOOL_JAVA_OPTIONS="-Djava.awt.headless=true -Xms128m -Xmx1g -Xss8m"\n'
    )
    env = dict(base_env, USER_HOME=str(user_home))
    command = [str(tool), "build", "-j", "1", "-o", f"threads={args.threads}",
               "-o", "document=false", "-o", f"timeout={args.timeout}"]
    if args.session:
        command += ["-d", str(args.directory.resolve()), *args.session]
    else:
        command += ["-D", str(args.directory.resolve())]
    metadata = {
        "command": command, "user_home": str(user_home),
        "base_heaps_read_only": str(base_heaps), "components": components,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "kind": "local Isabelle preparation check; not an LLM evaluation",
    }
    build_dir.mkdir(parents=True, exist_ok=True)
    print("USER_HOME=" + shlex.quote(str(user_home)) + " " + shlex.join(command), flush=True)
    if args.prepare_only:
        metadata["status"] = "prepared_only"
        (build_dir / "build-result.json").write_text(json.dumps(metadata, indent=2) + "\n")
        return 0
    started = time.monotonic()
    with (build_dir / "build.log").open("w") as log:
        process = subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        returncode = process.wait()
    metadata.update(returncode=returncode, elapsed_seconds=time.monotonic() - started,
                    status="passed" if returncode == 0 else "failed",
                    finished_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    (build_dir / "build-result.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return returncode


def docker_build(args: argparse.Namespace, tool: Path) -> int:
    """Reuse the known working project image, never mount writable shared heaps."""
    directory = args.directory.resolve()
    container_tool = "/workspace/verification/isabelle/bin/isabelle"
    container_name = "intervaltree-check-" + uuid.uuid4().hex[:12]
    build_dir = args.build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    bound_hashes = source_hashes(directory)
    snapshot = build_dir / ("snapshot-" + container_name.rsplit("-", 1)[1])
    project_snapshot = snapshot / "project"
    extra_snapshot = snapshot / "external"
    project_snapshot.mkdir(parents=True)
    for name, digest in bound_hashes.items():
        source_path = Path(name)
        content = source_path.read_bytes()
        import hashlib
        if hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError(f"Source changed during snapshot creation; retry: {name}")
        destination = ((project_snapshot / source_path.relative_to(PROJECT))
                       if source_path.is_relative_to(PROJECT)
                       else (extra_snapshot / source_path.relative_to(directory)))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    command = ["docker", "run", "--rm", "--network", "none", "--name", container_name,
               "--mount", f"type=bind,src={tool.parent.parent},dst=/workspace/verification/isabelle,readonly",
               "--mount", f"type=bind,src={project_snapshot},dst={PROJECT},readonly"]
    if not directory.is_relative_to(PROJECT):
        command += ["--mount", f"type=bind,src={extra_snapshot},dst={directory},readonly"]
    command += [args.image, container_tool, "env",
                f"ML_OPTIONS=-H 256 --maxheap 2048 --stackspace 64 --gcthreads {args.threads}",
                "ISABELLE_TOOL_JAVA_OPTIONS=-Djava.awt.headless=true -Xms128m -Xmx1g -Xss4m",
                "ISABELLE_BUILD_JAVA_OPTIONS=-Xms128m -Xmx1g -Xss4m",
                container_tool, "build", "-j", "1", "-o", f"threads={args.threads}",
                "-o", "document=false", "-o", f"timeout={args.timeout}"]
    command += (["-d", str(directory), *args.session] if args.session else ["-D", str(directory)])
    metadata = {
        "command": command, "backend": "docker", "image": args.image,
        "shared_heaps_mounted": False, "writable_host_mounts": [],
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "kind": "local Isabelle preparation check; not an LLM evaluation",
        "sources_sha256": bound_hashes, "source_snapshot": str(snapshot),
    }
    print(shlex.join(command), flush=True)
    if args.prepare_only:
        metadata["status"] = "prepared_only"
        (build_dir / "build-result.json").write_text(json.dumps(metadata, indent=2) + "\n")
        return 0
    started = time.monotonic()
    process = None
    try:
        with (build_dir / "build.log").open("w") as log:
            process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, bufsize=1)
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            returncode = process.wait()
    except KeyboardInterrupt:
        subprocess.run(["docker", "stop", "--time", "2", container_name], capture_output=True)
        returncode = 130
        if process is not None:
            process.wait()
    unchanged = metadata["sources_sha256"] == source_hashes(directory)
    metadata.update(returncode=returncode, elapsed_seconds=time.monotonic() - started,
                    sources_unchanged_during_build=unchanged,
                    status="passed" if returncode == 0 and unchanged else "failed",
                    finished_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    (build_dir / "build-result.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return returncode if unchanged else 2


def source_hashes(directory: Path) -> dict[str, str]:
    import hashlib
    import re
    if directory == PROJECT:
        files = {p.resolve() for p in (PROJECT / "theories").rglob("*.thy")}
    else:
        files = {p.resolve() for p in directory.rglob("*.thy")
                 if not any(part in {"user", "check"} or part.startswith("snapshot-")
                            for part in p.relative_to(directory).parts)}
    files.update(p.resolve() for p in directory.glob("ROOT"))
    project_theories = {p.stem: p.resolve() for p in PROJECT.glob("theories/*.thy")}
    # Sanitized projects intentionally reuse the original theory names. Prefer
    # their local files; resolving by short name into reference/ would leak proofs.
    local_theories = {p.stem: p for p in files if p.suffix == ".thy"}
    # Bind imported project definitions even for an external temporary session.
    while True:
        before = len(files)
        for p in list(files):
            source = p.read_text()
            imports = re.search(r"\bimports\b(.*?)\bbegin\b", source, re.S)
            if imports or p.name == "ROOT":
                scope = source if p.name == "ROOT" else imports.group(1)
                for name, dependency in project_theories.items():
                    if re.search(r"\b" + re.escape(name) + r"\b", scope):
                        files.add(local_theories.get(name, dependency))
        if len(files) == before:
            break
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


if __name__ == "__main__":
    sys.exit(main())
