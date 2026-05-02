# How to restore the baseline build state

The git repo tracks source + scripts + per-theory timing data
(`heaps/build_log.txt`, ~127 KB) but **not** the binary heap files (~2.7 GB).
Those live in the docker image `sel4-public:baseline-clean`.

If you've just cloned this repo and want to run build-acceleration
experiments against the baseline:

## 1. Restore the heap files from the docker image

```bash
# Start a container from the baseline image
docker run -d --name sel4-restore sel4-public:baseline-clean sleep infinity

# Copy heaps to host (matches what was in heaps/ at the baseline commit)
mkdir -p heaps
for sess in AInvs ASpec Access BaseRefine Bisim CBaseRefine CKernel CParser \
            CRefine CRefineSyscall CSpec DBaseRefine DPolicy DRefine DSpec \
            DSpecProofs HOL InfoFlow InfoFlowC InfoFlowCBase Pure Refine \
            RefineOrphanage SepDSpec SimplExport SimplExportAndRefine \
            Simpl-VCG UmmTypes Word_Lib; do
    docker cp sel4-restore:/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/$sess heaps/$sess
done

docker rm -f sel4-restore
```

After this, `heaps/` should have **29 heap files + build_log.txt**.

## 2. Or just run experiments inside the container

The container already has all heaps pre-loaded. Skip the cp and:

```bash
docker run -it --name sel4-experiments sel4-public:baseline-clean bash
# inside container:
cd /sel4-project/verification/l4v
export L4V_ARCH=ARM
export PATH=/sel4-project/verification/isabelle/bin:$PATH
isabelle build -n -d . CRefine InfoFlowCBase InfoFlowC   # should report up-to-date
```

## 3. Restore `verification/` (the l4v / seL4 / isabelle source trees)

These are excluded from git (they're separate ~900 MB checkouts). Re-fetch via:

```bash
mkdir -p verification && cd verification
repo init -u https://git@github.com/seL4/verification-manifest.git
repo sync
# Then per the README:
(cd l4v && git checkout seL4-13.0.0)
(cd seL4 && git checkout 13.0.0)
(cd isabelle && git checkout Isabelle2024)
```

## Build configuration that produced the baseline

- Container: `sel4-public:baseline-clean`
- Isabelle 2024 with polyml-5.9.1
- `/root/.isabelle/etc/settings`:
  `ML_OPTIONS="-H 1000 --maxheap 16000 --stackspace 64"` (10G→16G bump
  applied during the missing-sessions rebuild; the original was maxheap
  10000)
- `isabelle build -b -v -j 1 -o threads=8 -d <l4v> <SESSION>`
- Strict session-level serialization (no `-j N>1`).

## Two docker image tags — which to use for what

| tag | settings | when to use |
|---|---|---|
| `sel4-public:baseline-clean` | `-H 1000 --maxheap 16000` | reproducing the baseline / regression testing |
| `sel4-public:tuned` | `-H 8000 --maxheap 16000` | new builds — empirically -17% wall on CBaseRefine vs the original `-H 1000 --maxheap 10000` config (see `experiments/exp_A_results.md`) |

Both images contain the same 29 heap files. Settings is the only delta.

## Per-session walls in the original baseline (`build-logs/clean.log` inside the image)

These are the authoritative wall times — the `Timing X (...)` lines from
`isabelle build -v`. Note they are **not** the per-theory `TOTAL` rows
in `heaps/build_log.txt`, which sum per-theory elapsed only.

| session | wall | gc% | factor | threads |
|---|---:|---:|---:|---:|
| **CBaseRefine** | **5321s = 89:00** | 79.6% | 3.38 | 4 |
| **CKernel**     | 1300s = 21:40    | 55.7% | 2.98 | 4 |
| AInvs           | 1403s = 23:23    | 24.3% | 3.07 | 4 |
| Access          | 467s             | 18.8% | 3.28 | 4 |
| ASpec           | 177s             | 18.5% | 1.80 | 4 |
| BaseRefine      | 207s             | 13.7% | 1.50 | 4 |

Sessions where `gc%` ≥ 50% are the main acceleration candidates. The
`tuned` image's `-H 8000` setting addresses this: more initial ML heap
means fewer mark-compact cycles during the early growth phase.
