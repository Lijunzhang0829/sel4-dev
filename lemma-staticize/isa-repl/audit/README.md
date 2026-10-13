# Credibility audit — "search-elimination has no payoff in seL4"

A self-critical review of whether the 2026-06 experiment actually supports its
claim. Each block is a separate file; block 5 holds one modification log per
attempted lemma so a reviewer can judge every search by its actual attempts.

| block | file | question |
|--|--|--|
| 1 | [block1-claim-and-credibility.md](block1-claim-and-credibility.md) | what is claimed; what would make it credible |
| 2 | [block2-scripts.md](block2-scripts.md) | are the scripts measuring what they claim? |
| 3 | [block3-dataset.md](block3-dataset.md) | is the candidate set right / unbiased? |
| 4 | [block4-loop-design.md](block4-loop-design.md) | search adequacy + gate validity (NO-PATH semantics) |
| 5 | [block5-lemma-logs/SUMMARY.md](block5-lemma-logs/SUMMARY.md) | per-lemma: what was tried, with full attempt lists |
| 6 | [block6-threats-and-verdict.md](block6-threats-and-verdict.md) | threats to validity + overall credibility verdict |

**One-line bottom line (see block 6 for the argument):** the experiment is
**strong evidence** that *this method, within its search budget, finds no
static-izable speedup on the candidates tested*; it is **only a strong
suggestion**, not a proof, that *no static-izable speedup exists anywhere in seL4*
— chiefly because several LLM searches were shallow (2–10 attempts vs cap 30) and
only 8 lemmas were run through the loop.
