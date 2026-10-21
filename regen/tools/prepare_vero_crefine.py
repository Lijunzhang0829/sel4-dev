"""Prepare VERO's immutable view of the independent frozen CRefine contracts."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from regen.execution.proof_commits import durable
from regen.preparation.checker_context import load_context


def prepare(*, frozen_contracts, dataset, vero_source, output):
    frozen_contracts=Path(frozen_contracts).resolve()
    dataset=Path(dataset).resolve()
    vero_source=Path(vero_source).resolve()
    output=Path(output).resolve()
    context=load_context(dataset)
    if (context['target_session']!='CRefine' or
            context['base_import']!='CRefine_Semantic_Base.Semantic_Root'):
        raise ValueError('not the independent CRefine semantic base')
    raw=frozen_contracts.read_bytes()
    source=json.loads(raw)
    records=source['contracts']
    if len(records)!=309 or len({row['contract_id'] for row in records})!=309:
        raise ValueError('CRefine frozen inventory must contain exactly 309 unique contracts')
    required=('contract_id','exact_statement','contract_owned_context','owner_theory','name')
    for row in records:
        if any(key not in row for key in required):
            raise ValueError('incomplete frozen CRefine contract')
        if not row['owner_theory'].startswith('CRefine.'):
            raise ValueError('contract outside CRefine target session')
    if output.exists():
        raise ValueError('VERO benchmark output already exists')
    output.mkdir(parents=True)
    if str(vero_source) not in sys.path:
        sys.path.insert(0,str(vero_source))
    from vero.isabelle.benchmark import prepare as vero_prepare
    contracts_jsonl=output/'contracts.jsonl'
    contracts_jsonl.write_text(''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n'
        for row in records))
    provenance={'benchmark':'crefine-independent-arm-20261017-001',
        'frozen_contracts_sha256':hashlib.sha256(raw).hexdigest(),
        'checker_context_id':context['checker_context_id'],
        'upstream_policy':'stock Refine parent; no generated Refine result'}
    benchmark=vero_prepare(contracts_jsonl,output,
        base_import=context['base_import'],provenance=provenance)
    for source_row,contract in zip(records,benchmark['contracts']):
        if (contract['source_id']!=source_row['contract_id'] or
                contract['statement']!=source_row['exact_statement'] or
                contract['context']!=source_row['contract_owned_context']):
            raise ValueError('VERO benchmark changed a frozen contract')
    durable(output/'preparation-receipt.json',{
        'contract_count':309,'benchmark_id':benchmark['benchmark_id'],
        'frozen_contracts_sha256':provenance['frozen_contracts_sha256'],
        'checker_context_id':context['checker_context_id']})
    return benchmark


def main():
    parser=argparse.ArgumentParser()
    for name in ('frozen-contracts','dataset','vero-source','output'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    benchmark=prepare(frozen_contracts=args.frozen_contracts,
        dataset=args.dataset,vero_source=args.vero_source,output=args.output)
    print(json.dumps({'benchmark_id':benchmark['benchmark_id'],
        'contracts':len(benchmark['contracts'])}))


if __name__=='__main__':main()
