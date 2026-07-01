theory iso_conjI_plain
  imports Main
begin

lemma t: "(P::bool) \<and> Q" by (rule conjI)
end
