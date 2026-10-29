theory Interval_Tree
  imports Interval_Model
begin

text \<open>
  Explicit-state translation of the centered AVL implementation in
  intervaltree/node.py, upstream commit 1bc406e1f441577c4e421fc51aba2ab67fbd97fb.
  Buckets model Python sets by duplicate-free lists; equality includes an
  immutable natural-number payload. Integer endpoints and finite trees are
  intentional restrictions. No Python heap/aliasing correspondence is asserted.

  The upstream rotation, removal, pruning and predecessor routines are mutually
  recursive. execute_tree retains their branches and uses an explicit fuel
  argument. None means fuel exhaustion, a missing required entry, or a malformed
  internal shape. Public status wrappers distinguish rejected user operations
  from internal execution failure. Fuel adequacy is a separate proof obligation,
  not an assumption available to the other proofs.
\<close>

record entry = iv +
  payload :: nat

definition entry_valid :: "entry \<Rightarrow> bool" where
  "entry_valid e \<longleftrightarrow> lo e < hi e"

definition entry_hit :: "entry \<Rightarrow> int \<Rightarrow> bool" where
  "entry_hit e p \<longleftrightarrow> lo e \<le> p \<and> p < hi e"

definition erase_payload :: "entry \<Rightarrow> iv" where
  "erase_payload e = \<lparr>lo = lo e, hi = hi e\<rparr>"

datatype itree = Empty | Branch int "entry list" itree itree nat int

fun cached_depth :: "itree \<Rightarrow> nat" where
  "cached_depth Empty = 0"
| "cached_depth (Branch c s l r h b) = h"

fun cached_balance :: "itree \<Rightarrow> int" where
  "cached_balance Empty = 0"
| "cached_balance (Branch c s l r h b) = b"

fun node_center :: "itree \<Rightarrow> int" where
  "node_center Empty = 0"
| "node_center (Branch c s l r h b) = c"

fun node_bucket :: "itree \<Rightarrow> entry list" where
  "node_bucket Empty = []"
| "node_bucket (Branch c s l r h b) = s"

fun node_child :: "bool \<Rightarrow> itree \<Rightarrow> itree" where
  "node_child d Empty = Empty"
| "node_child d (Branch c s l r h b) = (if d then r else l)"

fun set_child :: "bool \<Rightarrow> itree \<Rightarrow> itree \<Rightarrow> itree" where
  "set_child d u Empty = Empty"
| "set_child d u (Branch c s l r h b) =
    (if d then Branch c s l u h b else Branch c s u r h b)"

fun set_bucket :: "entry list \<Rightarrow> itree \<Rightarrow> itree" where
  "set_bucket s Empty = Empty"
| "set_bucket s (Branch c old l r h b) = Branch c s l r h b"

definition mk_branch :: "int \<Rightarrow> entry list \<Rightarrow> itree \<Rightarrow> itree \<Rightarrow> itree" where
  "mk_branch c s l r = Branch c s l r
    (Suc (max (cached_depth l) (cached_depth r)))
    (int (cached_depth r) - int (cached_depth l))"

fun refresh_node :: "itree \<Rightarrow> itree" where
  "refresh_node Empty = Empty"
| "refresh_node (Branch c s l r h b) = mk_branch c s l r"

definition singleton_node :: "entry \<Rightarrow> itree" where
  "singleton_node e = mk_branch (lo e) [e] Empty Empty"

fun tree_entries :: "itree \<Rightarrow> entry set" where
  "tree_entries Empty = {}"
| "tree_entries (Branch c s l r h b) = set s \<union> tree_entries l \<union> tree_entries r"

fun tree_entry_list :: "itree \<Rightarrow> entry list" where
  "tree_entry_list Empty = []"
| "tree_entry_list (Branch c s l r h b) = s @ tree_entry_list l @ tree_entry_list r"

fun actual_depth :: "itree \<Rightarrow> nat" where
  "actual_depth Empty = 0"
| "actual_depth (Branch c s l r h b) = Suc (max (actual_depth l) (actual_depth r))"

fun tree_centers :: "itree \<Rightarrow> int set" where
  "tree_centers Empty = {}"
| "tree_centers (Branch c s l r h b) = insert c (tree_centers l \<union> tree_centers r)"

