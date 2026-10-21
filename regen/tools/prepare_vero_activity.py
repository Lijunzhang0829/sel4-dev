"""Copy VERO source and add an explicit externally supervised budget mode.

No generation, rendering, retry, or evaluation algorithm is replaced. Fail closed
if upstream budget guards have changed; keep the original source untouched.
"""
import argparse
from pathlib import Path
import shutil
from regen.execution.proof_commits import durable,digest


def prepare(source, output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('use a fresh destination outside original VERO source')
    runner=source/'vero/isabelle/runner.py'
    text=runner.read_text(); original=text
    replacements=[
        ('        and not continuous\n    ):\n        raise ValueError("unbounded total time requires finite checkpoint chunks")',
         '        and not continuous\n        and not cfg.get("external_activity_supervisor", False)\n    ):\n        raise ValueError("unbounded total time requires finite checkpoint chunks")'),
        ('            None if continuous else min(cfg["agent"]["timeout_seconds"], remaining)',
         '            None if continuous or cfg.get("external_activity_supervisor", False) else min(cfg["agent"]["timeout_seconds"], remaining)'),
    ]
    for old,new in replacements:
        if text.count(old)!=1:raise ValueError('unsupported VERO runner budget guard')
        text=text.replace(old,new,1)
    shutil.copytree(source,output,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (output/'vero/isabelle/runner.py').write_text(text)
    durable(output/'ACTIVITY-PATCH.json',{'policy':'external-activity-supervisor-v1',
        'source':str(source),'original_sha256':digest(original),'patched_sha256':digest(text),
        'changes':'allow external inactivity supervisor; no fixed agent allowance',
        'original_method_logic_preserved':True})
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(prepare(args.source,args.output))
