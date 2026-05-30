(* TEMPLATE: derivability.thy — the A'⟹A witness lemma.

   The _old aux lemma proves the OLD statement of the strengthened
   lemma, using the NEW strengthened lemma + a Hoare-monotonicity
   rule. Verifying this snippet via check-theory.sh confirms that
   anyone who previously consumed the OLD lemma can still get what
   they need from the NEW one — i.e. the change is a true
   strengthening, not just a different statement.

   The aux lemma sits in the SAME .thy file as the strengthened
   lemma (so it shares the session import context). This file is
   a stand-alone snippet for CI replay; the same body appears in
   patch.diff.

   Pattern → tactic template:
     C  by (rule hoare_pre[OF <new>]) simp
     A  by (rule hoare_strengthen_post[OF <new>]) <Q'→Q rule>
     A (E_R variant)
        by (rule hoare_strengthen_postE_R[OF <new>]) <rule>
     D  by (rule hoare_strengthen_post[OF <new>]) simp
     E  one aux per replaced component:
        by (rule hoare_post_imp[OF <op>_invs])
           (simp add: invs_def valid_state_def)
     B  SKIP — no old form exists for an additive lemma

   Replace the placeholders below with the actual lemma. *)

lemma <name>_old:
  "<verbatim old statement>"
  by <derivability tactic per pattern>
