"""Unit tests for proof_parser.parse_proofs.

Run from host (no Isabelle needed):
    python3 .claude/skills/isabelle_prover/scripts-container/test_proof_parser.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from proof_parser import parse_proofs


def lines(s):
    return [line + "\n" for line in s.strip("\n").split("\n")]


def line_at(src, idx):
    return src[idx].rstrip("\n")


# ─── tests ───────────────────────────────────────────────────────────────

def test_one_line_by():
    src = lines("""
theory T imports Main begin
lemma foo: "x = x" by simp
end
""")
    ps = parse_proofs(src)
    assert len(ps) == 1, ps
    p = ps[0]
    assert p['name'] == 'foo'
    assert p['proof_start'] == p['proof_end'], f"one-liner: {p}"
    assert "by simp" in line_at(src, p['proof_end'])
    assert p['top_tactic'] == 'by'
    assert p['search_pressure'] == 'low'  # `simp` alone is low pressure


def test_apply_done():
    src = lines("""
theory T imports Main begin
lemma foo: "x = x"
  apply simp
  apply auto
  done
end
""")
    ps = parse_proofs(src)
    assert len(ps) == 1
    assert "done" in line_at(src, ps[0]['proof_end']), ps


def test_apply_terminated_by_by():
    src = lines("""
theory T imports Main begin
lemma foo: "x = x"
  apply simp
  by auto
end
""")
    ps = parse_proofs(src)
    assert len(ps) == 1
    assert "by auto" in line_at(src, ps[0]['proof_end']), ps


def test_isar_block_with_sub_proofs():
    src = lines("""
theory T imports Main begin
lemma foo: "x = x"
proof -
  have h1: "x = x" by simp
  have h2: "x = x" by (auto simp: foo_def)
  show ?thesis by (rule h1)
qed
end
""")
    ps = parse_proofs(src)
    parents = [p for p in ps if not p['is_subproof']]
    subs = [p for p in ps if p['is_subproof']]
    assert len(parents) == 1
    assert "qed" in line_at(src, parents[0]['proof_end']), parents[0]
    sub_names = sorted(s['name'] for s in subs)
    assert 'foo::h1' in sub_names, sub_names
    assert 'foo::h2' in sub_names, sub_names


def test_nested_proof_qed():
    """Inner qed must NOT close the outer proof."""
    src = lines("""
theory T imports Main begin
lemma foo: "x = x"
proof -
  have h1: "x = x"
  proof -
    show "x = x" by simp
  qed
  show ?thesis by (rule h1)
qed
end
""")
    ps = parse_proofs(src)
    parents = [p for p in ps if not p['is_subproof']]
    assert len(parents) == 1
    end_line = parents[0]['proof_end']
    # The outer qed appears LAST; assert we landed on it (last occurrence in body)
    qeds = [j for j in range(parents[0]['proof_start'], end_line + 1)
            if line_at(src, j).lstrip().startswith("qed")]
    assert len(qeds) == 2, f"expected 2 qeds in body, got {qeds}"
    assert qeds[-1] == end_line, (qeds, end_line)


def test_multiple_lemmas():
    src = lines("""
theory T imports Main begin
lemma foo: "x = x" by simp
lemma bar: "y = y"
  apply auto
  done
lemma baz: "z = z"
proof -
  show ?thesis by simp
qed
end
""")
    ps = parse_proofs(src, extract_subproofs=False)
    names = sorted(p['name'] for p in ps)
    assert names == ['bar', 'baz', 'foo'], names


def test_locale_lemma():
    src = lines("""
theory T imports Main begin
locale L = fixes x
lemma (in L) foo: "x = x" by simp
end
""")
    ps = parse_proofs(src)
    names = [p['name'] for p in ps]
    assert 'foo' in names, names


def test_attr_lemma():
    src = lines("""
theory T imports Main begin
lemma [simp] foo: "x = x" by simp
end
""")
    ps = parse_proofs(src)
    names = [p['name'] for p in ps]
    assert 'foo' in names, names


def test_multiline_by_with_parens():
    """`by (rule foo,\n simp add: bar)` spans two lines."""
    src = lines("""
theory T imports Main begin
lemma foo: "x = x"
  by (rule refl,
      simp)
lemma bar: "y = y" by simp
end
""")
    ps = parse_proofs(src, extract_subproofs=False)
    foo = next(p for p in ps if p['name'] == 'foo')
    bar = next(p for p in ps if p['name'] == 'bar')
    assert "simp)" in line_at(src, foo['proof_end']), foo
    assert foo['proof_end'] < bar['proof_start'], (foo, bar)


def test_unhinted_auto_is_high_pressure():
    src = lines("""
