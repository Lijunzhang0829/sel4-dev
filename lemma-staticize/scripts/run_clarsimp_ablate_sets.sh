#!/usr/bin/env bash
# Fill-in experiment: run clarsimp ablation (wasted_ablate.py) on the SAME
# corroboration + hard sets the 3 reach-B methods used, so all four methods
# share one experimental matrix. blast lemmas are N/A (the swap regex is
# auto|fastforce|force) and are omitted.
#
# Usage: run_clarsimp_ablate_sets.sh corrob   # 7 ablatable corroboration lemmas
#        run_clarsimp_ablate_sets.sh hard     # 7 hard/high-value lemmas
set -u
CTR=sel4-l4v
L4V=/sel4-project/verification/l4v
REPS="${REPS:-3}"
PER_TIMEOUT="${PER_TIMEOUT:-900}"
OUT="lemma-staticize/runs/clarsimp-ablate-${1}-$(date +%Y%m%d-%H%M%S).log"

# lemma|thy_rel|session|proof_line|tactic
CORROB=(
  "sep_heap_domD|proof/capDL-api/RWHelper_DP.thy|DSpecProofs|171|fastforce"
  "sep_heap_domD'|proof/capDL-api/RWHelper_DP.thy|DSpecProofs|176|fastforce"
  "sep_irq_node_domD'|proof/capDL-api/RWHelper_DP.thy|DSpecProofs|181|fastforce"
  "auth_graph_map_memI|proof/access-control/Access_AC.thy|Access|83|fastforce"
  "pas_refined_sita_mem|proof/access-control/Access_AC.thy|Access|283|auto"
  "tcb_domain_map_wellformed_mono|proof/access-control/Access_AC.thy|Access|231|auto"
  "reply_masters_mdbD1|proof/access-control/CNode_AC.thy|Access|473|fastforce"
)
HARD=(
  "cap_insert_simple_arch_caps_no_ap|proof/invariant-abstract/ARM/ArchCSpace_AI.thy|AInvs|518|auto"
  "SAC_partsSubjectAffects_exceptT|proof/infoflow/PolicySystemSAC.thy|InfoFlow|921|auto"
  "refinement2_both|proof/crefine/ARM/Refine_C.thy|CRefine|1019|fastforce"
  "requiv_user_mem_eq|proof/infoflow/ARM/ArchUserOp_IF.thy|InfoFlow|859|fastforce"
  "requiv_device_mem_eq|proof/infoflow/ARM/ArchUserOp_IF.thy|InfoFlow|811|fastforce"
  "abstract_invs|proof/infoflow/refine/ADT_IF_Refine.thy|InfoFlowC|812|fastforce"
  "ckernel_invariant|proof/refine/ARM/Refine.thy|Refine|875|fastforce"
)
case "$1" in
  corrob) SET=("${CORROB[@]}") ;;
  hard)   SET=("${HARD[@]}") ;;
  *) echo "usage: $0 corrob|hard"; exit 1 ;;
esac

echo "[batch] set=$1  reps=$REPS  per-timeout=${PER_TIMEOUT}s  -> $OUT" | tee "$OUT"
for row in "${SET[@]}"; do
  IFS='|' read -r lem thy sess line tac <<< "$row"
  echo "==== $lem ($sess) $thy:$line  $tac->clarsimp ====" | tee -a "$OUT"
  timeout "$PER_TIMEOUT" docker exec \
    -e LEMMA="$lem" -e THY="$L4V/$thy" -e SESSION="$sess" \
    -e PROOF_LINE="$line" -e TACTIC="$tac" -e REPS="$REPS" -e L4V_DIR="$L4V" \
    "$CTR" bash -lc 'cd /workspace && python3 tools/seL4-proof-search/Isa-Repl/wasted_ablate.py' \
    >> "$OUT" 2>&1 || echo "[result] {\"lemma\": \"$lem\", \"verdict\": \"TIMEOUT/ERR (${PER_TIMEOUT}s)\"}" | tee -a "$OUT"
  echo | tee -a "$OUT"
done
echo "[batch done] -> $OUT" | tee -a "$OUT"
