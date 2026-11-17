theory Project_Input imports Binomial_Heap_Model begin
bundle l4v_word_context begin end
ML \<open>
structure Thy_Info = struct val get_theory = Build.get_theory end;
structure SHA1 = struct open SHA1 val rep = Message_Digest.rep end;
\<close>
end