text \<open>
  Routing states that left-subtree intervals end at or before the center,
  while right-subtree intervals begin strictly after it. This retains the
  centered-tree invariant; it is not the max-end augmentation of an ordinary
  interval BST. Metadata and AVL balance are specified separately so that query
  correctness does not assume unrelated cache properties.
\<close>

fun routing_ok :: "itree \<Rightarrow> bool" where
  "routing_ok Empty = True"
| "routing_ok (Branch c s l r h b) =
    (s \<noteq> [] \<and> distinct s \<and> (\<forall>e\<in>set s. entry_valid e \<and> entry_hit e c) \<and>
     routing_ok l \<and> routing_ok r \<and>
     (\<forall>e\<in>tree_entries l. hi e \<le> c) \<and>
     (\<forall>e\<in>tree_entries r. c < lo e) \<and>
     (\<forall>x\<in>tree_centers l. x < c) \<and>
     (\<forall>x\<in>tree_centers r. c < x))"

fun metadata_ok :: "itree \<Rightarrow> bool" where
  "metadata_ok Empty = True"
| "metadata_ok (Branch c s l r h b) =
    (metadata_ok l \<and> metadata_ok r \<and>
     h = Suc (max (cached_depth l) (cached_depth r)) \<and>
     b = int (cached_depth r) - int (cached_depth l))"

fun balanced_tree :: "itree \<Rightarrow> bool" where
  "balanced_tree Empty = True"
| "balanced_tree (Branch c s l r h b) =
    (balanced_tree l \<and> balanced_tree r \<and> abs b < 2)"

definition tree_wf :: "itree \<Rightarrow> bool" where
  "tree_wf t \<longleftrightarrow> routing_ok t \<and> metadata_ok t \<and> balanced_tree t"

fun tree_at :: "int \<Rightarrow> itree \<Rightarrow> entry set" where
  "tree_at p Empty = {}"
| "tree_at p (Branch c s l r h b) =
    set (filter (\<lambda>e. entry_hit e p) s) \<union>
      (if p < c then tree_at p l else if c < p then tree_at p r else {})"

fun tree_at_list :: "int \<Rightarrow> itree \<Rightarrow> entry list" where
  "tree_at_list p Empty = []"
| "tree_at_list p (Branch c s l r h b) =
    filter (\<lambda>e. entry_hit e p) s @
      (if p < c then tree_at_list p l else if c < p then tree_at_list p r else [])"

lemma tree_at_list_set: "set (tree_at_list p t) = tree_at p t"
  by (induction t) auto

declare tree_at.simps[code del]

lemma tree_at_code [code]: "tree_at p t = set (tree_at_list p t)"
  using tree_at_list_set by simp

datatype tree_command =
    AddEntry entry
  | RemoveEntry entry bool
  | RemoveEntries "entry list"
  | RotateNode
  | SingleRotateNode
  | DoubleRotateNode
  | PruneNode
  | PopGreatestNode

datatype tree_reply = TreeReply itree bool | PopReply itree itree

text \<open>
  The boolean in TreeReply is the upstream remove-helper's shared done flag.
  Node-independent operations ignore it. Bucket traversals use the explicit
  list order; observable query results are sets. As in Python, different set
  iteration orders need not yield identical internal shapes.
\<close>

fun execute_tree :: "nat \<Rightarrow> tree_command \<Rightarrow> itree \<Rightarrow> tree_reply option" where
  "execute_tree 0 cmd t = None"
