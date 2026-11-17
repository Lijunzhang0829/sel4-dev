#!/usr/bin/env python3
"""Native preparation checks, isolated from proof-generation experiments."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
AFP = "afp-2026/mirror-afp-2026-fca9585c6341cbf0f91816850b375c02e2c17ba6/thys"
IMAGE = "sha256:8e7ee430544e004b57343636038d5ce155c128e5ee002a22821cbeaa6f66d324"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, default=Path("/tmp/autocorres2-c-screen-20261103"))
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    build = ROOT / "build"
    user = build / "user"
    settings = user / ".isabelle/Isabelle2026/etc"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "settings").write_text(
        'ISABELLE_TOOL_JAVA_OPTIONS="-Djava.awt.headless=true -Xms256m -Xmx2g -Xss16m"\n'
        'ISABELLE_TMP_PREFIX="/tmp/calg-isabelle"\n'
        'ML_OPTIONS="--minheap 500 --enablegcsharing"\n')
    # Reuse immutable dependency heaps while keeping this project's outputs separate.
    cache = args.environment / "isolated-isabelle-home/Isabelle2026/heaps"
    for source in cache.rglob("*"):
        dest = user / ".isabelle/Isabelle2026/heaps" / source.relative_to(cache)
        if source.is_file() and not dest.exists() and not dest.is_symlink():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(Path("/screen") / source.relative_to(args.environment))
    sessions = args.session or ["Calg_Binary_Foundation"]
    command = ["docker", "run", "--rm", "--network", "none", "--cpus", "4", "--memory", "16g",
               "--user", f"{os.getuid()}:{os.getgid()}",
               "-e", "USER_HOME=/project/build/user", "-e", "L4V_ARCH=X64",
               "-v", f"{args.environment.resolve()}:/screen:ro",
               "-v", f"{ROOT}:/project:ro", "-v", f"{build}:/project/build:rw",
               "-w", "/project", IMAGE,
               "/screen/Isabelle2026/bin/isabelle", "build", "-v", "-j", "1",
               "-o", "threads=4", "-o", "document=false", "-o", f"timeout={args.timeout}",
               "-d", f"/screen/{AFP}", "-d", "/project", *sessions]
    input_paths = [ROOT / "ROOT", ROOT / "source-lock.json", ROOT / "environment-lock.json",
                        *sorted((ROOT / "theories").glob("*.thy")),
                        *sorted((ROOT / "targets-session").glob("*.thy")),
                        ROOT / "c/binary_heap_input.c"]
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    started = time.monotonic()
    log_path = build / f"{stamp}.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        code = proc.wait()
    after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    inputs_unchanged = inputs == after
    if not inputs_unchanged:
        code = 3
    result = {"kind": "preparation_native_check", "command": command,
              "started_utc": stamp, "elapsed_seconds": time.monotonic() - started,
              "exit_code": code, "inputs": inputs, "log": str(log_path.relative_to(ROOT)),
              "inputs_unchanged_during_check": inputs_unchanged,
              "target_proofs_generated": 0}
    (build / f"{stamp}.json").write_text(json.dumps(result, indent=2) + "\n")
    (build / "latest.json").write_text(json.dumps(result, indent=2) + "\n")
    return code

if __name__ == "__main__":
    raise SystemExit(main())