theory T imports Main begin
lemma foo: "x = x" by auto
lemma bar: "y = y" by (auto simp: foo)
lemma baz: "z = z" by (rule refl)
end
""")
    ps = {p['name']: p for p in parse_proofs(src)}
    assert ps['foo']['search_pressure'] == 'high', ps['foo']
    assert ps['bar']['search_pressure'] == 'medium', ps['bar']
    assert ps['baz']['search_pressure'] == 'low', ps['baz']


def test_pressure_scans_body_not_just_first_line():
    """A lemma with `using assms ... apply auto done` should be HIGH pressure
    because of the unhinted `auto` in the body — even though the first proof
    line is `using` which on its own is low pressure."""
    src = lines("""
theory T imports Main begin
lemma foo: "P"
  using assms
  apply auto
  done
lemma bar: "Q"
  unfolding bar_def
  apply (auto simp: foo_def)
  done
lemma baz: "R"
  unfolding baz_def
  apply (rule refl)
  done
end
""")
    ps = {p['name']: p for p in parse_proofs(src)}
    assert ps['foo']['search_pressure'] == 'high', ps['foo']      # unhinted auto
    assert ps['bar']['search_pressure'] == 'medium', ps['bar']    # hinted auto
    assert ps['baz']['search_pressure'] == 'low', ps['baz']       # rule, no search


def test_subproof_with_unhinted_auto():
    """Sub-proof of an Isar block with unhinted auto should be flagged high."""
    src = lines("""
theory T imports Main begin
lemma foo: "x"
proof -
  have h: "P" by auto
  show ?thesis by (rule h)
qed
end
""")
    subs = [p for p in parse_proofs(src) if p['is_subproof']]
    h = next(s for s in subs if s['name'] == 'foo::h')
    assert h['search_pressure'] == 'high', h


def test_isar_after_preamble():
    """Isar block opened after `using assms` preamble — sub-proofs must still
    be extracted (the original code only fired when top_tactic == 'proof')."""
    src = lines("""
theory T imports Main begin
lemma foo: "x"
  using assms
proof -
  have h1: "P" by simp
  have h2: "Q" by auto
  show ?thesis by (rule h1)
qed
end
""")
    subs = [p for p in parse_proofs(src) if p['is_subproof']]
    names = sorted(s['name'] for s in subs)
    assert 'foo::h1' in names, names
    assert 'foo::h2' in names, names


def test_multiline_have_body():
    """`have h: "..."\\n  by tac` (proof on next line) must be detected."""
    src = lines("""
theory T imports Main begin
lemma foo: "x"
proof -
  have h1: "P x"
    by simp
  have h2: "Q x"
    apply auto
    done
  show ?thesis by (rule h1)
qed
end
""")
    subs = [p for p in parse_proofs(src) if p['is_subproof']]
    names = sorted(s['name'] for s in subs)
    assert 'foo::h1' in names, names
    assert 'foo::h2' in names, names
    h1 = next(s for s in subs if s['name'] == 'foo::h1')
    h2 = next(s for s in subs if s['name'] == 'foo::h2')
    # h1 spans 2 lines (have + by)
    assert h1['size'] == 2, h1
    # h2 spans 3 lines (have + apply + done)
    assert h2['size'] == 3, h2


def test_nested_isar_in_have():
    """`have h: "..." proof - ... qed` — nested Isar."""
    src = lines("""
theory T imports Main begin
lemma foo: "x"
proof -
  have h: "P"
  proof -
    show "P" by simp
  qed
  show ?thesis by (rule h)
qed
end
""")
    subs = [p for p in parse_proofs(src) if p['is_subproof']]
    h = next((s for s in subs if s['name'] == 'foo::h'), None)
    assert h is not None, subs
    # h spans from `have h: "P"` to its `qed`
    assert "qed" in line_at(src, h['proof_end']), h


def test_comment_with_lemma_inside():
    """Commented-out lemma must not be detected."""
    src = lines("""
theory T imports Main begin
(* lemma fake_foo: "x = x" by simp *)
lemma real_foo: "y = y" by simp
end
""")
    ps = parse_proofs(src)
    names = [p['name'] for p in ps]
    assert names == ['real_foo'], names


def test_theorem_kind():
    src = lines("""
theory T imports Main begin
theorem t1: "x = x" by simp
corollary c1: "y = y" by simp
schematic_goal s1: "?P" by simp
end
""")
    ps = parse_proofs(src)
    kinds = {p['name']: p['kind'] for p in ps}
    assert kinds == {'t1': 'theorem', 'c1': 'corollary', 's1': 'schematic_goal'}, kinds


# ─── runner ──────────────────────────────────────────────────────────────

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
