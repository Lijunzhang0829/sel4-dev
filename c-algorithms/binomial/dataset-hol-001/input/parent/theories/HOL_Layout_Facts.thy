theory HOL_Layout_Facts imports Binomial_Heap_C begin
lemma tree_size[simp, size_align_simps]: "size_of TYPE(StrictC'_BinomialTree_C) = 24"
  by (simp add: size_td_simps size_of_def typ_info_t_StrictC'_BinomialTree_C_def)
lemma tree_align[simp, size_align_simps]: "align_of TYPE(StrictC'_BinomialTree_C) = 8"
  by (simp add: size_td_simps align_of_def typ_info_t_StrictC'_BinomialTree_C_def)
lemma heap_size[simp, size_align_simps]: "size_of TYPE(StrictC'_BinomialHeap_C) = 40"
  by (simp add: size_td_simps size_of_def typ_info_t_StrictC'_BinomialHeap_C_def)
lemma heap_align[simp, size_align_simps]: "align_of TYPE(StrictC'_BinomialHeap_C) = 8"
  by (simp add: size_td_simps align_of_def typ_info_t_StrictC'_BinomialHeap_C_def)

declare BINOMIAL_HEAP_TYPE_MIN_def[simp] BINOMIAL_HEAP_TYPE_MAX_def[simp]
declare binomial_heap_null_value_def[simp] binomial_heap_null_value_initial_value_def[simp]
declare binomial_heap_input.malloc.size_'_def[simp]
declare binomial_heap_input.calloc.count_'_def[simp]
declare binomial_heap_input.calloc.size_'_def[simp]
declare binomial_heap_input.calloc.ret'_'_def[simp]

lemmas StrictC'_BinomialHeap_C_size = heap_size
lemmas StrictC'_BinomialHeap_C_align = heap_align
lemmas StrictC'_BinomialTree_C_size = tree_size
lemmas StrictC'_BinomialTree_C_align = tree_align

ML_file "HOL_Field_Layout.ML"
local_setup \<open>HOL_Field_Layout.certify "StrictC'_BinomialTree_C"\<close>
local_setup \<open>HOL_Field_Layout.certify "StrictC'_BinomialHeap_C"\<close>

end
