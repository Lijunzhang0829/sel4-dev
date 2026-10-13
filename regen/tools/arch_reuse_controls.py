"""Synthetic scope-isolation controls; not repository proof candidates."""


def fixture():
    contexts = ['context begin interpretation Arch .']
    check = r'''ML_prf \<open>
      if Config.get @{context} show_types then error "local config leaked" else ();
      if can (Proof_Context.get_thms @{context}) "reuse_private_marker"
      then error "local fact leaked" else ();
      if can (Proof_Context.get_thms @{context}) "reuse_future"
      then error "future fact leaked" else ();
    \<close>'''
    nodes = [
        dict(id='first', name='reuse_first', context=contexts,
             statement='lemma reuse_first: "True"'),
        dict(id='second', name='reuse_second', context=contexts,
             statement='lemma reuse_second: "True"'),
        dict(id='third', name='reuse_future', context=contexts,
             statement='lemma reuse_future: "True"'),
        dict(id='attribute', name='reuse_attr', context=contexts,
             statement='lemma reuse_attr [simp]: "(n::nat) + 0 = n"'),
        dict(id='consumer', name='reuse_consumer', context=contexts,
             statement='lemma reuse_consumer: "(n::nat) + 0 = n"'),
    ]
    poison = r'''
local_setup \<open>fn lthy =>
  Local_Theory.map_contexts (fn level =>
    if level >= Local_Theory.level lthy - 1 then
      Config.put show_types true #>
      Proof_Context.put_thms false ("reuse_private_marker", SOME [@{thm TrueI}])
    else I) lthy\<close>
ML \<open>
  if Config.get @{context} show_types andalso
     can (Proof_Context.get_thms @{context}) "reuse_private_marker"
  then () else error "isolation control did not install local state"
\<close>
'''
    return ({'nodes': nodes, 'execution_order': [n['id'] for n in nodes],
             'accepted_order': [n['id'] for n in nodes]},
            {'first': check+'\nby simp\n'+poison, 'second': check+'\nby (rule reuse_first)',
             'third': 'by (rule reuse_second)', 'attribute': 'by simp',
             'consumer': 'by (rule reuse_attr)'})
