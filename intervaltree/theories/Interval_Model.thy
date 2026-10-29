theory Interval_Model
  imports Main
begin

text \<open>A manual port of VERO's frozen interval-list reference implementation.
The executable merge remains insertion sort followed by recursive coalescing;
it is not defined by selecting an abstract canonical representative.  In
particular, chop retains VERO's zero-width-cut behaviour.\<close>

record iv =
  lo :: int
  hi :: int

definition iv_points :: "'a iv_scheme \<Rightarrow> int set" where
  "iv_points i = {lo i..<hi i}"

definition covered :: "int \<Rightarrow> 'a iv_scheme list \<Rightarrow> bool" where
  "covered x xs \<longleftrightarrow> (\<exists>i\<in>set xs. lo i \<le> x \<and> x < hi i)"

definition coverage :: "'a iv_scheme list \<Rightarrow> int set" where
  "coverage xs = \<Union>(iv_points ` set xs)"

definition total_len :: "'a iv_scheme list \<Rightarrow> int" where
  "total_len xs = sum_list (map (\<lambda>i. hi i - lo i) xs)"

definition clamped_total_len :: "'a iv_scheme list \<Rightarrow> int" where
  "clamped_total_len xs = sum_list (map (\<lambda>i. max 0 (hi i - lo i)) xs)"

definition canonical_intervals :: "'a iv_scheme list \<Rightarrow> bool" where
  "canonical_intervals xs \<longleftrightarrow>
     (\<forall>i\<in>set xs. lo i < hi i) \<and>
     (\<forall>k. Suc k < length xs \<longrightarrow> hi (xs ! k) < lo (xs ! Suc k))"

definition overlaps :: "iv \<Rightarrow> iv \<Rightarrow> bool" where
  "overlaps a b \<longleftrightarrow> lo a < hi b \<and> lo b < hi a"

fun insert_by_lo :: "iv \<Rightarrow> iv list \<Rightarrow> iv list" where
  "insert_by_lo i [] = (if lo i < hi i then [i] else [])"
