theory Binomial_Bridge_Targets
 imports Binomial_Dataset_Interfaces Binomial_Dataset_Loop_Steps
begin

primrec bn_ds_node_list where
 "bn_ds_node_list (BN p cs) = p # concat (map bn_ds_node_list cs)"
definition bn_ds_forest_nodes where
 "bn_ds_forest_nodes F = set (concat (map (case_option [] bn_ds_node_list) F))"
definition bn_ds_physical_edges where
 "bn_ds_physical_edges s p = map (h_val (hrs_mem (t_hrs_' s)))
    (bn_ds_slots (subtrees_C (bn_tree s p)) (unat (order_C (bn_tree s p))))"

context binomial_heap_input_global_addresses begin

end
end
