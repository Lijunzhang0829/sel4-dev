#!/bin/bash
# tools/rebuild_canonical.sh
# =============================================================================
# Canonical TUNED-config rebuild of all 29 l4v sessions for ARM.
# =============================================================================
#
# WHAT THIS SCRIPT DOES
# ---------------------
# Drives a clean, from-scratch rebuild of every session in proof/ROOT (ARM
# arch, Theorem-3 chain plus side branches), under one fixed configuration.
# The point is to produce a **single, internally consistent timing record**
# that downstream analysis (critical-path, slow-command extraction, session
# DAG weighting) can rely on. Mixing timings from different ML_OPTIONS or
# different thread counts silently corrupts every conclusion you'd draw —
# so this script enforces one config and records its fingerprint inline.
#
# It runs on the HOST and drives an already-running container named
# `sel4-l4v` (created from image `sel4-public:tuned`) via `docker exec`.
# Per-session invocations are serialized; intra-session worker parallelism
# is left at threads=8.
#
# CANONICAL CONFIGURATION (locked, do not edit casually)
# ------------------------------------------------------
#   image:          sel4-public:tuned
#   ML_OPTIONS:     -H 8000 --maxheap 16000 --stackspace 64   (image-baked)
#   threads:        8                                          (intra-session)
#   parallelism:    -j 1                                       (strict serial)
#   drop_caches:    NOT performed — small-session wall noise ±3-5%
#   l4v:            tags/seL4-13.0.0   (ARM)
#   isabelle:       Isabelle2024 + Poly/ML 5.9.1 (32-on-64)
#
# PREREQUISITES
# -------------
#   * docker container `sel4-l4v` is running (built from sel4-public:tuned)
#   * verification/l4v is checked out at seL4-13.0.0, mounted at
#     /sel4-project/verification/l4v inside the container
#   * host has ~30 GB free for heaps + raw logs + db archive
#   * runtime: ~7h on a 16-core / 23 GB host
#
# ENVIRONMENT VARIABLES
# ---------------------
#   RESUME=1  (default)  skip a session if its heap already exists in the
#                        container — useful for restart after partial failure
#   WIPE=1               wipe $HEAP_DIR_CONT first; force full rebuild and
#                        rewrite the log header
#
# OUTPUTS (host paths, all under heaps/)
# --------------------------------------
#   heaps/build_log.tuned.txt        canonical session-level Timing lines + header.
#                                    Feed to tools/assemble_build_log.py to merge in
#                                    per-theory data and produce the final
#                                    heaps/build_log.txt.
#   heaps/rebuild-raw/<sess>.log     raw `isabelle build -v` stdout per session
#                                    (for debugging failures or re-extracting timings)
#   heaps/db-archive/<sess>.db       session .db file containing theory_timings
#                                    and command_timings (zstandard-compressed
#                                    sqlite blobs); needed by assemble_build_log.py
#                                    and slow-command analyzers.
#   heaps/rebuild-canonical.STATUS   one-line-per-event progress log for
#                                    `tail -f` while the rebuild runs
#
# FAILURE BEHAVIOUR
# -----------------
# On any session build failure the script appends a "BUILD FAILED" marker to
# the canonical log and exits with that session's return code. Re-run with
# RESUME=1 (default) after fixing — already-built sessions are skipped.
#
# AFTER A SUCCESSFUL RUN
# ----------------------
#   1. python3 tools/assemble_build_log.py
#        merges heaps/build_log.tuned.txt + heaps/db-archive/*.db into the
#        canonical heaps/build_log.txt with full per-theory timings.
#   2. mv heaps/build_log.tuned.txt heaps/build_log.tuned.archived.txt
#        (or delete) — the merged build_log.txt is now the source of truth.
#
# A NOTE ON UmmTypes
# ------------------
# UmmTypes is a phantom session: it has no persistent ROOT entry and is
# rebuilt by spec/cspec/mk_umm_types.py instead of `isabelle build`. We
# special-case it below; no .db is archived for it (no theory_timings).

set -u

CONTAINER=sel4-l4v
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HEAP_DIR_HOST="$REPO_ROOT/heaps"
HEAP_DIR_CONT=/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux
L4V_DIR_CONT=/sel4-project/verification/l4v

OUT_LOG="$HEAP_DIR_HOST/build_log.tuned.txt"
RAW_DIR="$HEAP_DIR_HOST/rebuild-raw"
DB_DIR="$HEAP_DIR_HOST/db-archive"
STATUS="$HEAP_DIR_HOST/rebuild-canonical.STATUS"

# Topological session order. UmmTypes is a phantom session produced by
# mk_umm_types.py — handled specially below (no persistent ROOT).
ORDERED=(
    Pure HOL Word_Lib Simpl-VCG CParser UmmTypes
    ASpec DSpec AInvs Access BaseRefine Refine
    RefineOrphanage DBaseRefine DRefine DPolicy SepDSpec DSpecProofs
    InfoFlow Bisim CKernel CSpec
    CBaseRefine CRefine CRefineSyscall
    SimplExport SimplExportAndRefine
    InfoFlowCBase InfoFlowC
)

# RESUME=1 (default): skip sessions whose heap already exists.
# WIPE=1: nuke heaps first; full from-scratch rebuild.
WIPE=${WIPE:-0}
RESUME=${RESUME:-1}

ts()   { date -Iseconds; }
note() { echo "[$(ts)] $*" | tee -a "$STATUS"; }

# Pre-flight
docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}\$" \
    || { echo "ERROR: container $CONTAINER not running" >&2; exit 2; }

mkdir -p "$RAW_DIR" "$DB_DIR"
: > "$STATUS"

