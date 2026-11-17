theory Environment_Parameters imports Environment_Spec_Review begin
definition r2_address_configuration ::
  "unit ptr list \<Rightarrow> addr set \<Rightarrow> addr set \<Rightarrow> bool" where
  "r2_address_configuration fs g st = (length fs = 19 \<and> ((((c_fnptr_guard (fs ! 0) \<and>
   c_fnptr_guard (fs ! 1)) \<and>
  c_fnptr_guard (fs ! 2) \<and>
  c_fnptr_guard (fs ! 3) \<and>
  c_fnptr_guard (fs ! 4)) \<and>
 (c_fnptr_guard (fs ! 5) \<and>
  c_fnptr_guard (fs ! 6)) \<and>
 c_fnptr_guard (fs ! 7) \<and>
 c_fnptr_guard (fs ! 8) \<and>
 c_fnptr_guard (fs ! 9)) \<and>
((c_fnptr_guard (fs ! 10) \<and>
  c_fnptr_guard (fs ! 11)) \<and>
 c_fnptr_guard (fs ! 12) \<and>
 c_fnptr_guard (fs ! 13) \<and>
 c_fnptr_guard (fs ! 14)) \<and>
(c_fnptr_guard (fs ! 15) \<and>
 c_fnptr_guard (fs ! 16) \<and>
 c_fnptr_guard (fs ! 17)) \<and>
c_fnptr_guard (fs ! 18) \<and>
all_distinct
 (Node
   (Node
     (Node (Node Tip (fs ! 0) False Tip)
       (fs ! 1) False
       (Node Tip (fs ! 2) False
         (Node Tip (fs ! 3) False Tip)))
     (fs ! 4) False
     (Node (Node Tip (fs ! 5) False Tip)
       (fs ! 6) False
       (Node Tip (fs ! 7) False
         (Node Tip (fs ! 8) False Tip))))
   (fs ! 9) False
   (Node
     (Node (Node Tip (fs ! 10) False Tip)
       (fs ! 11) False
       (Node Tip (fs ! 12) False
         (Node Tip (fs ! 13) False Tip)))
     (fs ! 14) False
     (Node (Node Tip (fs ! 15) False Tip)
       (fs ! 16) False
       (Node Tip (fs ! 17) False
         (Node Tip (fs ! 18) False
           Tip))))) \<and>
g \<inter> st = {}))"


end
