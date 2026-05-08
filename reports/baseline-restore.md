# Restoring the baseline build state

The git repo tracks **source + scripts + per-theory timing data**
(`heaps/build_log.txt`) but **not** the binary heap files (~2.7 GB) nor the
`verification/` source trees (~900 MB). This doc shows how to recover both.

## 1. Restore Isabelle session heaps

The 29 session heaps live in two places:

| location | content | when to use |
|---|---|---|
| docker image `sel4-public:tuned` | original heaps baked into the image (built before this canonical-rebuild commit) | quickest path; functional state correct, *internal byte layout differs* from `heaps/build_log.txt` |
| docker image `sel4-public:baseline-clean` | same as above; only differs in `/root/.isabelle/etc/settings` (`-H 1000` vs `-H 8000`) | regression testing against the original `-H 1000 --maxheap 10000` config |
| host `heaps/<session>` after re-running canonical rebuild | match `heaps/build_log.txt` byte-for-byte | exact-match reproducibility |

For most experiment work the docker image's heaps are sufficient — they
verify the same proofs, only their internal Poly/ML allocation layout
differs. The functional verification state is identical.

```bash
docker run -d --name sel4-restore sel4-public:tuned sleep infinity
mkdir -p heaps
for sess in AInvs ASpec Access BaseRefine Bisim CBaseRefine CKernel CParser \
            CRefine CRefineSyscall CSpec DBaseRefine DPolicy DRefine DSpec \
            DSpecProofs HOL InfoFlow InfoFlowC InfoFlowCBase Pure Refine \
            RefineOrphanage SepDSpec SimplExport SimplExportAndRefine \
            Simpl-VCG UmmTypes Word_Lib; do
    docker cp "sel4-restore:/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/$sess" "heaps/$sess"
done
docker rm -f sel4-restore
```

Result: `heaps/` has 29 heap files (~2.7 GB) plus `build_log.txt`.

To get heaps that match `heaps/build_log.txt` byte-for-byte, you'd need
to re-run the canonical rebuild inside the container under the tuned
config (`-H 8000 --maxheap 16000`, `threads=8`, `-j 1`, clean dir, ~7h
on a 16-core host). The timing data in `build_log.txt` came from such a
run.

## 2. Restore `verification/` source trees

```bash
mkdir -p verification && cd verification
repo init -u https://github.com/seL4/verification-manifest.git
repo sync
(cd l4v       && git checkout seL4-13.0.0)
(cd seL4      && git checkout 13.0.0)
(cd isabelle  && git checkout Isabelle2024)
```

## 3. Two notable session quirks

- **`CRefineSyscall`** (`crefine/intermediate/Intermediate_C`) inherits
  from `CBaseRefine` + `CRefine`. Counted in the 29-heap baseline because
  it's a declared session in `proof/ROOT`, but **not on the SOSP'09
  Theorem 1/2/3 functional-correctness chain**. Lose it and Theorem 3
  still holds. No `theory_timings`/`command_timings` recoverable from its
  `.db` (per limitation noted in `reports/README.md`).

- **`AutoCorresCRefine`** (`crefine/autocorres-test`) is **broken in
  l4v 13.0 ARM** — `AutoCorresTest.thy` imports `Refine_C` from a path
  Isabelle resolves wrong on ARM. Other arches (X64/RISCV64/AARCH64)
  exclude it via `verification/l4v/run_tests`; our `compile.sh` follows
  suit. **No heap exists in either image** — that's expected, not a
  recovery gap.

## 4. Verifying the restore

After §1 + §2, inside the container:

```bash
cd /sel4-project/verification/l4v
export L4V_ARCH=ARM
isabelle build -n -d . CRefine InfoFlowCBase InfoFlowC
# expect: "Up-to-date" for all reachable sessions
```

Per-session canonical wall times: see [`heaps/build_log.txt`](../heaps/build_log.txt)
(SESSION OVERVIEW table) — generated under tuned config, total
~25,331s ≈ 7.0h.