| "execute_tree (Suc n) (AddEntry e) t =
    (case t of Empty \<Rightarrow> Some (TreeReply (singleton_node e) False)
     | Branch c s l r h b \<Rightarrow>
       if entry_hit e c then
         Some (TreeReply (set_bucket (if e \<in> set s then s else e # s) t) False)
       else let d = (c < lo e); child = node_child d t in
         if child = Empty then
           Some (TreeReply (refresh_node (set_child d (singleton_node e) t)) False)
         else (case execute_tree n (AddEntry e) child of
             Some (TreeReply u done) \<Rightarrow> execute_tree n RotateNode (set_child d u t)
           | _ \<Rightarrow> None))"
| "execute_tree (Suc n) (RemoveEntry e raises) t =
    (case t of Empty \<Rightarrow> if raises then None else Some (TreeReply Empty True)
     | Branch c s l r h b \<Rightarrow>
       if entry_hit e c then
         if e \<notin> set s then (if raises then None else Some (TreeReply t True))
         else let rest = filter (\<lambda>x. x \<noteq> e) s in
           if rest \<noteq> [] then Some (TreeReply (set_bucket rest t) True)
           else execute_tree n PruneNode (set_bucket [] t)
       else let d = (c < lo e); child = node_child d t in
         if child = Empty then (if raises then None else Some (TreeReply t True))
         else (case execute_tree n (RemoveEntry e raises) child of
             Some (TreeReply u done) \<Rightarrow>
               if done then Some (TreeReply (set_child d u t) True)
               else execute_tree n RotateNode (set_child d u t)
           | _ \<Rightarrow> None))"
| "execute_tree (Suc n) (RemoveEntries es) t =
    (case es of [] \<Rightarrow> Some (TreeReply t False)
     | e # rest \<Rightarrow> (case execute_tree n (RemoveEntry e True) t of
         Some (TreeReply u done) \<Rightarrow> execute_tree n (RemoveEntries rest) u
       | _ \<Rightarrow> None))"
| "execute_tree (Suc n) RotateNode t =
    (let u = refresh_node t; b = cached_balance u; d = (0 < b) in
      if abs b < 2 then Some (TreeReply u False)
      else if (d = (0 < cached_balance (node_child d u))) \<or>
              cached_balance (node_child d u) = 0
           then execute_tree n SingleRotateNode u
           else execute_tree n DoubleRotateNode u)"
| "execute_tree (Suc n) SingleRotateNode t =
    (let d = (0 < cached_balance t); save = node_child d t in
     if save = Empty then None
     else case execute_tree n RotateNode (set_child d (node_child (\<not>d) save) t) of
       Some (TreeReply down done) \<Rightarrow>
         (let promotees = filter (\<lambda>e. entry_hit e (node_center save)) (node_bucket down) in
          case execute_tree n (RemoveEntries promotees) down of
            Some (TreeReply down' ignored) \<Rightarrow>
              Some (TreeReply
                (refresh_node (set_bucket (remdups (node_bucket save @ promotees))
                   (set_child (\<not>d) down' save))) False)
          | _ \<Rightarrow> None)
     | _ \<Rightarrow> None)"
| "execute_tree (Suc n) DoubleRotateNode t =
    (let d = (0 < cached_balance t) in
      case execute_tree n SingleRotateNode (node_child d t) of
        Some (TreeReply child ignored) \<Rightarrow>
          execute_tree n SingleRotateNode (refresh_node (set_child d child t))
      | _ \<Rightarrow> None)"
| "execute_tree (Suc n) PruneNode t =
    (case t of Empty \<Rightarrow> None
     | Branch c s l r h b \<Rightarrow>
       if l = Empty then Some (TreeReply r False)
       else if r = Empty then Some (TreeReply l False)
       else (case execute_tree n PopGreatestNode l of
          Some (PopReply heir l') \<Rightarrow>
            execute_tree n RotateNode
              (refresh_node (set_child True r (set_child False l' heir)))
        | _ \<Rightarrow> None))"
| "execute_tree (Suc n) PopGreatestNode t =
    (case t of Empty \<Rightarrow> None
     | Branch c s l r h b \<Rightarrow>
       if s = [] then None
       else if r = Empty then
         (let max_end = Max (hi ` set s);
              c' = fold max (map hi (filter (\<lambda>e. hi e \<noteq> max_end) s)) c;
              moved = filter (\<lambda>e. entry_hit e c') s;
              rest = filter (\<lambda>e. \<not> entry_hit e c') s;
              heir = mk_branch c' moved Empty Empty
          in Some (PopReply heir (if rest = [] then l else set_bucket rest t)))
       else (case execute_tree n PopGreatestNode r of
          Some (PopReply heir r') \<Rightarrow>
            (let promotees = filter (\<lambda>e. entry_hit e (node_center heir)) s;
                 rest = filter (\<lambda>e. \<not> entry_hit e (node_center heir)) s;
                 heir' = set_bucket (remdups (node_bucket heir @ promotees)) heir;
                 remaining = set_child True r' (set_bucket rest t)
             in case (if rest = [] then execute_tree n PruneNode remaining
                      else execute_tree n RotateNode (refresh_node remaining)) of
                  Some (TreeReply u ignored) \<Rightarrow> Some (PopReply heir' u)
                | _ \<Rightarrow> None)
        | _ \<Rightarrow> None))"

definition tree_add :: "nat \<Rightarrow> entry \<Rightarrow> itree \<Rightarrow> itree option" where
  "tree_add fuel e t = (case execute_tree fuel (AddEntry e) t of
       Some (TreeReply u done) \<Rightarrow> Some u | _ \<Rightarrow> None)"

definition tree_remove :: "nat \<Rightarrow> entry \<Rightarrow> itree \<Rightarrow> itree option" where
  "tree_remove fuel e t = (case execute_tree fuel (RemoveEntry e True) t of
       Some (TreeReply u done) \<Rightarrow> Some u | _ \<Rightarrow> None)"

definition tree_discard :: "nat \<Rightarrow> entry \<Rightarrow> itree \<Rightarrow> itree option" where
  "tree_discard fuel e t = (case execute_tree fuel (RemoveEntry e False) t of
       Some (TreeReply u done) \<Rightarrow> Some u | _ \<Rightarrow> None)"

definition tree_rotate :: "nat \<Rightarrow> itree \<Rightarrow> itree option" where
  "tree_rotate fuel t = (case execute_tree fuel RotateNode t of
       Some (TreeReply u done) \<Rightarrow> Some u | _ \<Rightarrow> None)"

fun boundary_inc :: "int \<Rightarrow> (int \<times> nat) list \<Rightarrow> (int \<times> nat) list" where
  "boundary_inc k [] = [(k,1)]"
| "boundary_inc k ((x,n)#xs) =
    (if k=x then (x,Suc n)#xs else (x,n)#boundary_inc k xs)"

fun boundary_dec :: "int \<Rightarrow> (int \<times> nat) list \<Rightarrow> (int \<times> nat) list" where
  "boundary_dec k [] = []"
| "boundary_dec k ((x,n)#xs) =
    (if k=x then (if n \<le> 1 then xs else (x,n-1)#xs)
     else (x,n)#boundary_dec k xs)"

definition boundary_count :: "(int \<times> nat) list \<Rightarrow> int \<Rightarrow> nat" where
  "boundary_count bs k = (case map_of bs k of None \<Rightarrow> 0 | Some n \<Rightarrow> n)"

definition boundary_keys :: "(int \<times> nat) list \<Rightarrow> int list" where
  "boundary_keys bs = sort (remdups (map fst (filter (\<lambda>p. 0 < snd p) bs)))"

record tree_state =
  root_node :: itree
  all_items :: "entry list"
  boundaries :: "(int \<times> nat) list"

definition empty_state :: tree_state where
  "empty_state = \<lparr>root_node=Empty, all_items=[], boundaries=[]\<rparr>"

definition endpoint_count :: "entry list \<Rightarrow> int \<Rightarrow> nat" where
  "endpoint_count es k = length (filter (\<lambda>e. lo e=k) es) + length (filter (\<lambda>e. hi e=k) es)"

definition state_wf :: "tree_state \<Rightarrow> bool" where
  "state_wf s \<longleftrightarrow> tree_wf (root_node s) \<and> distinct (all_items s) \<and>
     set (all_items s) = tree_entries (root_node s) \<and>
     distinct (map fst (boundaries s)) \<and> (\<forall>p\<in>set (boundaries s). 0 < snd p) \<and>
     (\<forall>k. boundary_count (boundaries s) k = endpoint_count (all_items s) k)"

definition state_add :: "nat \<Rightarrow> entry \<Rightarrow> tree_state \<Rightarrow> tree_state option" where
  "state_add fuel e s =
    (if e \<in> set (all_items s) then Some s
     else if \<not> entry_valid e then None
     else case tree_add fuel e (root_node s) of None \<Rightarrow> None
       | Some t \<Rightarrow> Some (s\<lparr>root_node := t, all_items := e # all_items s,
           boundaries := boundary_inc (hi e) (boundary_inc (lo e) (boundaries s))\<rparr>))"

definition state_remove :: "nat \<Rightarrow> entry \<Rightarrow> tree_state \<Rightarrow> tree_state option" where
  "state_remove fuel e s =
    (if e \<notin> set (all_items s) then None
     else case tree_remove fuel e (root_node s) of None \<Rightarrow> None
       | Some t \<Rightarrow> Some (s\<lparr>root_node := t,
           all_items := filter (\<lambda>x. x \<noteq> e) (all_items s),
           boundaries := boundary_dec (hi e) (boundary_dec (lo e) (boundaries s))\<rparr>))"

definition state_discard :: "nat \<Rightarrow> entry \<Rightarrow> tree_state \<Rightarrow> tree_state option" where
  "state_discard fuel e s =
    (if e \<notin> set (all_items s) then Some s else state_remove fuel e s)"

datatype update_status = Updated tree_state | InvalidInterval | MissingInterval | ExecutionFailure

definition state_add_status :: "nat \<Rightarrow> entry \<Rightarrow> tree_state \<Rightarrow> update_status" where
  "state_add_status fuel e s =
    (if e \<notin> set (all_items s) \<and> \<not> entry_valid e then InvalidInterval
     else case state_add fuel e s of Some u \<Rightarrow> Updated u | None \<Rightarrow> ExecutionFailure)"

definition state_remove_status :: "nat \<Rightarrow> entry \<Rightarrow> tree_state \<Rightarrow> update_status" where
  "state_remove_status fuel e s =
    (if e \<notin> set (all_items s) then MissingInterval
     else case state_remove fuel e s of Some u \<Rightarrow> Updated u | None \<Rightarrow> ExecutionFailure)"

definition state_at :: "int \<Rightarrow> tree_state \<Rightarrow> entry set" where
  "state_at p s = tree_at p (root_node s)"

definition tree_overlap :: "int \<Rightarrow> int \<Rightarrow> tree_state \<Rightarrow> entry set" where
  "tree_overlap a b s = (if b \<le> a then {} else
     tree_at a (root_node s) \<union>
     \<Union>(set (map (\<lambda>p. tree_at p (root_node s))
       (filter (\<lambda>p. a \<le> p \<and> p < b) (boundary_keys (boundaries s))))))"

definition tree_envelop :: "int \<Rightarrow> int \<Rightarrow> tree_state \<Rightarrow> entry set" where
  "tree_envelop a b s = {e \<in> tree_overlap a b s. a \<le> lo e \<and> hi e \<le> b}"

text \<open>
  Finite enumerations for code generation. The following code equations are
  proved equal to the set-valued query definitions; they do not enumerate the
  infinite type of all entries. Duplicates in the intermediate list disappear
  at the public set-valued observation, as with Python's result.update.
\<close>

definition overlap_candidates :: "int \<Rightarrow> int \<Rightarrow> tree_state \<Rightarrow> entry list" where
  "overlap_candidates a b s = (if b \<le> a then [] else
     tree_at_list a (root_node s) @ concat (map (\<lambda>p. tree_at_list p (root_node s))
       (filter (\<lambda>p. a \<le> p \<and> p < b) (boundary_keys (boundaries s)))))"

lemma overlap_candidates_set: "set (overlap_candidates a b s) = tree_overlap a b s"
  by (auto simp: overlap_candidates_def tree_overlap_def tree_at_list_set)

declare tree_overlap_def[code del]

lemma tree_overlap_code [code]: "tree_overlap a b s = set (overlap_candidates a b s)"
  using overlap_candidates_set by simp

declare tree_envelop_def[code del]

lemma tree_envelop_code [code]:
  "tree_envelop a b s = set (filter (\<lambda>e. a \<le> lo e \<and> hi e \<le> b)
      (overlap_candidates a b s))"
  by (simp add: tree_envelop_def overlap_candidates_set)

datatype tree_update = Insert entry | Delete entry | Discard entry

fun apply_update :: "nat \<Rightarrow> tree_update \<Rightarrow> tree_state \<Rightarrow> tree_state option" where
  "apply_update fuel (Insert e) s = state_add fuel e s"
| "apply_update fuel (Delete e) s = state_remove fuel e s"
| "apply_update fuel (Discard e) s = state_discard fuel e s"

fun run_updates :: "nat \<Rightarrow> tree_update list \<Rightarrow> tree_state \<Rightarrow> tree_state option" where
  "run_updates fuel [] s = Some s"
| "run_updates fuel (op#ops) s = (case apply_update fuel op s of
       None \<Rightarrow> None | Some s' \<Rightarrow> run_updates fuel ops s')"

end
