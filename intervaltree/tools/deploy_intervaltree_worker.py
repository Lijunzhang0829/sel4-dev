#!/usr/bin/env python3
"""Deployment only: use the unchanged vero.isabelle plan-run and checker.

Run on machine B against a fresh release containing input/ and vero-src/.
No target-specific proof code or reference project is deployed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def call(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--auth", type=Path, required=True)
    p.add_argument("--stage", choices=["prepare", "start"], default="prepare")
    a = p.parse_args()
    release = a.release.resolve()
    if a.stage == "start":
        if not (release / "preflight.json").exists():
            raise ValueError("Successful preflight required")
        call("docker", "exec", "--user", "0:1002", "-d", a.name, "bash", "-c",
             "umask 027; exec python3 -u -m vero.isabelle plan-run "
             "--benchmark /benchmark/benchmark.json --config /control/config.json "
             "--output /results/trial > /results/launch.log 2>&1")
        return
    manifest = json.loads((release / "transfer-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((release / name).read_bytes()).hexdigest() == expected, name
    image = call("docker", "image", "inspect", "--format", "{{.Id}}", "sel4-public:tuned",
                 capture_output=True, text=True).stdout.strip()
    for name in ["control", "benchmark", "sandbox", "results", "agent-home", "public"]:
        (release / name).mkdir(exist_ok=False)
    (release / "benchmark/benchmark.json").write_bytes((release / "input/benchmark.json").read_bytes())
    (release / "public/premises.jsonl").write_text("")
    checker = {
        "backend": "fresh-process-v1", "incremental": True,
        "session": "Intervaltree_Definitions", "session_dirs": ["/semantic-base"],
        "isabelle": "/workspace/verification/isabelle/bin/isabelle",
        "timeout_seconds": 300, "total_timeout_seconds": 300, "threads": 2,
        "env": {"HOME": "/home/agent", "USER_HOME": "/home/agent"},
    }
    save(release / "benchmark/checker.json", checker)
    metrics = {"directory": "/results/trial/metrics", "run_id": a.name,
               "manifest": {"method": "vero", "dataset": "intervaltree-91",
                            "model": "gpt-6-astra", "reasoning_effort": "low",
                            "lineage": {"fresh": True, "parent_run": None}}}
    cfg = {
        "instruction_profile": "vero", "sandbox": "/sandbox", "sandbox_uid": 1001,
        "max_iterations": None, "iteration_time_chunk_seconds": None,
        "iteration_max_time_seconds": None, "total_agent_seconds": None,
        "max_contract_retries": 5, "sample_interval_seconds": 120,
        "completion_metric": "positive", "feedback": True, "resume_artifact": False,
        "plan_seconds": 600, "plan_preflight_seconds": 300,
        "plan_public_benchmark": "/run/vero-plan/benchmark.json",
        "agent": {"codex": "/opt/vero/codex", "model": "gpt-6-astra",
                  "reasoning_effort": "low", "sandbox_mode": "danger-full-access",
                  "external_isolation": "docker-nonroot", "timeout_seconds": None,
                  "home": "/home/agent", "user": "agent",
                  "command_prefix": ["runuser", "-u", "agent", "--"],
                  "metrics": metrics},
        "checker": {**checker, "command_prefix": ["runuser", "-u", "checker", "--"],
                    "env": {"HOME": "/home/checker", "USER_HOME": "/home/checker"}},
        "metrics": metrics,
    }
    save(release / "control/config.json", cfg)
    cmd = ["docker", "run", "-d", "--name", a.name, "--network", "host",
           "--security-opt", "no-new-privileges", "--cap-drop", "ALL"]
    for cap in ["SETUID", "SETGID", "CHOWN", "FOWNER", "DAC_OVERRIDE", "KILL"]:
        cmd += ["--cap-add", cap]
    cmd += ["--cpus", "4", "--memory", "8g", "--tmpfs", "/tmp:exec,size=2g",
            "--tmpfs", "/workspace:exec", "--tmpfs", "/sel4-project:exec"]
    for env in ["PYTHONPATH=/vero-src", "VERO_SRC=/vero-src", "PYTHONDONTWRITEBYTECODE=1",
                "PATH=/workspace/verification/isabelle/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "http_proxy=http://127.0.0.1:17890", "https_proxy=http://127.0.0.1:17890",
                "no_proxy=localhost,127.0.0.1"]:
        cmd += ["-e", env]
    mounts = [
        ("/data/zljj/sel4-dev/verification/isabelle", "/workspace/verification/isabelle", True),
        (release / "input", "/semantic-base", True),
        (release / "vero-src", "/vero-src", True),
        (release / "benchmark", "/benchmark", True),
        (release / "control", "/control", True),
        ("/data/zljj/vero-a-unified/20260928-001/client", "/opt/vero", True),
        (a.auth.resolve(), "/run/vero-auth", True),
        (release / "sandbox", "/sandbox", False),
        (release / "results", "/results", False),
        (release / "agent-home", "/home/agent", False),
        (release / "public", "/public", False),
    ]
    for source, dest, ro in mounts:
        assert Path(source).exists(), source
        cmd += ["--mount", f"type=bind,src={source},dst={dest}" + (",readonly" if ro else "")]
    cmd += ["--entrypoint", "/bin/bash", image, "-c", "sleep infinity"]
    container = call(*cmd, capture_output=True, text=True).stdout.strip()
    save(release / "deployment.json", {"container": a.name, "container_id": container,
         "image_id": image, "model": "gpt-6-astra", "reasoning_effort": "low",
         "release": str(release), "target_count": 91, "engine": "vero.isabelle plan-run",
         "reference_proofs_transferred": False, "config": cfg})
    init = r'''set -eu
useradd -u 1001 -M -s /bin/bash agent
useradd -u 1002 -M -s /bin/bash checker
mkdir -p /home/agent/.isabelle/etc /home/agent/.codex /home/checker/.isabelle/etc /backend
for owner in /root /home/agent /home/checker; do
  if [ "$owner" != /root ]; then
    cp /root/.isabelle/etc/settings "$owner/.isabelle/etc/settings"
    ln -s /root/.isabelle/contrib "$owner/.isabelle/contrib"
    ln -s /root/.isabelle/heaps "$owner/.isabelle/heaps"
  fi
  cat >> "$owner/.isabelle/etc/settings" <<'SETTINGS'
ML_OPTIONS="-H 256 --maxheap 2048 --stackspace 64 --gcthreads 2"
ISABELLE_TOOL_JAVA_OPTIONS="-Djava.awt.headless=true -Xms128m -Xmx1g -Xss4m"
ISABELLE_BUILD_JAVA_OPTIONS="-Xms128m -Xmx1g -Xss4m"
SETTINGS
done
cp /run/vero-auth /home/agent/.codex/auth.json
chmod 600 /home/agent/.codex/auth.json
chown -hR 1001:1001 /home/agent /sandbox
chown -hR 1002:1002 /home/checker
chmod 700 /home/checker
chown 0:1002 /results
chmod 2750 /results
mkdir -p /run/vero-plan
chmod 755 /run/vero-plan
chmod 711 /root /root/.isabelle
chmod 700 /root/.isabelle/etc
ln -s /opt/vero/rg /usr/local/bin/rg
ln -s /vero-src/vero/isabelle/tool.py /backend/tool.py
'''
    call("docker", "exec", a.name, "bash", "-c", init)
    call("docker", "exec", a.name, "bash", "-c",
         "set -eu; isabelle build -b -j 1 -o threads=2 -o document=false -d /semantic-base "
         "Intervaltree_Definitions > /results/input-build.log 2>&1")
    export = r'''let
  val thy = Thy_Info.get_theory "Intervaltree_Definitions.Interval_Input";
  val ctxt = Proof_Context.init_global thy;
  fun fact (name, th) = XML.Elem (("fact", [("name", name)]),
    [XML.Text (Syntax.string_of_term ctxt (Thm.prop_of th))]);
  val xml = XML.Elem (("premises", []), map fact (Global_Theory.all_thms_of thy true));
in File.write (Path.explode "/public/premises.xml") (XML.string_of xml) end'''
    call("docker", "exec", a.name, "/workspace/verification/isabelle/bin/isabelle",
         "process", "-l", "Intervaltree_Definitions", "-d", "/semantic-base", "-e", export)
    call("docker", "exec", a.name, "python3", "-c",
         "import json, pathlib, xml.etree.ElementTree as E; "
         "p=pathlib.Path('/public/premises.jsonl'); "
         "rows=[{'canonical_name':x.attrib['name'],'exact_statement':x.text or ''} "
         "for x in E.parse('/public/premises.xml').getroot()]; "
         "p.write_text(''.join(json.dumps(x)+'\\n' for x in rows)); "
         "p.chmod(0o444); print('Legal premise records:',len(rows))")
    preflight = r'''
import json,sys
from pathlib import Path
from vero.isabelle.benchmark import read,render
from vero.isabelle.checker import Checker
bench=read('/benchmark/benchmark.json')
cfg=read('/control/config.json')
render(bench,'/tmp/intervaltree-preflight')
result=Checker(cfg['checker']).check('/tmp/intervaltree-preflight','Vero_All')
Path('/results/preflight.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='log'}))
if result['status']!='ok':
 print(result['log'])
 sys.exit(1)
'''
    call("docker", "exec", a.name, "python3", "-c", preflight)
    call("docker", "cp", a.name + ":/results/preflight.json", release / "preflight.json")
    print(json.dumps({"prepared": True, "container": a.name, "model_started": False}))


if __name__ == "__main__":
    main()
