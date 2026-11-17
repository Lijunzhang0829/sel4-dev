theory Heap_Environment
  imports AutoCorres2.AutoCorres
begin

text \<open>Draft external environment, not a verification of libc. Raw bytes are used
so allocation, relocation and aliasing are not erased by split-heap abstraction.
The reserved footprint covers caller payloads and other externally owned storage.
Only positive allocation sizes are in this first benchmark's scope.\<close>

record calg_allocator =
  live_blocks :: "addr \<Rightarrow> nat option"
  reserved_bytes :: "addr set"

definition block_bytes :: "addr \<Rightarrow> nat \<Rightarrow> addr set" where
  "block_bytes p n = {a. unat p \<le> unat a \<and> unat a < unat p + n}"

definition block_ok :: "addr \<Rightarrow> nat \<Rightarrow> bool" where
  "block_ok p n \<longleftrightarrow> p \<noteq> 0 \<and> 0 < n \<and>
    unat p + n \<le> 2 ^ 64 \<and> unat p mod 16 = 0"

definition allocator_wf :: "calg_allocator \<Rightarrow> bool" where
  "allocator_wf A \<longleftrightarrow>
    (\<forall>p n. live_blocks A p = Some n \<longrightarrow> block_ok p n \<and>
       block_bytes p n \<inter> reserved_bytes A = {}) \<and>
    (\<forall>p n q m. live_blocks A p = Some n \<longrightarrow>
       live_blocks A q = Some m \<longrightarrow> p \<noteq> q \<longrightarrow>
       block_bytes p n \<inter> block_bytes q m = {})"

definition fresh_block :: "calg_allocator \<Rightarrow> addr \<Rightarrow> nat \<Rightarrow> bool" where
  "fresh_block A p n \<longleftrightarrow> block_ok p n \<and>
    block_bytes p n \<inter> reserved_bytes A = {} \<and>
    (\<forall>q m. live_blocks A q = Some m \<longrightarrow>
      block_bytes p n \<inter> block_bytes q m = {})"

definition bytes_frame :: "addr set \<Rightarrow> heap_mem \<Rightarrow> heap_mem \<Rightarrow> bool" where
  "bytes_frame W h h' \<longleftrightarrow> (\<forall>a. a \<notin> W \<longrightarrow> h' a = h a)"

definition malloc_rel ::
  "nat \<Rightarrow> heap_mem \<Rightarrow> calg_allocator \<Rightarrow> addr \<Rightarrow>
    heap_mem \<Rightarrow> calg_allocator \<Rightarrow> bool" where
  "malloc_rel n h A p h' A' \<longleftrightarrow> allocator_wf A \<and> 0 < n \<and>
    ((p = 0 \<and> h' = h \<and> A' = A) \<or>
     (fresh_block A p n \<and> bytes_frame (block_bytes p n) h h' \<and>
      A' = A\<lparr>live_blocks := (live_blocks A)(p := Some n)\<rparr>))"

definition free_rel ::
  "addr \<Rightarrow> heap_mem \<Rightarrow> calg_allocator \<Rightarrow>
    heap_mem \<Rightarrow> calg_allocator \<Rightarrow> bool" where
  "free_rel p h A h' A' \<longleftrightarrow> allocator_wf A \<and>
    ((p = 0 \<and> h' = h \<and> A' = A) \<or>
     (p \<noteq> 0 \<and> (\<exists>n. live_blocks A p = Some n \<and>
       bytes_frame (block_bytes p n) h h') \<and>
      A' = A\<lparr>live_blocks := (live_blocks A)(p := None)\<rparr>))"

definition realloc_rel ::
  "addr \<Rightarrow> nat \<Rightarrow> heap_mem \<Rightarrow> calg_allocator \<Rightarrow>
    addr \<Rightarrow> heap_mem \<Rightarrow> calg_allocator \<Rightarrow> bool" where
  "realloc_rel p n h A q h' A' \<longleftrightarrow> allocator_wf A \<and> 0 < n \<and>
    (if p = 0 then malloc_rel n h A q h' A' else
      (\<exists>m. live_blocks A p = Some m \<and>
        ((q = 0 \<and> h' = h \<and> A' = A) \<or>
         (fresh_block (A\<lparr>live_blocks := (live_blocks A)(p := None)\<rparr>) q n \<and>
          (q = p \<or> block_bytes q n \<inter> block_bytes p m = {}) \<and>
          bytes_frame (block_bytes p m \<union> block_bytes q n) h h' \<and>
          (\<forall>i < min m n. h' (q + of_nat i) = h (p + of_nat i)) \<and>
          A' = A\<lparr>live_blocks := ((live_blocks A)(p := None))(q := Some n)\<rparr>))))"

text \<open>Preparation sanity checks only; no heap algorithm target is proved here.\<close>
lemma malloc_failure_available:
  "allocator_wf A \<Longrightarrow> 0 < n \<Longrightarrow> malloc_rel n h A 0 h A"
  by (simp add: malloc_rel_def)

lemma realloc_failure_available:
  "allocator_wf A \<Longrightarrow> 0 < n \<Longrightarrow> live_blocks A p = Some m \<Longrightarrow>
    realloc_rel p n h A 0 h A"
  by (auto simp: realloc_rel_def malloc_rel_def)

lemma malloc_success_available:
  "allocator_wf A \<Longrightarrow> fresh_block A p n \<Longrightarrow>
   malloc_rel n h A p h (A\<lparr>live_blocks := (live_blocks A)(p := Some n)\<rparr>)"
  by (auto simp: malloc_rel_def fresh_block_def block_ok_def bytes_frame_def)

end
