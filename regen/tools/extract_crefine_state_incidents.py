"""Deterministically extract state incident fixtures from retained local reports.

This reads operator reports and an existing one-contract unit-test fixture. It
never reads machine B, authentication material, or a provider transcript, and
never modifies production experiments. Use --check to verify reproducibility.
"""
import argparse
import hashlib
import json
from pathlib import Path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def extract(repo, output, *, check=False):
    generated = {}
    def read(relative):
        return json.loads((repo / relative).read_text())
    def origin(relative, selection='$'):
        return {'source_path': relative, 'source_sha256': sha((repo / relative).read_bytes()),
                'selection': selection}
    def save(name, data, sources):
        relative = 'evidence/' + name
        raw = data if isinstance(data, bytes) else encoded(data)
        generated[relative] = raw
        return {'path': relative, 'sha256': sha(raw), **sources[0], 'sources': sources}
    def replay(id, handler, kind, parameters, oracle):
        return dict(id=id, handler='state.'+handler, kind=kind, parameters=parameters, oracle=oracle)
    def case(id, title, category, status, steps, evidence, replays, existing_tests, limitations):
        generated['cases/'+id+'.json'] = encoded(dict(schema_version=1,id=id,title=title,
            component='state',category=category,status=status,source_steps=steps,
            evidence=evidence,replays=replays,existing_tests=existing_tests,limitations=limitations))

    prefix_dir = 'reports/regen/vero-green-regression-audit-20261025-001'
    old = read(prefix_dir+'/evidence.json')
    events = [row for row in old['events'] if row['kind']=='contract_green_invalidated']
    assert [row['sequence'] for row in events] == [12396,12397,12469]
    files = []
    for name in ('before_first','after_first','before_second','after_second'):
        source = prefix_dir+'/'+name+'.thy'
        raw = (repo/source).read_bytes()
        assert raw.decode() == old['checks'][name]['source']
        entry = save('state-prefix-'+name+'.thy', raw, [origin(source)])
        entry['original_native_check_path'] = old['checks'][name]['path']
        files.append(entry)
    audit_path = prefix_dir+'/audit-summary.json'
    prefix = save('state-prefix-revocations.json', {
        'run':old['run'],
        'transitions':[
            {'before':files[0]['path'],'after':files[1]['path'],'revocations':events[:2]},
            {'before':files[2]['path'],'after':files[3]['path'],'revocations':events[2:]}],
        'native_dependency_audit':read(audit_path)},
        [origin(prefix_dir+'/evidence.json','$.events[kind=contract_green_invalidated] and $.run'),origin(audit_path)])
    selected_tests = ['regen.tests.current.test_vero_prefix_revalidation.VeroPrefixRevalidationTests.'+name for name in (
        'test_unused_helper_removed_and_later_red_keeps_green',
        'test_used_helper_removed_really_invalidates_proof',
        'test_historical_invalidated_version_recovers_only_after_native_success',
        'test_timeout_clean_markers_cannot_renew_certificate',
        'test_zero_exit_without_audit_cannot_renew_certificate',
        'test_changed_external_context_failure_invalidates_old_certificate',
        'test_one_failed_import_is_not_rechecked_for_every_contract',
        'test_partial_revalidation_cannot_authorize_a_full_theory_seal')]
    case('state-prefix-revocation','无关辅助声明变更导致已证明 contracts 被撤销','acceptance_receipts','fixed_with_native_validation',
        [{'run':Path(old['run']).name,'events':[12396,12397,12469],'targets':['c0022','c0057','c0016'],
          'baseline_acceptance_event':7607}],files+[prefix],
        [replay('recorded-prefix-changes','prefix_step','extracted_step',{'evidence':prefix['path']},
            'Three real revocations reproduce unchanged proof bodies and changed pre-proof context; native dependency audit found no changed helper cited.'),
         replay('independent-revalidation-guards','unit_regressions','synthetic_regression',{'tests':selected_tests},
            'Independent successful native evidence preserves unaffected acceptance; changed dependencies, timeout, missing audit and incomplete sealing never grant GREEN.')],selected_tests,
        ['Four theory files and three ledger events are exact historical extracts; the whole event chain is not included.',
         'Native dependency results are historical observations; offline replay does not rerun Isabelle.',
         'Selected unit tests use stub native outcomes and do not establish contract proof success.'])

    framework_path='reports/regen/vero-budget-credit-20261026-001/credit-receipt.json'
    branch_path='reports/regen/vero-branch-budget-credit-20261026-001/credit-receipt.json'
    credits = {'framework':read(framework_path),'branch':read(branch_path)}
    budget = save('state-budget-credits.json',credits,[origin(framework_path),origin(branch_path)])
    expected = {'overlap':906.1,'union':10410.99132930463,'additional_credit':9048.547329304629}
    steps=[{'run':credits['framework']['run_id'],'event':credits['framework']['sequence'],'boundary':credits['framework']['data']['boundary']},
           {'run':credits['branch']['run_id'],'event':credits['branch']['sequence'],'boundary':credits['branch']['data']['boundary']}]
    case('state-budget-union','回滚分支与框架重验预算冲正按区间去重','budget_accounting','audited_correction',steps,[budget],
        [replay('credit-union','budget_union','extracted_step',{'evidence':budget['path'],'expected':expected},
                'Overlap 906.1s is credited once; union 10410.99132930463s; second incremental credit 9048.547329304629s. Planning, tokens and proof history remain unchanged.')],
        ['reports/regen/vero-branch-budget-credit-20261026-001/test_branch_credit.py'],
        ['Replays the recorded arithmetic oracle only; it does not alter a live budget or rewrite original events.'])
    case('state-crash-clock','恢复时保留累计有效时间和未观测 crash tail','budget_recovery','guarded',steps,[budget],
        [replay('closed-gate-and-unknown-tail','restore_clock','synthetic_regression',{'evidence':budget['path']},
                'Use exact corrected clock state in a synthetic minimal ledger: 86400 offline seconds charge zero; prior event remains unchanged; unknown crash tail remains unknown.')],
        ['regen.tests.current.test_vero_effective_time_budget'],
        ['Clock state is extracted; its minimal ledger wrapper and one-day offline interval are synthetic.','This does not reconstruct the missing crash interval or claim that it cost zero online time.'])

    archive_path='reports/regen/vero-rpc-repair-20261026-001/archive-receipts.jsonl'
    receipts=[json.loads(line) for line in (repo/archive_path).read_text().splitlines() if line]
    assert len(receipts)==31
    pause_path='reports/regen/vero-rpc-repair-20261026-001/pause-audit.json'
    pause=read(pause_path)
    summary={'observed_at':pause['observed_at'],'service_status':pause['service_status'],
        'last_vero_event':pause['ledger']['vero']['last_event'],
        'last_vero_clock':pause['ledger']['vero']['last_clock'],
        'findings':pause['findings'], 'disk':pause['disk']['disk'],
        'temporary_files':pause['disk']['temporary_files']}
    storage=save('state-storage-enospc.json',{'archive_receipts':receipts,'pause_audit':summary},
        [origin(archive_path,'all 31 JSONL records'),origin(pause_path,'observed_at/service_status/ledger.vero.last_event/ledger.vero.last_clock/findings/disk.disk/disk.temporary_files')])
    case('state-storage-enospc','诊断日志放大耗尽磁盘并中断持久化','storage','mitigated_capacity_risk_remains',
        [{'run':Path(old['run']).name,'last_event':27730,'last_budget_checkpoint':27725,'iteration':'iter-004','item':'item_2196','request':'1880d4c341604e87918e1620cb19e252'},
         {'operation':'lossless response archive','verified_archives':31}], [storage],
        [replay('enospc-atomic-publish','storage_atomicity','synthetic_regression',{'evidence':storage['path']},
                'Injected fsync ENOSPC must not publish a partial response or change the previous checkpoint. Recorded 31 verified gzip receipts release 21773021413 bytes.')],
        ['regen.tests.current.test_vero_tool_protocol'],
        ['ENOSPC is injected in a temporary directory; the test never fills a disk.','Archive receipts are real, but archived multi-gigabyte payloads are not bundled or decompressed during this replay.','Archival mitigated capacity pressure; it did not bound all original native log growth.'])

    privacy_path='reports/regen/crefine-owner-blind-machine-b-20261025-001/privacy-audit.json'
    original=read(privacy_path)
    allowed=('prepared_at','status','contract_count','original_theory_count','machine_a_anonymous_sha256',
        'anonymous_sha256','matches_machine_a','semantics_context_attributes_preserved','public_views_scanned',
        'original_owner_and_id_disclosures','codex_owner_fields','vero_placeholder_modules',
        'vero_contract_ids_follow_anonymous_semantic_order','original_groups_and_imports_removed',
        'private_map_model_visible','old_sessions_imported','checker_context_id','bench_sha256')
    from regen.tests.current.test_iterative_repository_planner import IterativeRepositoryPlannerTest
    fixture=IterativeRepositoryPlannerTest()
    try:
        fixture.setUp()
        synthetic=json.loads(fixture.input.read_text())
        # Test preparation writes temporary roots; normalize only this synthetic fixture.
        serialized=json.dumps(synthetic,ensure_ascii=False).replace(str(fixture.root),'/synthetic-owner-fixture')
        synthetic=json.loads(serialized)
    finally:
        fixture.doCleanups()
    privacy=save('state-owner-visibility.json',{'historical_audit':{k:original[k] for k in allowed},
        'synthetic_source':synthetic,'synthetic_source_note':'Existing one-contract fixture; temporary root normalized to /synthetic-owner-fixture. No production contract map read.'},
        [origin(privacy_path,'allowlisted public audit fields; authentication/private filesystem paths excluded'),
         origin('regen/tests/current/test_iterative_repository_planner.py','IterativeRepositoryPlannerTest.setUp -> repository-planning-input.json; synthetic'),
         origin('regen/tests/current/test_planning_input.py','PlanningInputTest.setUp synthetic contract and parent premise')])
    case('state-owner-visibility','隐藏原始 contract 的 theory 归属且保留语义输入','input_visibility','fixed_with_preparation_audit',
        [{'preparation':'crefine-owner-blind-machine-b-20261025-001','prepared_at':original['prepared_at'],'contract_count':309}], [privacy],
        [replay('owner-reassignment-invariance','owner_visibility','synthetic_regression',{'evidence':privacy['path']},
                'Changing only original owner theory/contract ID leaves anonymous payload identical, preserves exact statement, and discloses neither owner nor original ID. Historical audit covers 309 contracts.')],
        ['regen.tests.current.test_owner_blind_dataset'],
        ['One-contract synthetic runtime fixture does not replace auditing the full 309-contract production view.','Private contract-map paths and authentication directory fields are deliberately excluded.'])

    reuse_path='reports/regen/dual-step-resume-validation-20261027-001/vero-resume-reuse-audit.json'
    reuse=save('state-artifact-reuse.json',read(reuse_path),[origin(reuse_path)])
    expected={'previous_sha256':'ca2237edb7b22ae04d59d4970afaa3b1ef7999c2687ba2ef2af0ff03f9190e4f',
        'current_sha256':'a5b7c6e4607c94258b4f1602d85dce877a0290d55eb41a2f97f2f05c2c95d917'}
    case('state-artifact-reuse','恢复时 artifact 已变化不能借用上一 iteration 的报告','recovery_cache','expected_guard',
        [{'run':Path(old['run']).name,'previous_iteration':'iter-004','recovering_iteration':'iter-005','changed_slot':'c0236:prove'}],[reuse],
        [replay('cross-iteration-report-rejected','artifact_reuse','extracted_step',{'evidence':reuse['path'],'expected':expected},
                'Changed c0236 artifact SHA differs from iter004; missing same-iteration report requires normal evaluation. Previous 14 proved specs do not certify a changed candidate.')],
        ['regen.tests.current.test_vero_prefix_revalidation'],
        ['Historical report digest comparison is replayed; no full VERO runner or Isabelle recovery is invoked.','The changed unfinished proof body was intentionally not included in the original audit.'])
    for name,raw in generated.items():
        destination=output/name
        if check:
            if not destination.is_file() or destination.read_bytes()!=raw:
                raise ValueError('fixture differs: '+str(destination))
        else:
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(raw)
    return {'generated_files':len(generated),'cases':6,'bytes':sum(map(len,generated.values())),
            'mode':'verify' if check else 'extract'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args(argv)
    output=args.output or args.repo_root/'regen/tests/fixtures/crefine_incidents_v1'
    print(json.dumps(extract(args.repo_root.resolve(),output.resolve(),check=args.check),sort_keys=True))


if __name__=='__main__':
    main()
