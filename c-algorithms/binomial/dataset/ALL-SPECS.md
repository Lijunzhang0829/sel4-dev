# 当前数据集全部声明

100 条活动目录声明：91 条生成目标、9 条背景目录声明。0054 已注释停用。背景声明未作为已证事实导入。

## spec_0001

固定整数最小堆配置下，同一payload与自身比较返回0且状态不变。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0001:
  "c_guard h \<Longrightarrow>
   heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<Longrightarrow>
   compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<Longrightarrow>
   c_guard (ptr_coerce v :: 32 signed word ptr) \<Longrightarrow>
   runs_to (binomial_heap_cmp' h v v) s Q = Q (Result 0) s"
```

直接定义：。

## spec_0002

fresh块的字节范围与任一已有活块分离。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0002:
  "fresh_block A p n \<Longrightarrow> live_blocks A q = Some m \<Longrightarrow>
    block_bytes p n \<inter> block_bytes q m = {}"
```

直接定义：。

## spec_0003

三节点具体输入替换任意堆typing描述符后，仍满足其稳定表示关系。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0003:
 "bn_rep (env_pop_s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' env_pop_s),d)\<rparr>)
   (Ptr 16) (block_bytes 256 8) env_pop_F {#(Ptr 260 :: unit ptr), Ptr 256, Ptr 256#}"
```

直接定义：`env_pop_F`, `env_pop_s`。

## spec_0004

真实扫描单步将索引加一，保持前缀最小性不变量及原状态。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0004:
  "bn_rep s h P F M \<Longrightarrow> unat i < length F \<Longrightarrow>
  bn_review_scan_prefix s F (unat i) (if j = 0xFFFFFFFF then None else Some (unat j)) \<Longrightarrow>
  bn_contract (bn_ds_scan_step h (i,j)) s (\<lambda>(i',j') t.
    t = s \<and> i' = i + 1 \<and>
    bn_review_scan_prefix s F (unat i') (if j' = 0xFFFFFFFF then None else Some (unat j')))"
```

直接定义：`bn_ds_scan_step`, `bn_review_scan_prefix`。

## spec_0005

指定插入中间布局及fresh条件下，将叶合并失败传播为堆合并不满足runs_to。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0005:
 assumes hh: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16 :: StrictC'_BinomialHeap_C ptr) = env_insert_heap"
 and hf: "h_val (hrs_mem (t_hrs_' s)) (Ptr 4096 :: StrictC'_BinomialHeap_C ptr) = StrictC'_BinomialHeap_C BINOMIAL_HEAP_TYPE_MIN binomial_heap_input.int_compare 1 (Ptr 4144) 1"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and r1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 96 :: StrictC'_BinomialTree_C ptr ptr) = Ptr 64"
 and r2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 4144 :: StrictC'_BinomialTree_C ptr ptr) = Ptr 160"
 and f1: "fresh_block (phantom_machine_state_' s) 192 16"
 and f2: "fresh_block ((phantom_machine_state_' s)\<lparr>live_blocks := (live_blocks (phantom_machine_state_' s))(192 := Some 16)\<rparr>) 224 24"
 and f3: "fresh_block ((phantom_machine_state_' s)\<lparr>live_blocks := (live_blocks (phantom_machine_state_' s))(192 := Some 16, 224 := Some 24)\<rparr>) 256 8"
 shows "\<not> runs_to (binomial_heap_merge' (Ptr 16) (Ptr 4096)) s Q"
```

直接定义：`env_insert_heap`, `env_insert_leaf`。

## spec_0006

guard、空闲字节范围和类型条件足以构造合法栈分配。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0006:
 fixes p :: "'a::mem_type ptr"
 assumes "c_guard p" "ptr_span p \<subseteq> S"
   "ptr_span p \<subseteq> stack_free d"
   "typ_uinfo_t TYPE('a) \<noteq> typ_uinfo_t TYPE(stack_byte)"
 shows "(p, ptr_force_type p d) \<in> stack_allocs 1 S TYPE('a) d"
```

直接定义：。

## spec_0007

同阶树合并成功时阶次加一、元素多重集合并，保持布局、引用图和存储关系；正常失败时恢复。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0007:
  "bn_r2_tree_merge_pre s h P T U N edges slots held \<Longrightarrow>
  bn_contract (binomial_tree_merge' h (bn_root T) (bn_root U)) s (\<lambda>r t.
    if r = NULL then bn_ds_restore s t else
    (\<exists>V N' edges'. bn_root V = r \<and> bn_rank V = bn_rank T + 1 \<and>
      bn_ds_tree_layout t P V \<and> distinct (bn_blocks t V) \<and>
      mset (bn_values t V) = mset (bn_values s T) + mset (bn_values s U) \<and>
      refcount_C (bn_tree t r) = 0 \<and>
      bn_ds_graph t P N' edges' slots held {r} \<and> bn_r2_graph_storage t N' slots \<and>
      N' = insert r N \<and> bn_r3_old_node_fields s N t N' \<and>
      bn_ds_storage_transition s (bn_ds_graph_blocks s N) t (bn_ds_graph_blocks t N')) \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s) \<and>
    (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a))"
```

直接定义：`bn_ds_graph`, `bn_ds_graph_blocks`, `bn_ds_restore`, `bn_ds_storage_transition`, `bn_ds_tree_layout`, `bn_r2_graph_storage`, `bn_r2_tree_merge_pre`, `bn_r3_old_node_fields`。

## spec_0008

在列出的合法、分离条件下，合并两个叶节点满足执行安全和正常可达性；不要求返回值一定非NULL。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0008:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow> c_guard h \<Longrightarrow>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<Longrightarrow>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<Longrightarrow>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
    bn_valid s P (BN p []) \<Longrightarrow> bn_valid s P (BN q []) \<Longrightarrow> p \<noteq> q \<Longrightarrow>
    live_blocks (phantom_machine_state_' s) (ptr_val h) = Some (size_of TYPE(StrictC'_BinomialHeap_C)) \<Longrightarrow>
    ptr_val h \<noteq> ptr_val p \<Longrightarrow> ptr_val h \<noteq> ptr_val q \<Longrightarrow>
    bn_contract (binomial_tree_merge' h p q) s (\<lambda>r t. True)"
```

直接定义：。

## spec_0009

栈就绪条件蕴含先堆头、后指针的两次合法栈分配选择存在。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0009:
  "bn_stack_ready S s \<Longrightarrow>
   \<exists>p d q e.
     (p,d) \<in> stack_allocs 1 S TYPE(StrictC'_BinomialHeap_C) (hrs_htd (t_hrs_' s)) \<and>
     (q,e) \<in> stack_allocs 1 S TYPE(StrictC'_BinomialTree_C ptr) d"
```

直接定义：。

## spec_0010

引用计数大于一时，unref只将其减一，保持其余字段、分配器及对象外字节。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0010:
  "c_guard p \<Longrightarrow> 1 < unat (refcount_C (bn_tree s p)) \<Longrightarrow>
    bn_contract (binomial_tree_unref' p) s (\<lambda>r t.
      bn_tree t p = (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree s p) - 1\<rparr> \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      bytes_frame (block_bytes (ptr_val p) (size_of TYPE(StrictC'_BinomialTree_C)))
        (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)))"
```

直接定义：。

## spec_0011

合法活块内、大小和索引在界内的数组元素，其地址范围包含在该块内。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0011:
  "allocator_wf A \<Longrightarrow>
   live_blocks A (ptr_val (p :: 'a::mem_type ptr)) = Some n \<Longrightarrow>
   size_of TYPE('a) * k \<le> n \<Longrightarrow> i < k \<Longrightarrow>
   ptr_span (p +\<^sub>p int i) \<subseteq> block_bytes (ptr_val p) n"
```

直接定义：。

## spec_0012

构造成功时得到空堆，失败时保持分配器；两种结果都保护已有对象和payload。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0012:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
    bn_contract calg_binomial_new' s (\<lambda>h t.
      (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a) \<and>
      (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow>
        live_blocks (phantom_machine_state_' t) p = Some n \<and>
        (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)) \<and>
      (if h = NULL then phantom_machine_state_' t = phantom_machine_state_' s else
        bn_rep t h P [] {#} \<and> roots_C (bn_heap t h) = NULL \<and>
        phantom_machine_state_' t = (phantom_machine_state_' s)\<lparr>live_blocks :=
          (live_blocks (phantom_machine_state_' s))(ptr_val h := Some (size_of TYPE(StrictC'_BinomialHeap_C)))\<rparr>))"
```

直接定义：。

## spec_0013

稳定独占树的根引用计数为1。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0013:
  "bn_valid s P T \<Longrightarrow> refcount_C (bn_tree s (bn_root T)) = 1"
```

直接定义：。

## spec_0014

稳定堆根槽数不超过32，且roots_length字段等于森林列表长度。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0014:
  "bn_rep s h P F M \<Longrightarrow>
    length F \<le> 32 \<and> unat (roots_length_C (bn_heap s h)) = length F"
```

直接定义：。

## spec_0015

在finish接口、元素多重集和精确计数一致时，恢复完整稳定堆表示。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0015:
  "bn_r2_finish_base s h P F N edges [] {#} \<Longrightarrow>
  unat (num_values_C (bn_heap s h)) = size M \<Longrightarrow>
  M = mset (bn_forest_values s F) \<Longrightarrow> bn_rep s h P F M"
```

直接定义：`bn_r2_finish_base`。

## spec_0016

固定整数最小堆比较器返回负数/零分别对应键小于/相等，且不改变状态。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0016:
  "c_guard h \<Longrightarrow>
    heap_type_C (bn_heap s h) = BINOMIAL_HEAP_TYPE_MIN \<Longrightarrow>
    compare_func_C (bn_heap s h) = binomial_heap_input.int_compare \<Longrightarrow>
    bn_payload P x \<Longrightarrow> bn_payload P y \<Longrightarrow>
    bn_contract (binomial_heap_cmp' h x y) s (\<lambda>r t.
      t = s \<and> ((r <s 0) \<longleftrightarrow> bn_key s x < bn_key s y) \<and>
      ((r = 0) \<longleftrightarrow> bn_key s x = bn_key s y))"
```

直接定义：。

## spec_0017

冻结的严格memcpy条件即使长度为零，也要求源、目的地址非零。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0017:
  "memcpy_ok A dst src 0 \<Longrightarrow> dst \<noteq> 0 \<and> src \<noteq> 0"
```

直接定义：。

## spec_0018

free模型不改变其他起始地址的活块记录。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0018:
  "free_rel p hp A hp' B \<Longrightarrow> q \<noteq> p \<Longrightarrow>
    live_blocks B q = live_blocks A q"
```

直接定义：。

## spec_0019

指定pop中间布局及分配状态下，将叶合并失败传播为堆合并不满足runs_to。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0019:
 assumes heap: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16) = env_pop_heap"
 and fake: "roots_C (h_val (hrs_mem (t_hrs_' s)) (Ptr 4096)) = Ptr 224"
   "roots_length_C (h_val (hrs_mem (t_hrs_' s)) (Ptr 4096)) = 1"
 and r1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 192) = (Ptr 64 :: StrictC'_BinomialTree_C ptr)"
 and r2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 224) = (Ptr 160 :: StrictC'_BinomialTree_C ptr)"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64) = env_pop_leaf 260"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160) = env_pop_leaf 256"
 and k1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 260) = (1 :: 32 signed word)"
 and k2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 256) = (0 :: 32 signed word)"
 and alloc: "phantom_machine_state_' s = env_pop_A"
 shows "\<not> runs_to (binomial_heap_merge' (Ptr 16) (Ptr 4096)) s Q"
```

直接定义：`env_pop_A`, `env_pop_heap`, `env_pop_leaf`。

## spec_0020

扩大允许写集，原frame关系仍成立。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0020:
  "bytes_frame W hp hp' \<Longrightarrow> W \<subseteq> V \<Longrightarrow> bytes_frame V hp hp'"
```

直接定义：。

## spec_0021

真实完整根扫描循环结束于数组末端，给出界内最小根下标，且状态不变。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0021:
  "bn_rep s h P F M \<Longrightarrow> M \<noteq> {#} \<Longrightarrow>
  bn_contract (bn_ds_scan h) s (\<lambda>(i,j) t.
   t = s \<and> unat i = length F \<and> unat j < length F \<and>
   (\<exists>T. F ! unat j = Some T \<and> value_C (bn_tree s (bn_root T)) \<in># M \<and>
    (\<forall>q \<in># M. bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q)))"
```

直接定义：`bn_ds_scan`。

## spec_0022

合法非空森林中存在一个根，其payload属于原多重集且具有最小键。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0022:
  "bn_rep s h P F M \<Longrightarrow> M \<noteq> {#} \<Longrightarrow>
    \<exists>i T. i < length F \<and> F ! i = Some T \<and>
      value_C (bn_tree s (bn_root T)) \<in># M \<and>
      (\<forall>q \<in># M. bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q)"
```

直接定义：。

## spec_0023

同一内存与自身满足任意写集的frame。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0023:
  "bytes_frame W hp hp"
```

直接定义：。

## spec_0024

满足guard且字节范围分离的类型化对象，写入一个不改变另一个的读取。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0024:
  "c_guard (p :: 'a::mem_type ptr) \<Longrightarrow>
   c_guard (q :: 'b::mem_type ptr) \<Longrightarrow>
   ptr_span p \<inter> ptr_span q = {} \<Longrightarrow>
   h_val (heap_update p v m) q = h_val m q"
```

直接定义：。

## spec_0025

在合法分配池中登记合法fresh块后，分配池仍良构。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0025:
  "allocator_wf A \<Longrightarrow> fresh_block A p n \<Longrightarrow>
   allocator_wf (A\<lparr>live_blocks := (live_blocks A)(p := Some n)\<rparr>)"
```

直接定义：。

## spec_0026

malloc模型正常返回非零地址时，登记相应大小的新块，并保持保留区域及其他块记录。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0026:
  "malloc_rel n hp A p hp' B \<Longrightarrow> p \<noteq> 0 \<Longrightarrow>
    live_blocks B p = Some n \<and> reserved_bytes B = reserved_bytes A \<and>
    (\<forall>q. q \<noteq> p \<longrightarrow> live_blocks B q = live_blocks A q)"
```

直接定义：。

## spec_0027

允许写集外的某个字节保持不变。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0027:
  "bytes_frame W hp hp' \<Longrightarrow> a \<notin> W \<Longrightarrow> hp' a = hp a"
```

直接定义：。

## spec_0028

由bind的安全义务和一个可达正常前缀结果，得到对应后缀的安全义务。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0028:
 "runs_to (f >>= k) s Q \<Longrightarrow> reaches f s (Result v) t \<Longrightarrow> runs_to (k v) t Q"
```

直接定义：。

## spec_0029

释放堆头、根数组和拥有的树存储，精确删除相应分配记录，保护payload及外部对象。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0029:
  "bn_rep s h P F M \<Longrightarrow> bn_contract (binomial_heap_free' h) s (\<lambda>r t.
    bn_frame s h P F t \<and> allocator_wf (phantom_machine_state_' t) \<and>
    live_blocks (phantom_machine_state_' t) =
      (\<lambda>p. if p \<in> set (bn_owned s h F) then None else live_blocks (phantom_machine_state_' s) p))"
```

直接定义：。

## spec_0030

没有任何可分配新树块时，插入返回0并保持原表示、外部内存和分配器。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0030:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow>
    P \<inter> \<S> = {} \<Longrightarrow>
    (\<And>p. \<not> fresh_block (phantom_machine_state_' s) p (size_of TYPE(StrictC'_BinomialTree_C))) \<Longrightarrow>
    bn_contract (binomial_heap_insert' h v) s (\<lambda>r t.
      r = 0 \<and> bn_rep t h P F M \<and> bn_frame s h P F t \<and>
      phantom_machine_state_' t = phantom_machine_state_' s)"
```

直接定义：。

## spec_0031

合法堆的根扫描在起点满足空前缀不变量。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0031:
  "bn_rep s h P F M \<Longrightarrow> bn_review_scan_prefix s F 0 None"
```

直接定义：`bn_review_scan_prefix`。

## spec_0032

树的元素数量等于1加各子树元素数量之和。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0032:
  "length (bn_values s (BN p cs)) = Suc (sum_list (map (\<lambda>T. length (bn_values s T)) cs))"
```

直接定义：。

## spec_0033

插入成功时增加一次给定元素并保持表示及存储关系；正常失败时保持原内容和活块记录；保护外部内存。

角色：verification_target；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0033:
  "bn_rep s h P F M \<Longrightarrow> bn_payload P v \<Longrightarrow>
    size M + 1 < 2 ^ 32 \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_insert' h v) s (\<lambda>r t.
      bn_frame s h P F t \<and>
      (if r = 0 then (\<exists>F'. bn_rep t h P F' M) \<and>
        live_blocks (phantom_machine_state_' t) = live_blocks (phantom_machine_state_' s)
       else \<exists>F'. bn_rep t h P F' (add_mset v M) \<and> bn_storage_transition s h F t F'))"
```

直接定义：。

## spec_0034

真实扫描在三节点具体输入上得到(末端索引2，最小根下标1)，且状态不变。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0034:
 "reaches (bn_ds_scan (Ptr 16))
   (env_pop_s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' env_pop_s),d)\<rparr>) (Result (2,1))
   (env_pop_s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' env_pop_s),d)\<rparr>)"
```

直接定义：`bn_ds_scan`, `env_pop_s`。

## spec_0035

由malloc与后继的安全义务，抽取任一允许的分配结果所对应的后继安全义务。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0035:
  "runs_to (malloc' n >>= k) s Q \<Longrightarrow>
   malloc_rel (unat n) (hrs_mem (t_hrs_' s)) (phantom_machine_state_' s) p m A \<Longrightarrow>
   runs_to (k (Ptr p))
     ((t_hrs_'_update (hrs_mem_update (\<lambda>_. m)) s)
       \<lparr>phantom_machine_state_' := A\<rparr>) Q"
```

直接定义：。

## spec_0036

calloc模型正常成功时，请求范围内各字节为零。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0036:
  "calloc_rel item_count item_width hp A p hp' B \<Longrightarrow> p \<noteq> 0 \<Longrightarrow>
    i < item_count * item_width \<Longrightarrow> hp' (p + of_nat i) = 0"
```

直接定义：。

## spec_0037

只修改分配器保留区域中的字节，不改变分离的活块数组元素读取。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0037:
  "allocator_wf A \<Longrightarrow>
   live_blocks A (ptr_val (p :: 'a::mem_type ptr)) = Some n \<Longrightarrow>
   size_of TYPE('a) * k \<le> n \<Longrightarrow> i < k \<Longrightarrow>
   S \<subseteq> reserved_bytes A \<Longrightarrow> bytes_frame S hp hp' \<Longrightarrow>
   h_val hp' (p +\<^sub>p int i) = h_val hp (p +\<^sub>p int i)"
```

直接定义：。

## spec_0038

非空堆扫描完整前缀后，不变量给出的候选根是全体元素的最小根。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0038:
  "bn_rep s h P F M \<Longrightarrow> M \<noteq> {#} \<Longrightarrow>
  bn_review_scan_prefix s F (length F) (Some j) \<Longrightarrow>
  \<exists>T. F ! j = Some T \<and> value_C (bn_tree s (bn_root T)) \<in># M \<and>
   (\<forall>q \<in># M. bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q)"
```

直接定义：`bn_review_scan_prefix`。

## spec_0039

空内容堆在分配器不变、仅修改保留区域字节时，保持原稳定表示。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0039:
  "bn_rep s h P F {#} \<Longrightarrow>
   phantom_machine_state_' t = phantom_machine_state_' s \<Longrightarrow>
   S \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
   bytes_frame S (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)) \<Longrightarrow>
   bn_rep t h P F {#}"
```

直接定义：。

## spec_0040

将某个根槽设为None后，剩余内容加上移除树的内容等于原内容。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0040:
  "i < length F \<Longrightarrow>
    mset (bn_forest_values s (F[i := None])) +
      mset (case_option [] (bn_values s) (F ! i)) = mset (bn_forest_values s F)"
```

直接定义：。

## spec_0041

在malloc的runs_to义务下，任一合法fresh成功分配结果必须满足指定后置条件。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0041:
  "runs_to (malloc' n) s Q \<Longrightarrow>
   fresh_block (phantom_machine_state_' s) p (unat n) \<Longrightarrow>
   Q (Result (Ptr p))
     ((t_hrs_'_update (hrs_mem_update (\<lambda>_. hrs_mem (t_hrs_' s))) s)
       \<lparr>phantom_machine_state_' :=
         (phantom_machine_state_' s)\<lparr>live_blocks :=
           (live_blocks (phantom_machine_state_' s))(p := Some (unat n))\<rparr>\<rparr>)"
```

直接定义：。

## spec_0042

从规定的merge后中间状态执行插入收尾：必要时加计数、unref临时树，恢复对应元素集合的稳定表示。 修订版要求临时slot由header和待释放图allocation之外的存储支持。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0042:
  "bn_r2_finish_base s h P G N edges [slot] {#} \<Longrightarrow>
  bn_r2_slot_supported s slot (insert (ptr_val h) (bn_ds_graph_blocks s N)) \<Longrightarrow>
  c_guard slot \<Longrightarrow> h_val (hrs_mem (t_hrs_' s)) slot = tree \<Longrightarrow>
  bn_ds_tree_layout s P (BN tree []) \<Longrightarrow> value_C (bn_tree s tree) = v \<Longrightarrow>
  unat (num_values_C (bn_heap s h)) = size M \<Longrightarrow> size M + 1 < 2^32 \<Longrightarrow>
  mset (bn_forest_values s G) = (if result = 0 then M else add_mset v M) \<Longrightarrow>
  bn_contract (bn_ds_insert_finish h slot result) s (\<lambda>r t.
    r = result \<and> bn_rep t h P G (if result = 0 then M else add_mset v M) \<and>
    bn_ds_storage_transition s
      (insert (ptr_val h) (insert (ptr_val (roots_C (bn_heap s h))) (bn_ds_graph_blocks s N)))
      t (set (bn_owned t h G)) \<and>
    hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s))"
```

直接定义：`bn_ds_graph_blocks`, `bn_ds_insert_finish`, `bn_ds_storage_transition`, `bn_ds_tree_layout`, `bn_r2_finish_base`, `bn_r2_slot_supported`。

## spec_0043

指定相同payload叶节点布局及fresh分配条件下，叶合并不满足runs_to安全义务。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0043:
 assumes hh: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16 :: StrictC'_BinomialHeap_C ptr) = env_insert_heap"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160 :: StrictC'_BinomialTree_C ptr) = env_insert_leaf"
 and f1: "fresh_block (phantom_machine_state_' s) 224 24"
 and f2: "fresh_block ((phantom_machine_state_' s)\<lparr>live_blocks := (live_blocks (phantom_machine_state_' s))(224 := Some 24)\<rparr>) 256 8"
 shows "\<not> runs_to (binomial_tree_merge' (Ptr 16) (Ptr 64) (Ptr 160)) s Q"
```

直接定义：`env_insert_heap`, `env_insert_leaf`。

## spec_0044

所有合法栈分配/初始化及退出清理分支的函数体合同，可组装完整栈包装合同。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0044:
  "stack_allocs 1 \<S> TYPE('a::mem_type) (hrs_htd (t_hrs_' s)) \<noteq> {} \<Longrightarrow>
   (\<And>p d v bs.
      (p,d) \<in> stack_allocs 1 \<S> TYPE('a) (hrs_htd (t_hrs_' s)) \<Longrightarrow>
      length bs = size_of TYPE('a) \<Longrightarrow>
      bn_contract ((c :: 'a ptr \<Rightarrow> ('e::default, 'b, globals) spec_monad) p)
        (t_hrs_'_update (hrs_mem_update (heap_update_padding p v bs))
          (t_hrs_'_update (hrs_htd_update (\<lambda>_. d)) s))
        (\<lambda>r t. \<forall>bs'. length bs' = size_of TYPE('a) \<longrightarrow>
          Q r (t_hrs_'_update (hrs_mem_update (heap_update_list (ptr_val p) bs'))
            (t_hrs_'_update (hrs_htd_update (stack_releases 1 p)) t)))) \<Longrightarrow>
   bn_contract (globals.with_fresh_stack_ptr 1 (\<lambda>_. UNIV) c) s Q"
```

直接定义：。

## spec_0045

free模型释放0地址时，内存和分配器保持不变。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0045:
  "free_rel 0 hp A hp' B \<Longrightarrow> hp' = hp \<and> B = A"
```

直接定义：。

## spec_0046

与允许写集分离的整个区域保持不变。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0046:
  "bytes_frame W hp hp' \<Longrightarrow> P \<inter> W = {} \<Longrightarrow>
    \<forall>a \<in> P. hp' a = hp a"
```

直接定义：。

## spec_0047

树元素展开列表的首项就是根节点payload。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0047:
  "hd (bn_values s T) = value_C (bn_tree s (bn_root T))"
```

直接定义：。

## spec_0048

完整堆合并成功时建立元素并集及新森林/引用/存储接口；正常失败时恢复原状态关系。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0048:
  "bn_r2_merge_pre s h other P F G ext held N edges \<Longrightarrow>
  bn_contract (binomial_heap_merge' h other) s (\<lambda>r t.
   if r = 0 then bn_ds_restore s t
   else bn_r2_merge_success s h P F G ext held N t)"
```

直接定义：`bn_ds_restore`, `bn_r2_merge_pre`, `bn_r2_merge_success`。

## spec_0049

分配器不变且修改局限于与payload分离的保留区域时，建立API所需frame。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0049:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
   S \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
   P \<inter> S = {} \<Longrightarrow>
   phantom_machine_state_' t = phantom_machine_state_' s \<Longrightarrow>
   bytes_frame S (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)) \<Longrightarrow>
   bn_frame s h P F t"
```

直接定义：。

## spec_0050

严格memcpy以NULL为源时，其与任意后继的组合不满足runs_to安全义务，包括零长度。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0050:
  "\<not> runs_to (memcpy' dst NULL n >>= k) s Q"
```

直接定义：。

## spec_0051

稳定堆拥有块无重复，且包含堆头地址。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0051:
  "bn_rep s h P F M \<Longrightarrow> distinct (bn_owned s h F) \<and>
    ptr_val h \<in> set (bn_owned s h F)"
```

直接定义：。

## spec_0052

非空pop正常成功时返回最小元素并删除一次出现；正常失败时保持内容和活块记录；保护外部内存。

角色：verification_target；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0052:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_pop' h) s (\<lambda>r t. bn_frame s h P F t \<and>
      (if r = NULL then (\<exists>F'. bn_rep t h P F' M) \<and>
        live_blocks (phantom_machine_state_' t) = live_blocks (phantom_machine_state_' s)
       else r \<in># M \<and> (\<forall>q \<in># M. bn_key s r \<le> bn_key s q) \<and>
         (\<exists>F'. bn_rep t h P F' (M - {#r#}) \<and> bn_storage_transition s h F t F')))"
```

直接定义：。

## spec_0053

稳定堆可建立真实物理子边、根槽、引用计数账本及图存储关系。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0053:
  "bn_rep s h P F M \<Longrightarrow>
  bn_ds_graph s P (bn_ds_forest_nodes F) (bn_ds_physical_edges s)
    (bn_ds_slots (roots_C (bn_heap s h)) (length F)) {#} {} \<and>
  bn_r2_graph_storage s (bn_ds_forest_nodes F)
    (bn_ds_slots (roots_C (bn_heap s h)) (length F))"
```

直接定义：`bn_ds_forest_nodes`, `bn_ds_graph`, `bn_ds_physical_edges`, `bn_ds_slots`, `bn_r2_graph_storage`。

## spec_0055

前段合同与所有正常结果上的后段合同，可组合成bind合同。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0055:
  "bn_contract f s P \<Longrightarrow>
   (\<And>v t. P v t \<Longrightarrow> bn_contract (g v) t Q) \<Longrightarrow>
   bn_contract (f >>= g) s Q"
```

直接定义：。

## spec_0056

最后一次unref回收整棵独占树及子数组，精确删除拥有块记录，保护payload和其他活块。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0056:
  "allocator_wf (phantom_machine_state_' s) \<Longrightarrow>
    P \<subseteq> reserved_bytes (phantom_machine_state_' s) \<Longrightarrow>
    bn_valid s P T \<Longrightarrow> distinct (bn_blocks s T) \<Longrightarrow>
    bn_contract (binomial_tree_unref' (bn_root T)) s (\<lambda>r t.
      allocator_wf (phantom_machine_state_' t) \<and>
      reserved_bytes (phantom_machine_state_' t) = reserved_bytes (phantom_machine_state_' s) \<and>
      (\<forall>a \<in> P. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a) \<and>
      live_blocks (phantom_machine_state_' t) =
        (\<lambda>p. if p \<in> set (bn_blocks s T) then None else live_blocks (phantom_machine_state_' s) p) \<and>
      (\<forall>p n. live_blocks (phantom_machine_state_' s) p = Some n \<longrightarrow>
        p \<notin> set (bn_blocks s T) \<longrightarrow>
        (\<forall>a \<in> block_bytes p n. hrs_mem (t_hrs_' t) a = hrs_mem (t_hrs_' s) a)))"
```

直接定义：。

## spec_0057

合法树的根键不大于该树中任一元素的键。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0057:
  "bn_valid s P T \<Longrightarrow> q \<in> set (bn_values s T) \<Longrightarrow>
    bn_key s (value_C (bn_tree s (bn_root T))) \<le> bn_key s q"
```

直接定义：。

## spec_0058

树根地址属于该树的拥有块集合。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0058:
  "ptr_val (bn_root T) \<in> set (bn_blocks s T)"
```

直接定义：。

## spec_0059

指定不同键叶节点布局及分配状态下，叶合并不满足runs_to安全义务。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0059:
 assumes heap: "h_val (hrs_mem (t_hrs_' s)) (Ptr 16) = env_pop_heap"
 and t1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 64) = env_pop_leaf 260"
 and t2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 160) = env_pop_leaf 256"
 and k1: "h_val (hrs_mem (t_hrs_' s)) (Ptr 260) = (1 :: 32 signed word)"
 and k2: "h_val (hrs_mem (t_hrs_' s)) (Ptr 256) = (0 :: 32 signed word)"
 and alloc: "phantom_machine_state_' s = env_pop_A\<lparr>live_blocks := (live_blocks env_pop_A)(288 := Some 24)\<rparr>"
 shows "\<not> runs_to (binomial_tree_merge' (Ptr 16) (Ptr 64) (Ptr 160)) s Q"
```

直接定义：`env_pop_A`, `env_pop_heap`, `env_pop_leaf`。

## spec_0060

假定merge已恢复到摘根后的状态，pop失败后写回原根，返回NULL，恢复原内容、frame、分配器和typing。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0060:
  "bn_rep entry h P F M \<Longrightarrow> i < length F \<Longrightarrow> F ! i = Some T \<Longrightarrow>
  bn_ds_restore (bn_review_clear_root entry h i) s \<Longrightarrow>
  bn_contract (bn_ds_pop_restore h (of_nat i) (bn_root T)) s (\<lambda>r t.
   r = NULL \<and> bn_rep t h P F M \<and> bn_frame entry h P F t \<and>
   phantom_machine_state_' t = phantom_machine_state_' entry \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' entry))"
```

直接定义：`bn_ds_pop_restore`, `bn_ds_restore`, `bn_review_clear_root`。

## spec_0061

不同允许写集的两次frame关系，串接后的允许写集为二者并集。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0061:
  "bytes_frame W hp hp' \<Longrightarrow> bytes_frame V hp' hp'' \<Longrightarrow>
    bytes_frame (W \<union> V) hp hp''"
```

直接定义：。

## spec_0062

进位循环每一步保持完整中间不变量并推进索引；异常时恢复，且存在执行结果。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0062:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_r2_carry_inv s0 h other P F G ext held N a n (bn_carry,i,len,vals) s \<Longrightarrow>
  unat i < n \<Longrightarrow>
  runs_to (bn_ds_carry_loop_step h other (of_nat n) a (bn_carry,i,len,vals)) s
   (\<lambda>r t. case r of Result state \<Rightarrow>
       bn_r2_carry_inv s0 h other P F G ext held N a n state t \<and>
       (\<exists>c l vs. state = (c,i+1,l,vs))
     | Exn e \<Rightarrow> e = 0 \<and> bn_ds_restore s0 t) \<and>
  (\<exists>r t. reaches (bn_ds_carry_loop_step h other (of_nat n) a (bn_carry,i,len,vals)) s r t)"
```

直接定义：`bn_ds_carry_loop_step`, `bn_ds_restore`, `bn_r2_carry_inv`, `bn_r2_merge_pre`。

## spec_0063

在merge接口及新根数组分配关系下，初始carry=NULL、索引/长度为0时满足进位不变量。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0063:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_ds_roots_allocation s0 s a n \<Longrightarrow>
  bn_r2_carry_inv s0 h other P F G ext held N a n (NULL,0,0,vals) s"
```

直接定义：`bn_ds_roots_allocation`, `bn_r2_carry_inv`, `bn_r2_merge_pre`。

## spec_0064

由栈包装的安全义务和合法栈选择，得到对应函数体的安全性必要条件。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0064:
  "runs_to (globals.with_fresh_stack_ptr 1 (\<lambda>_. UNIV) f) s Q \<Longrightarrow>
   ((p :: 'a::xmem_type ptr),d) \<in>
     stack_allocs 1 \<S> TYPE('a) (hrs_htd (t_hrs_' s)) \<Longrightarrow>
   runs_to (f p) (s\<lparr>t_hrs_' := (hrs_mem (t_hrs_' s), d)\<rparr>) (\<lambda>_ _. True)"
```

直接定义：。

## spec_0065

对NULL执行tree_ref不改变状态。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0065:
  "bn_contract (binomial_tree_ref' NULL) s (\<lambda>r t. t = s)"
```

直接定义：。

## spec_0066

栈就绪且分离时，临时申请再释放堆头栈对象，保持稳定表示、外部frame、分配器和typing。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0066:
  "bn_rep s h P F M \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow> P \<inter> \<S> = {} \<Longrightarrow>
  bn_contract (globals.with_fresh_stack_ptr 1 (\<lambda>_. UNIV)
    (\<lambda>(p :: StrictC'_BinomialHeap_C ptr). return p)) s
    (\<lambda>p t. bn_rep t h P F M \<and> bn_frame s h P F t \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' s))"
```

直接定义：。

## spec_0067

函数合同与非空、满足后置的退出清理关系，可组合成on_exit合同。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0067:
  "bn_contract f s P \<Longrightarrow>
   (\<And>v t. P v t \<Longrightarrow> \<exists>u. (t,u) \<in> R) \<Longrightarrow>
   (\<And>v t u. P v t \<Longrightarrow> (t,u) \<in> R \<Longrightarrow> Q v u) \<Longrightarrow>
   bn_contract (on_exit f R) s Q"
```

直接定义：。

## spec_0068

完整森林进位循环正常结束满足最终接口；异常退出时恢复；并存在可达执行结果。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0068:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_ds_roots_allocation s0 s a n \<Longrightarrow>
  runs_to (bn_ds_carry_loop h other (of_nat n) a vals) s
    (\<lambda>r t. case r of Result result \<Rightarrow> bn_r2_carry_done s0 h P F G ext held N a n result t
      | Exn e \<Rightarrow> e = 0 \<and> bn_ds_restore s0 t) \<and>
  (\<exists>r t. reaches (bn_ds_carry_loop h other (of_nat n) a vals) s r t)"
```

直接定义：`bn_ds_carry_loop`, `bn_ds_restore`, `bn_ds_roots_allocation`, `bn_r2_carry_done`, `bn_r2_merge_pre`。

## spec_0069

执行真实摘根及fake-heap设置片段后，建立借用子树的merge前提，保持分配器和typing。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0069:
  "bn_rep entry h P F M \<Longrightarrow> i < length F \<Longrightarrow> F ! i = Some T \<Longrightarrow>
  c_guard fake \<Longrightarrow> root_ptr_valid (hrs_htd (t_hrs_' entry)) fake \<Longrightarrow>
  ptr_span fake \<subseteq> \<S> \<Longrightarrow> \<S> \<subseteq> reserved_bytes (phantom_machine_state_' entry) \<Longrightarrow>
  P \<inter> \<S> = {} \<Longrightarrow>
  bn_contract (bn_ds_pop_detach h fake (of_nat i)) entry (\<lambda>r t.
   r = bn_root T \<and> phantom_machine_state_' t = phantom_machine_state_' entry \<and>
   hrs_htd (t_hrs_' t) = hrs_htd (t_hrs_' entry) \<and>
   (\<exists>N edges. bn_r2_merge_pre t h fake P (F[i := None]) (map Some (bn_ds_children T)) [] {#r#} N edges))"
```

直接定义：`bn_ds_children`, `bn_ds_pop_detach`, `bn_r2_merge_pre`。

## spec_0070

扫描空根槽时只增加扫描索引，候选最小根与内存状态不变。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0070:
  "bn_rep s h P F M \<Longrightarrow> unat i < length F \<Longrightarrow>
   F ! unat i = None \<Longrightarrow>
   bn_contract (bn_ds_scan_step h (i,j)) s (\<lambda>r t. r = (i + 1,j) \<and> t = s)"
```

直接定义：`bn_ds_scan_step`。

## spec_0071

相同允许写集的两次frame关系可以串接。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0071:
  "bytes_frame W hp hp' \<Longrightarrow> bytes_frame W hp' hp'' \<Longrightarrow>
    bytes_frame W hp hp''"
```

直接定义：。

## spec_0072

完整稳定堆表示蕴含模块使用的森林/堆头观察视图。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0072:
  "bn_rep s h P F M \<Longrightarrow> bn_ds_forest_view s h P F"
```

直接定义：`bn_ds_forest_view`。

## spec_0073

合法分配池中的fresh新块起始地址尚无活块记录。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0073:
  "allocator_wf A \<Longrightarrow> fresh_block A p n \<Longrightarrow> live_blocks A p = None"
```

直接定义：。

## spec_0074

稳定独占树表示蕴含模块使用的树布局视图。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0074:
  "bn_valid s P T \<Longrightarrow> bn_ds_tree_layout s P T"
```

直接定义：`bn_ds_tree_layout`。

## spec_0075

空堆pop返回NULL，保持空内容的稳定表示、frame和分配器。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0075:
  "bn_rep s h P F {#} \<Longrightarrow> bn_stack_ready \<S> s \<Longrightarrow>
    P \<inter> \<S> = {} \<Longrightarrow>
    bn_contract (binomial_heap_pop' h) s (\<lambda>r t.
      r = NULL \<and> bn_rep t h P F {#} \<and> bn_frame s h P F t \<and>
      phantom_machine_state_' t = phantom_machine_state_' s)"
```

直接定义：。

## spec_0076

malloc模型正常返回0时，内存和分配器保持不变。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0076:
  "malloc_rel n hp A 0 hp' B \<Longrightarrow> hp' = hp \<and> B = A"
```

直接定义：。

## spec_0077

森林拼接的元素多重集等于两个森林元素多重集之和。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0077:
  "mset (bn_forest_values s (F @ G)) = mset (bn_forest_values s F) + mset (bn_forest_values s G)"
```

直接定义：。

## spec_0078

树的元素展开列表非空。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0078:
  "bn_values s T \<noteq> []"
```

直接定义：。

## spec_0079

在明确64字节栈和原始byte typing下，任一合法堆头分配后仍能分配指针对象。

角色：background_reuse；环境：`reviewed-stack-locale`。

```isabelle
lemma spec_0079:
 assumes pd: "(p,d) \<in> stack_allocs 1 \<S> TYPE(StrictC'_BinomialHeap_C) stack_byte_typing"
 shows "stack_allocs 1 \<S> TYPE(StrictC'_BinomialTree_C ptr) d \<noteq> {}"
```

直接定义：。

## spec_0080

合同后置条件可以逻辑弱化。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0080:
  "bn_contract f s P \<Longrightarrow>
   (\<And>v t. P v t \<Longrightarrow> Q v t) \<Longrightarrow>
   bn_contract f s Q"
```

直接定义：。

## spec_0081

合法树根的机器order字段等于结构阶次。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0081:
  "bn_valid s P T \<Longrightarrow> unat (order_C (bn_tree s (bn_root T))) = bn_rank T"
```

直接定义：。

## spec_0082

树合并满足执行合同；若正常返回NULL，则恢复原分配器、typing和原可读区域字节。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0082:
  "bn_r2_tree_merge_pre s h P T U N edges slots held \<Longrightarrow>
  bn_contract (binomial_tree_merge' h (bn_root T) (bn_root U)) s
    (\<lambda>r t. r = NULL \<longrightarrow> bn_ds_restore s t)"
```

直接定义：`bn_ds_restore`, `bn_r2_tree_merge_pre`。

## spec_0083

进位不变量和末端索引共同推出进位循环的最终接口。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0083:
  "bn_r2_merge_pre s0 h other P F G ext held N edges \<Longrightarrow>
  n = max (length F) (length G) + 1 \<Longrightarrow>
  bn_r2_carry_inv s0 h other P F G ext held N a n (bn_carry,i,len,vals) s \<Longrightarrow>
  unat i = n \<Longrightarrow>
  bn_r2_carry_done s0 h P F G ext held N a n (bn_carry,i,len,vals) s"
```

直接定义：`bn_r2_carry_done`, `bn_r2_carry_inv`, `bn_r2_merge_pre`。

## spec_0084

尚无候选根时遇到首个非空根槽，选中该下标并推进索引，状态不变。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0084:
  "bn_rep s h P F M \<Longrightarrow> unat i < length F \<Longrightarrow>
   F ! unat i = Some T \<Longrightarrow>
   bn_contract (bn_ds_scan_step h (i,0xFFFFFFFF)) s
     (\<lambda>r t. r = (i + 1,i) \<and> t = s)"
```

直接定义：`bn_ds_scan_step`。

## spec_0085

对象含padding写入和字节列表写入都满足给定栈区域之外的frame。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0085:
  "ptr_span (p :: 'a::mem_type ptr) \<subseteq> S \<Longrightarrow>
   length bs = size_of TYPE('a) \<Longrightarrow>
   bytes_frame S hp (heap_update_padding p v bs hp) \<and>
   bytes_frame S hp (heap_update_list (ptr_val p) bs hp)"
```

直接定义：。

## spec_0086

assume状态/结果关系非空且所有允许结果满足后置时，建立合同。

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0086:
  "R s \<noteq> {} \<Longrightarrow>
   (\<And>v t. (v,t) \<in> R s \<Longrightarrow> Q v t) \<Longrightarrow>
   bn_contract (assume_result_and_state R) s Q"
```

直接定义：。

## spec_0087

合法堆的计数读取API返回元素多重集大小，且不改变状态。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0087:
  "bn_rep s h P F M \<Longrightarrow> bn_contract (binomial_heap_num_entries' h) s
    (\<lambda>r t. unat r = size M \<and> t = s)"
```

直接定义：。

## spec_0088

guard成立且引用计数未到上界时，tree_ref将计数加一，保持分配器及对象外字节。

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0088:
  "c_guard p \<Longrightarrow> unat (refcount_C (bn_tree s p)) < 65535 \<Longrightarrow>
    bn_contract (binomial_tree_ref' p) s (\<lambda>r t.
      bn_tree t p = (bn_tree s p)\<lparr>refcount_C := refcount_C (bn_tree s p) + 1\<rparr> \<and>
      phantom_machine_state_' t = phantom_machine_state_' s \<and>
      bytes_frame (block_bytes (ptr_val p) (size_of TYPE(StrictC'_BinomialTree_C)))
        (hrs_mem (t_hrs_' s)) (hrs_mem (t_hrs_' t)))"
```

直接定义：。

## spec_0089

子引用秩严格下降并留在节点集

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0089:
 assumes G: "bn_ds_graph s P N edges slots held pending"
 and PN: "p \<in> N" and QE: "q \<in> set (edges p)"
 shows "q \<in> N \<and> unat (order_C (bn_tree s q)) < unat (order_C (bn_tree s p))"
```

直接定义：`bn_ds_graph`。

## spec_0090

引用图无环，支持递归释放终止

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0090:
 "bn_ds_graph s P N edges slots held pending \<Longrightarrow>
  acyclic {(x,y). x \<in> N \<and> y \<in> set (edges x)}"
```

直接定义：`bn_ds_graph`。

## spec_0091

可达节点具有正引用计数，排除下溢

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0091:
 assumes G: "bn_ds_graph s P N edges slots held {}" and PN: "p \<in> N"
 shows "0 < unat (refcount_C (bn_tree s p))"
```

直接定义：`bn_ds_graph`。

## spec_0092

逻辑槽位分组置换不改变引用图

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0092:
 "bn_ds_graph s P N edges (xs @ ys @ zs) held pending =
  bn_ds_graph s P N edges (ys @ xs @ zs) held pending"
```

直接定义：`bn_ds_graph`。

## spec_0093

完整allocation外部owner经存储转换保持

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0093:
 assumes support: "bn_r2_span_supported s W B"
 and transition: "bn_ds_storage_transition s B t C"
 shows "bn_r2_span_supported t W C"
```

直接定义：`bn_ds_storage_transition`, `bn_r2_span_supported`。

## spec_0094

写入当前槽位后的阶段给出完整undo前提

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0094:
 assumes ENTRY: "bn_r3_rollback_entry s0 h other P F G ext held N0 e0 a n i bn_carry s N edges"
 shows "unat i < 33 \<and> bn_ds_owned_array s a (unat i+1) \<and>
  bn_ds_graph s P N edges
    (bn_ds_slots a (unat i+1) @ (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ ext))
    (held + mset (filter (\<lambda>p. p\<noteq>NULL) [bn_carry])) {} \<and>
  bn_r2_graph_storage s N
    (bn_ds_slots a (unat i+1) @ (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ ext)) \<and>
  bn_r2_slots_supported s (bn_ds_slots (roots_C (bn_heap s0 h)) (length F) @ ext)
    (insert (ptr_val a) (bn_ds_graph_blocks s N)) \<and>
  ptr_val a\<notin>bn_ds_graph_blocks s N"
```

直接定义：`bn_ds_graph`, `bn_ds_graph_blocks`, `bn_ds_owned_array`, `bn_ds_slots`, `bn_r2_graph_storage`, `bn_r2_slots_supported`, `bn_r3_rollback_entry`。

## spec_0095

新失败分支片段与真实carry步骤等价

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0095:
 "bn_r3_carry_loop_step h other n a state = bn_ds_carry_loop_step h other n a state"
```

直接定义：`bn_ds_carry_loop_step`, `bn_r3_carry_loop_step`。

## spec_0096

共享引用尚未归零时实际unref只递减引用

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0096:
 assumes G: "c_guard p" and R: "1 < unat (refcount_C (bn_tree s p))"
 shows "bn_contract (binomial_tree_unref' p) s (\<lambda>r t. t = r2_dec s p)"
```

直接定义：`r2_dec`。

## spec_0097

实际leaf unref的保留与释放两个分支

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0097:
 assumes W: "allocator_wf (phantom_machine_state_' s)"
 and G: "c_guard p" and LP: "live_blocks (phantom_machine_state_' s) (ptr_val p)=Some 24"
 and ORDER: "order_C (bn_tree s p)=0" and ARRAY: "subtrees_C (bn_tree s p)=NULL"
 and POS: "0 < unat (refcount_C (bn_tree s p))"
 shows "bn_contract (binomial_tree_unref' p) s (\<lambda>r t.
   if refcount_C (bn_tree s p)=1 then
     bn_r3_release (r2_dec s p) {ptr_val p} {ptr_val p} t
   else t=r2_dec s p)"
```

直接定义：`bn_r3_release`, `r2_dec`。

## spec_0098

原森林加临时叶节点的精确节点集和引用数

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0098:
 assumes FB: "bn_r2_finish_base s h P F N edges [slot] {#}"
 and READ: "h_val (hrs_mem (t_hrs_' s)) slot = tree"
 and LEAF: "bn_ds_tree_layout s P (BN tree [])"
 shows "N = insert tree (bn_ds_forest_nodes F) \<and>
  (\<forall>q\<in>N. unat (refcount_C (bn_tree s q)) =
    (if q\<in>bn_ds_forest_nodes F then 1 else 0) + (if q=tree then 1 else 0))"
```

直接定义：`bn_ds_forest_nodes`, `bn_ds_tree_layout`, `bn_r2_finish_base`。

## spec_0099

直接字段和allocation frame保持森林布局和值

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0099:
  assumes L: "bn_ds_tree_layout s P T"
    and A: "\<forall>b\<in>set (bn_blocks s T). live_blocks (phantom_machine_state_' t) b = live_blocks (phantom_machine_state_' s) b"
    and HN: "\<forall>p \<in> set (bn_ds_node_list T). value_C (bn_tree t p)=value_C (bn_tree s p) \<and>
      order_C (bn_tree t p)=order_C (bn_tree s p) \<and> subtrees_C (bn_tree t p)=subtrees_C (bn_tree s p)"
    and HE: "\<forall>p \<in> set (bn_ds_node_list T). \<forall>i<unat (order_C (bn_tree s p)).
      h_val (hrs_mem (t_hrs_' t)) (subtrees_C (bn_tree s p) +\<^sub>p int i) =
      h_val (hrs_mem (t_hrs_' s)) (subtrees_C (bn_tree s p) +\<^sub>p int i)"
    and KEY: "\<forall>v :: unit ptr. bn_payload P v \<longrightarrow> bn_key t v = bn_key s v"
  shows "bn_ds_tree_layout t P T \<and> bn_values t T = bn_values s T \<and> bn_blocks t T = bn_blocks s T"
```

直接定义：`bn_ds_node_list`, `bn_ds_tree_layout`。

## spec_0100

整个insert收尾C片段的正常执行和存储效果

角色：verification_target；环境：`function-address-locale`。

```isabelle
lemma spec_0100:
 assumes W: "allocator_wf (phantom_machine_state_' s)"
 and GH: "c_guard h" and GP: "c_guard p"
 and LH: "live_blocks (phantom_machine_state_' s) (ptr_val h)=Some 40"
 and LP: "live_blocks (phantom_machine_state_' s) (ptr_val p)=Some 24"
 and NE: "ptr_val p\<noteq>ptr_val h"
 and SLOT: "bn_r2_slot_supported s slot {ptr_val h}"
 and READ: "h_val (hrs_mem (t_hrs_' s)) slot=p"
 and ORDER: "order_C (bn_tree s p)=0"
 and ARRAY: "subtrees_C (bn_tree s p)=NULL"
 and POS: "0 < unat (refcount_C (bn_tree s p))"
 shows "bn_contract (bn_ds_insert_finish h slot result) s (\<lambda>r t.
  r=result \<and> (if refcount_C (bn_tree s p)=1 then
   bn_r3_release (r2_dec (r3_count s h result) p) {ptr_val p} {ptr_val p} t
  else t=r2_dec (r3_count s h result) p))"
```

直接定义：`bn_ds_insert_finish`, `bn_r2_slot_supported`, `bn_r3_release`, `r2_dec`, `r3_count`。

## spec_0101

直接字段形式与旧整记录接口等价

角色：verification_target；环境：`global`。

```isabelle
lemma spec_0101:
 "bn_r3_old_node_fields s N t M = bn_ds_old_node_fields s N t M"
```

直接定义：`bn_ds_old_node_fields`, `bn_r3_old_node_fields`。
