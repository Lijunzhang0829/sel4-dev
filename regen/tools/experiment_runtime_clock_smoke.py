"""Real native timeout/watchdog/recovery smoke for the sole current clock.

Synthetic HOL fixtures and scripted provenance, zero model calls. Only the
native worker delay and host response deadline are injected, never verdicts.
"""
import argparse
import json
from pathlib import Path
from regen.infrastructure.experiment_runtime_budget import ExperimentRuntimeBudget
from regen.tools.normal_native_outage_smoke import run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.dataset,args.output,seal=False,clock_factory=ExperimentRuntimeBudget)
    print(json.dumps({'pass':result['pass'],'wall_seconds':result['wall_seconds'],
                      'cases':len(result['cases']),'model_calls':0}))


if __name__=='__main__':main()