L4V_HEAD=$(cd "$REPO_ROOT/verification/l4v" && git rev-parse HEAD)
L4V_TAG=$(cd "$REPO_ROOT/verification/l4v" && git describe --all HEAD 2>/dev/null || echo "unknown")
IMAGE_ID=$(docker inspect "$CONTAINER" --format '{{.Image}}' | sed 's|^sha256:||' | cut -c1-12)
HOST_CPU=$(nproc)
HOST_RAM=$(awk '/MemTotal/{printf "%.0f", $2/1024/1024}' /proc/meminfo)

# Header (only if WIPE or no existing log)
if [ "$WIPE" = "1" ] || [ ! -s "$OUT_LOG" ]; then
{
  echo "# seL4 / l4v build timings — canonical TUNED rebuild"
  echo "# Generated: $(ts)"
  echo "# Environment:"
  echo "#   container:    $CONTAINER  (image sel4-public:tuned, id $IMAGE_ID)"
  echo "#   l4v HEAD:     $L4V_HEAD  ($L4V_TAG)"
  echo "#   isabelle:     Isabelle2024 + Poly/ML 5.9.1 (32-on-64)"
  echo "#   ML_OPTIONS:   -H 8000 --maxheap 16000 --stackspace 64  (image-baked, verified via isabelle getenv)"
  echo "#   threads:      8 intra-session"
  echo "#   parallelism:  -j 1 strict serial; per-session docker exec invocation"
  echo "#   drop_caches:  NOT performed (sudo NOPASSWD not configured) — small-session wall noise ±3-5%"
  echo "#   host:         ${HOST_CPU} cores, ${HOST_RAM} GB RAM"
  echo "#   heaps:        clean rebuild from empty $HEAP_DIR_CONT"
  echo "# Sessions: ${#ORDERED[@]} sessions, topological order"
  echo
} > "$OUT_LOG"
else
  note "RESUME mode: appending to existing $OUT_LOG"
fi

# Step 1: wipe container heap dir if requested
if [ "$WIPE" = "1" ]; then
    note "wipe $HEAP_DIR_CONT in $CONTAINER"
    docker exec "$CONTAINER" bash -c "rm -rf $HEAP_DIR_CONT && mkdir -p $HEAP_DIR_CONT" \
        || { note "FATAL wipe failed"; exit 3; }
fi

# Step 2: per-session loop
START_EPOCH=$(date +%s)
for sess in "${ORDERED[@]}"; do
    raw="$RAW_DIR/${sess}.log"

    # RESUME: skip if heap already exists in container.
    if [ "$RESUME" = "1" ] && docker exec "$CONTAINER" test -f "$HEAP_DIR_CONT/$sess" 2>/dev/null; then
        note "SKIP  $sess (heap already exists)"
        continue
    fi

    note "START $sess"
    sess_start=$(date +%s)

    if [ "$sess" = "UmmTypes" ]; then
        # Phantom session: rebuild via mk_umm_types.py.
        # Script demands ISABELLE_TOOL + ISABELLE_PROCESS env vars (only checks presence).
        # isabelle-process binary is gone in Isabelle 2024 — placeholder path is fine.
        docker exec -w "$L4V_DIR_CONT" "$CONTAINER" bash -c '
            export ISABELLE_TOOL=$(which isabelle)
            export ISABELLE_PROCESS=$(dirname "$ISABELLE_TOOL")/isabelle-process
            python3 spec/cspec/mk_umm_types.py \
                --root '"$L4V_DIR_CONT"' \
                spec/cspec/c/build/ARM/kernel_all.c_pp \
                spec/cspec/c/build/ARM/umm_types.txt
        ' > "$raw" 2>&1
    else
        docker exec -w "$L4V_DIR_CONT" "$CONTAINER" \
            isabelle build -b -v -j 1 -o threads=8 -d . "$sess" \
            > "$raw" 2>&1
    fi
    rc=$?
    sess_end=$(date +%s)
    sess_wall=$((sess_end - sess_start))

    if [ $rc -ne 0 ]; then
        note "FAIL $sess rc=$rc wall=${sess_wall}s — see $raw"
        echo "# BUILD FAILED at session $sess (rc=$rc) after ${sess_wall}s wall" >> "$OUT_LOG"
        echo "# Raw log: $raw" >> "$OUT_LOG"
        exit $rc
    fi

    # Canonical session-level Timing line from isabelle build -v output.
    # Format: "Timing <SESSION> (<N> threads, <X> elapsed time, <Y> cpu time, <Z> GC time, factor <F>)"
    timing=$(grep "^Timing $sess " "$raw" | head -1)
    if [ -n "$timing" ]; then
        echo "$timing" >> "$OUT_LOG"
    else
        # Pure/HOL/UmmTypes etc. may not emit Timing line; fall back to wall-clock.
        echo "Timing $sess (no canonical Timing line; wall-clock=${sess_wall}s)" >> "$OUT_LOG"
    fi

    # Archive .db
    docker cp "$CONTAINER:$HEAP_DIR_CONT/log/${sess}.db" "$DB_DIR/${sess}.db" 2>/dev/null \
        || note "  no .db for $sess (skipped archive)"

    note "DONE  $sess wall=${sess_wall}s"
done

TOTAL_EPOCH=$(($(date +%s) - START_EPOCH))
note "ALL DONE total_wall=${TOTAL_EPOCH}s"
echo "" >> "$OUT_LOG"
echo "# Total rebuild wall-clock: ${TOTAL_EPOCH}s" >> "$OUT_LOG"

note "rebuild successful — log at $OUT_LOG"
note "next: append per-theory + per-command timings via append_session_timings.py / extract_session_command_timings.py against $DB_DIR/*.db"
note "to replace canonical: mv $OUT_LOG $HEAP_DIR_HOST/build_log.txt"
