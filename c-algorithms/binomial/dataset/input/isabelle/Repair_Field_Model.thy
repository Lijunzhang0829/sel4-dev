theory Repair_Field_Model imports Frozen_Model begin

definition bn_r3_old_node_fields where
 "bn_r3_old_node_fields s N t M \<longleftrightarrow>
  (\<forall>p\<in>N \<inter> M.
   value_C (bn_tree t p)=value_C (bn_tree s p) \<and>
   order_C (bn_tree t p)=order_C (bn_tree s p) \<and>
   subtrees_C (bn_tree t p)=subtrees_C (bn_tree s p))"

definition bn_r3_release where
  "bn_r3_release s D W t \<longleftrightarrow>
    allocator_wf (phantom_machine_state_' t) \<and>
    reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    (\<forall>q. live_blocks (phantom_machine_state_' t) q =
      (if q\<in>D then None else live_blocks (phantom_machine_state_' s) q)) \<and>
    (\<forall>x\<in>reserved_bytes (phantom_machine_state_' s). hrs_mem (t_hrs_' t) x = hrs_mem (t_hrs_' s) x) \<and>
    (\<forall>q n. live_blocks (phantom_machine_state_' s) q = Some n \<longrightarrow> q\<notin>W \<longrightarrow>
      (\<forall>x\<in>block_bytes q n. hrs_mem (t_hrs_' t) x = hrs_mem (t_hrs_' s) x))"
end
