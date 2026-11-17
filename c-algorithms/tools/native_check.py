#!/usr/bin/env python3
"""Record sanitizer/differential checks with their exact source identities."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:8e7ee430544e004b57343636038d5ce155c128e5ee002a22821cbeaa6f66d324"

def main():
    command = ["docker", "run", "--rm", "--network", "none", "-v", f"{ROOT}:/project:ro",
               IMAGE, "bash", "/project/tools/native_check.sh"]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    paths = [*sorted((ROOT / "vendor/src").glob("*")), ROOT / "tools/native_check.c",
             ROOT / "tools/native_check.sh"]
    record = {"kind": "native_differential_not_formal_proof", "command": command,
              "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "exit_code": result.returncode, "output": result.stdout,
              "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    if result.returncode == 0:
        record["counts"] = json.loads(result.stdout.strip())
    (ROOT / "evidence/native-check.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record.get("counts", {"error": result.stdout})))
    return result.returncode

if __name__ == "__main__":
    raise SystemExit(main())
