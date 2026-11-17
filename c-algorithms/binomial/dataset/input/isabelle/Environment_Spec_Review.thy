theory Environment_Spec_Review imports Frozen_Model begin

definition audit_stack_addresses :: "addr set" where
 "audit_stack_addresses = {a. 4096 \<le> unat a \<and> unat a < 4160}"

locale Calg_Binomial_Reviewed_Stack = binomial_heap_input_global_addresses +
 assumes audit_stack_configuration: "Binomial_Heap_C.\<S> = audit_stack_addresses"

context Calg_Binomial_Reviewed_Stack begin

end
end
