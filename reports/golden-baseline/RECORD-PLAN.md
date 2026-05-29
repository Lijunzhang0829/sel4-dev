# Golden Baseline 2026-05-29 — Record Plan

> Pre-build commit. Documents what will be recorded, where, and when, so
> nothing depends on the working tree surviving without being captured.

## Build configuration

| key | value |
|---|---|
| `build_run_id` | `golden-20260529-30gb-j2` |
| host RAM | 29 GB (recently upgraded from 23 GB) |
| `-j` | **2** (full build, both invocation phases) |
| `--maxheap` | **12000** (12 GB per process; 2 × 14 GB RSS ≈ 28 GB fits in 30 GB) |
| `--stackspace` | 64 |
| `SKIP_DUPLICATED_PROOFS` | `1` |
| `ISABELLE_BUILD_OPTIONS` | `document=false` |
| `L4V_ARCH` | ARM |
| `threads` per session | auto (Isabelle default ≈ host cores) |
| `l4v` commit | `00d9073f` (clean working tree) |
| Isabelle | `verification/isabelle` subtree (no git SHA available) |

## Files recorded (under `reports/golden-baseline/`)

| file | when written | content |
|---|---|---|
| `RECORD-PLAN.md` | this commit (pre-build) | this document |
| `env.json` | pre-build (one-shot) | full `/root/.isabelle/etc/settings`, host specs, l4v commit |
| `build-logs/round1.log` | streamed during build | complete `isabelle build` tee output |
| `walls.json` | incremental, re-generated after each round | per-session `wall_s` / `cpu_s` / `factor` / `round` |
| `heap-sizes.txt` | incremental, re-generated after each round | per-heap `size_bytes` + `sha256` + name |
| `lemma-inventory.db` | post-build (one-shot) | SQLite snapshot of every lemma in source (~20 MB) |
| `lemma-inventory-summary.md` | post-build | human-readable lemma stats |
| `BASELINE-2026-05-29.md` | post-build | final writeup with per-session walls table, hardware comparison, limitations |

## Recording cadence (commit points)

```
T-0    pre-build
       commit: scaffold (extract.py, RECORD-PLAN.md, env.json)
       → push origin spec-strengthen

T-1    build starts: isabelle build -b -j 2 -v ...

T-N    each session finishes
       Monitor catches "Finished X" event
       (no commit here — too noisy; data is in build-logs/round1.log)

T-K    build finishes (success or partial)
       run: python3 tools/golden_baseline/extract.py
       commit: walls.json + heap-sizes.txt updates
       → push origin spec-strengthen

T-L    final writeup
       run: lemma_inventory build.py
       write: BASELINE-2026-05-29.md
       commit: lemma-inventory.db, summary, writeup
       → push origin spec-strengthen
```

## Safety mechanisms (vs the 2026-05-28 data-loss incident)

1. **Build logs live at `reports/golden-baseline/build-logs/`** (host bind-mount path), NOT `/tmp/` inside container. Survives container restart, survives `docker compose down`.
2. **All artifacts are git-tracked from the moment they're created.** No data sits in working tree uncommitted for more than one round.
3. **`extract.py` is idempotent and partial-friendly.** Mid-build, re-running it captures whatever data is currently available; nothing is destroyed.
4. **`heaps/` directory is NEVER deleted by automation.** Manual `rm -rf` is the only way heaps disappear, and only with explicit user direction documenting why.
5. **`verification/l4v` submodule HEAD is captured in `env.json`.** Any drift after build start is recorded.

## Target session list

Full ARM proof tree. Isabelle build will figure out dependency closure
from these as targets:

```
Refine CRefine CRefineSyscall DRefine DPolicy DSpecProofs
InfoFlowC RefineOrphanage Bisim SimplExportAndRefine SepDSpec
AutoCorresSEL4 Access InfoFlow
```

Plus all transitive dependencies (Pure, HOL, Word_Lib, ASpec, CParser,
CKernel, CSpec, SimplExport, AInvs, BaseRefine, CBaseRefine,
InfoFlowCBase, DSpec, DBaseRefine, Simpl-VCG).

Expected unique heaps: **29**.

## Expected wall budget

Based on previous runs:
- `-j 1` total (sequential): ~6-8 h
- `-j 2 --maxheap 12000` projected: ~4-5 h (CRefine + Refine + SimplExportAndRefine can overlap with smaller stuff)

Hard ceiling: 12 h (then assume something hung).

## Known caveats this baseline carries

- **Hardware-bound**: 16 cores / 30 GB RAM / Docker. Numbers reflect this specific machine; absolute wall times are not portable.
- **`-j 2` overcommits cores**: with 16 cores and threads=auto (~16 threads/session), `-j 2 × 16` = 32 thread oversubscription. This matches upstream CI's `-j 2` pattern; benchmarking the alternative (`-j 1` with full thread parallelism) is deferred.
- **`--maxheap 12000` is not stock Isabelle**: maintainer email says don't constrain heap. We do it here because the alternative on this hardware is OOM under `-j 2`. Pure stock-settings build would require `-j 1`.
- **Source commit `00d9073f`** = `verification/l4v origin/master` head as of 2026-05-29. This baseline expires when upstream advances; refresh by re-running this entire flow.
