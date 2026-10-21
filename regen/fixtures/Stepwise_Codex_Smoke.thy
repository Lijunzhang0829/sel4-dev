theory Stepwise_Codex_Smoke
  imports Complex_Main
begin

lemma stepwise_codex_smoke: "rev (rev xs) = xs"
  by simp

end
