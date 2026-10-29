theory Interval_Model
  imports Main
begin

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

end
