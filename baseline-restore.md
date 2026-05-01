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

- Container: `sel4-public:baseline-clean` (see `docker images`)
- Isabelle 2024 with polyml-5.9.1
- `/root/.isabelle/etc/settings`:
  `ML_OPTIONS="-H 1000 --maxheap 16000 --stackspace 64"` (10G→16G bump)
- `isabelle build -b -v -j 1 -o threads=8 -d <l4v> <SESSION>`
- Strict session-level serialization (no `-j N>1`).

## Per-session wall times in the baseline (top sessions)

From `heaps/build_log.txt`, top by elapsed time:

| Session | elapsed | gc% | par_factor | theories |
|---|---|---|---|---|
| SimplExportAndRefine | 3779s | 24% | 3.7x | 8 (1 dominates) |
| CBaseRefine | 3478s | **64%** | 3.6x | 304 |
| CRefine | 1889s | 38% | 4.8x | 52 |
| Refine | 1292s | 21% | 3.3x | 48 |
| CKernel | 1013s | **60%** | 2.8x | 61 |
| AInvs | 780s | 24% | 3.3x | 119 |

Sessions where `gc%` ≥ 50% are the main acceleration candidates (the
`-H 1000` initial heap is too small relative to the working set, causing
excessive resize / full-GC cycles).
