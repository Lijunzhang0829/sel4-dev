#!/usr/bin/env python3
"""Reproduce the C input; only add the explicit int_compare call restriction."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:8e7ee430544e004b57343636038d5ce155c128e5ee002a22821cbeaa6f66d324"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-lock", action="store_true",
                        help="explicitly record revised draft source inputs")
    args = parser.parse_args()
    command = ["gcc", "-E", "-P", "-CC", "-nostdinc", "-Ic/include",
               "-Ivendor/src", "c/binary_instance.c"]
    prefix = ["docker", "run", "--rm", "--network", "none", "-v", f"{ROOT}:/project:ro",
              "-w", "/project", IMAGE]
    raw = subprocess.check_output(prefix + command, cwd=ROOT, text=True)
    normalized, count = re.subn(
        r"(?m)^(\s*)(return [^\n]*heap->compare_func\([^\n]*)$",
        r"\1__attribute__((calls(int_compare))) \2", raw)
    if count != 2:
        raise RuntimeError(f"Expected two comparator call annotations, found {count}")
    output = ROOT / "c/binary_heap_input.c"
    files = sorted((ROOT / "vendor/src").glob("*"))
    files += [ROOT / "vendor/COPYING.md", ROOT / "c/include/stdlib.h", ROOT / "c/binary_instance.c"]
    manifest = {
        "upstream": "https://github.com/fragglet/c-algorithms",
        "commit": "23d453792ed89a28ed7d2c8d4311a4d9f7822edd",
        "scope": "binary heap, sequential integer min-heap instance",
        "compiler": subprocess.check_output(prefix + ["gcc", "--version"], text=True).splitlines()[0],
        "command": command, "comparator_annotations": count, "compiler_image": IMAGE,
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }
    manifest["files"][str(output.relative_to(ROOT))] = hashlib.sha256(normalized.encode()).hexdigest()
    lock = ROOT / "source-lock.json"
    if lock.exists() and not args.refresh_lock:
        if json.loads(lock.read_text()) != manifest:
            raise RuntimeError("Source/toolchain drift: existing source-lock.json does not match")
        if not output.exists() or output.read_text() != normalized:
            raise RuntimeError("Checked-in preprocessed C differs from reproduced input")
    else:
        output.write_text(normalized)
        lock.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(output), "sha256": manifest["files"][str(output.relative_to(ROOT))]}))

if __name__ == "__main__":
    main()