| "insert_by_lo i (j # js) =
     (if lo i < hi i then
        if lo i \<le> lo j then i # j # js else j # insert_by_lo i js
      else j # js)"

definition sort_by_lo :: "iv list \<Rightarrow> iv list" where
  "sort_by_lo xs = foldr insert_by_lo xs []"

function coalesce :: "iv list \<Rightarrow> iv list" where
  "coalesce [] = []"
| "coalesce [x] = [x]"
| "coalesce (x # y # xs) =
     (if lo y \<le> hi x then
        coalesce (\<lparr>lo = lo x, hi = max (hi x) (hi y)\<rparr> # xs)
      else x # coalesce (y # xs))"
  by pat_completeness auto
termination
  by (relation "measure length") auto

definition merge_overlaps :: "iv list \<Rightarrow> iv list" where
  "merge_overlaps xs = coalesce (sort_by_lo xs)"

definition chop_one :: "int \<Rightarrow> int \<Rightarrow> iv \<Rightarrow> iv list" where
  "chop_one b e i =
    filter (\<lambda>p. lo p < hi p)
      ((if lo i < b then [\<lparr>lo = lo i, hi = min (hi i) b\<rparr>] else []) @
       (if e < hi i then [\<lparr>lo = max (lo i) e, hi = hi i\<rparr>] else []))"

definition chop :: "int \<Rightarrow> int \<Rightarrow> iv list \<Rightarrow> iv list" where
  "chop b e xs = concat (map (chop_one b e) xs)"

fun cut_all :: "iv list \<Rightarrow> iv list \<Rightarrow> iv list" where
  "cut_all [] xs = xs"
| "cut_all (r # rs) xs = cut_all rs (chop (lo r) (hi r) xs)"

lemma iv_points_iff [simp]:
  "x \<in> iv_points i \<longleftrightarrow> lo i \<le> x \<and> x < hi i"
  by (simp add: iv_points_def)

lemma finite_iv_points [simp]: "finite (iv_points i)"
  by (simp add: iv_points_def)

lemma coverage_iff [simp]: "x \<in> coverage xs \<longleftrightarrow> covered x xs"
  by (auto simp: coverage_def covered_def)

lemma finite_coverage [simp]: "finite (coverage xs)"
  by (simp add: coverage_def)

lemma covered_Nil [simp]: "\<not> covered x []"
  by (simp add: covered_def)

lemma covered_Cons [simp]:
  "covered x (i # xs) \<longleftrightarrow> (lo i \<le> x \<and> x < hi i) \<or> covered x xs"
  by (auto simp: covered_def)

lemma covered_append [simp]:
  "covered x (xs @ ys) \<longleftrightarrow> covered x xs \<or> covered x ys"
  by (auto simp: covered_def)

lemma coverage_Nil [simp]: "coverage [] = {}"
  by (auto simp: covered_def)

lemma coverage_Cons [simp]: "coverage (i # xs) = iv_points i \<union> coverage xs"
  by auto

lemma coverage_append [simp]: "coverage (xs @ ys) = coverage xs \<union> coverage ys"
  by auto

lemma total_len_Nil [simp]: "total_len [] = 0"
  by (simp add: total_len_def)

lemma total_len_Cons [simp]: "total_len (i # xs) = hi i - lo i + total_len xs"
  by (simp add: total_len_def)

lemma total_len_append [simp]: "total_len (xs @ ys) = total_len xs + total_len ys"
  by (simp add: total_len_def)

lemma canonical_Nil [simp]: "canonical_intervals []"
  by (simp add: canonical_intervals_def)

lemma canonical_singleton [simp]: "canonical_intervals [i] \<longleftrightarrow> lo i < hi i"
  by (auto simp: canonical_intervals_def)

lemma interval_separation_transp:
  "transp (\<lambda>a b::'a iv_scheme. hi a < lo b \<and> lo b < hi b)"
  by (auto simp: transp_def; linarith)

lemma canonical_sorted_wrt:
  "canonical_intervals xs \<longleftrightarrow>
    (\<forall>i\<in>set xs. lo i < hi i) \<and>
    sorted_wrt (\<lambda>a b. hi a < lo b \<and> lo b < hi b) xs"
  using sorted_wrt_iff_nth_Suc_transp[OF interval_separation_transp, of xs]
  by (auto simp: canonical_intervals_def dest: nth_mem)

lemma canonical_Cons:
  "canonical_intervals (i # xs) \<longleftrightarrow>
    lo i < hi i \<and> canonical_intervals xs \<and> (\<forall>j\<in>set xs. hi i < lo j)"
  by (auto simp: canonical_sorted_wrt)

lemma canonical_measure:
  "canonical_intervals xs \<Longrightarrow> total_len xs = int (card (coverage xs))"
proof (induction xs)
  case Nil
  then show ?case by simp
next
  case (Cons i xs)
  then have nonempty: "lo i < hi i" and tail: "canonical_intervals xs"
    and separated: "\<forall>j\<in>set xs. hi i < lo j"
    by (auto simp: canonical_Cons)
  have disjoint: "iv_points i \<inter> coverage xs = {}"
  proof (rule equals0I)
    fix p
    assume "p \<in> iv_points i \<inter> coverage xs"
    then obtain j where j: "j \<in> set xs" "lo j \<le> p" "p < hi i"
      by (auto simp: covered_def)
    have "hi i < lo j" using separated j(1) by blast
    with j show False by linarith
  qed
  have interval_card: "int (card (iv_points i)) = hi i - lo i"
    using nonempty by (simp add: iv_points_def)
  show ?case
    using Cons.IH[OF tail] interval_card
    by (simp add: card_Un_disjoint disjoint)
qed

lemma insert_by_lo_set:
  "set (insert_by_lo i xs) = (if lo i < hi i then insert i (set xs) else set xs)"
  by (induction xs) auto

lemma sort_by_lo_set:
  "set (sort_by_lo xs) = {i \<in> set xs. lo i < hi i}"
  by (induction xs) (auto simp: sort_by_lo_def insert_by_lo_set)

lemma sort_by_lo_coverage:
  "covered x (sort_by_lo xs) = covered x xs"
  by (auto simp: covered_def sort_by_lo_set)

lemma insert_by_lo_insort:
  "insert_by_lo i xs = (if lo i < hi i then insort_key lo i xs else xs)"
  by (induction xs) auto

lemma sort_by_lo_sort_key:
  "sort_by_lo xs = sort_key lo (filter (\<lambda>i. lo i < hi i) xs)"
  by (induction xs) (simp_all add: sort_by_lo_def sort_key_def insert_by_lo_insort)

lemma sort_by_lo_sorted:
  "sorted (map lo (sort_by_lo xs))"
  by (simp add: sort_by_lo_sort_key)

lemma coalesce_coverage:
  "sorted (map lo xs) \<Longrightarrow> covered p (coalesce xs) = covered p xs"
proof (induction xs rule: coalesce.induct)
  case 1
  then show ?case by simp
next
  case (2 x)
  then show ?case by simp
next
  case (3 x y xs)
  show ?case
  proof (cases "lo y \<le> hi x")
    case True
    let ?z = "\<lparr>lo = lo x, hi = max (hi x) (hi y)\<rparr>"
    have sorted: "sorted (map lo (?z # xs))"
      using "3.prems" by auto
    have ih: "covered p (coalesce (?z # xs)) = covered p (?z # xs)"
      using "3.IH"(1)[OF True sorted] by simp
    show ?thesis
      using "3.prems" True ih
      by (auto simp: max_def split: if_splits)
  next
    case False
    have sorted: "sorted (map lo (y # xs))" using "3.prems" by simp
    show ?thesis using "3.IH"(2)[OF False sorted] False by simp
  qed
qed

lemma merge_coverage:
  "covered p (merge_overlaps xs) = covered p xs"
  by (simp add: merge_overlaps_def coalesce_coverage sort_by_lo_sorted sort_by_lo_coverage)

lemma coalesce_lo_origin:
  "out_iv \<in> set (coalesce xs) \<Longrightarrow> \<exists>i\<in>set xs. lo out_iv = lo i"
  by (induction xs arbitrary: out_iv rule: coalesce.induct)
     (auto split: if_splits)

lemma coalesce_canonical:
  "sorted (map lo xs) \<Longrightarrow> (\<forall>i\<in>set xs. lo i < hi i) \<Longrightarrow>
    canonical_intervals (coalesce xs)"
proof (induction xs rule: coalesce.induct)
  case 1
  then show ?case by simp
next
  case (2 x)
  then show ?case by simp
next
  case (3 x y xs)
  show ?case
  proof (cases "lo y \<le> hi x")
    case True
    let ?z = "\<lparr>lo = lo x, hi = max (hi x) (hi y)\<rparr>"
    have sorted: "sorted (map lo (?z # xs))" using "3.prems" by auto
    have valid: "\<forall>i\<in>set (?z # xs). lo i < hi i"
      using "3.prems" by auto
    show ?thesis using "3.IH"(1)[OF True sorted valid] True by simp
  next
    case False
    have sorted: "sorted (map lo (y # xs))" using "3.prems" by simp
    have valid: "\<forall>i\<in>set (y # xs). lo i < hi i" using "3.prems" by simp
    have tail: "canonical_intervals (coalesce (y # xs))"
      using "3.IH"(2)[OF False sorted valid] by simp
    have gap: "\<forall>out_iv\<in>set (coalesce (y # xs)). hi x < lo out_iv"
    proof (intro ballI)
      fix out_iv
      assume "out_iv \<in> set (coalesce (y # xs))"
      then obtain i where i: "i \<in> set (y # xs)" "lo out_iv = lo i"
        using coalesce_lo_origin by blast
      have "lo y \<le> lo i" using "3.prems" i(1) by auto
      with False i(2) show "hi x < lo out_iv" by linarith
    qed
    show ?thesis using False tail gap "3.prems" by (simp add: canonical_Cons)
  qed
qed

lemma merge_is_canonical:
  "canonical_intervals (merge_overlaps xs)"
  unfolding merge_overlaps_def
  by (rule coalesce_canonical) (auto simp: sort_by_lo_sorted sort_by_lo_set)

lemma chop_one_nonempty:
  "i \<in> set (chop_one b e j) \<Longrightarrow> lo i < hi i"
  by (auto simp: chop_one_def)

lemma chop_nonempty:
  "i \<in> set (chop b e xs) \<Longrightarrow> lo i < hi i"
  by (auto simp: chop_def dest: chop_one_nonempty)

lemma chop_one_pointwise:
  "covered x (chop_one b e i) \<longleftrightarrow>
     (lo i \<le> x \<and> x < hi i) \<and> \<not> (b \<le> x \<and> x < e)"
  unfolding chop_one_def
  by (auto simp: min_def max_def split: if_splits)

lemma chop_pointwise:
  "covered x (chop b e xs) \<longleftrightarrow>
     covered x xs \<and> \<not> (b \<le> x \<and> x < e)"
  unfolding chop_def
  by (induction xs) (auto simp: chop_one_pointwise)

lemma chop_coverage:
  "coverage (chop b e xs) = coverage xs - {b..<e}"
  by (auto simp: chop_pointwise)

lemma cut_all_pointwise:
  "covered x (cut_all rs xs) \<longleftrightarrow> covered x xs \<and> \<not> covered x rs"
  by (induction rs arbitrary: xs) (auto simp: chop_pointwise)

lemma cut_all_coverage:
  "coverage (cut_all rs xs) = coverage xs - coverage rs"
  by (auto simp: cut_all_pointwise)

lemma chop_append:
  "chop b e (xs @ ys) = chop b e xs @ chop b e ys"
  by (simp add: chop_def)

lemma zero_width_cut_splits:
  "chop 2 2 [\<lparr>lo = 0, hi = 4\<rparr>] =
     [\<lparr>lo = 0, hi = 2\<rparr>, \<lparr>lo = 2, hi = 4\<rparr>]"
  by (simp add: chop_def chop_one_def)

end
