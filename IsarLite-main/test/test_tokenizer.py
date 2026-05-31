"""
Tests for the Isabelle tokenizer (Token.explode replica).
"""

from __future__ import annotations

import pytest
from isarlite.base import (
    Tokenizer,
    Token,
    TokenType,
    parse_spans,
)


@pytest.mark.unit
class TestTokenizeAckermann:
    """
    Tokenize the full Ackermann.thy source with bootstrap keywords.
    """

    def test_returns_list_of_tokens(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        assert isinstance(tokens, list)
        assert len(tokens) > 0
        assert all(isinstance(t, Token) for t in tokens)

    def test_commands_detected(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        cmds = [t.source for t in tokens if t.kind_id == TokenType.COMMAND]
        assert "theory" in cmds
        assert "fun" in cmds
        assert "lemma" in cmds
        assert "theorem" in cmds
        assert "proof" in cmds
        assert "by" in cmds
        assert "qed" in cmds
        assert "text" in cmds
        assert "subsection" in cmds
        assert "end" in cmds

    def test_text_cartouche_content(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        """
        The first 'text' span should have a cartouche with a title.
        """
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        # Find the first cartouche
        cartouches = [t for t in tokens if t.kind_id == TokenType.CARTOUCHE]
        assert len(cartouches) > 0
        assert cartouches[0].source.startswith("\\<open>")

    def test_informal_comment(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        comments = [t for t in tokens if t.kind_id == TokenType.INFORMAL_COMMENT]
        # Ackermann.thy has a (* Title: ... *) comment at the top
        assert len(comments) >= 1
        assert (
            comments[0].source
            == "(*  Title:      HOL/Examples/Ackermann.thy\n    Author:     Larry Paulson\n*)"
        )


@pytest.mark.unit
class TestTokenizerDirect:
    """
    Direct tokenizer tests without mounting a theory file.
    """

    def test_simple_command(self, bootstrap_tokenizer: Tokenizer) -> None:
        tokens = bootstrap_tokenizer.tokenize("by auto")
        assert len(tokens) >= 2
        assert tokens[0].kind_id == TokenType.COMMAND
        assert tokens[0].source == "by"

    def test_lemma_keyword(self, bootstrap_tokenizer: Tokenizer) -> None:
        tokens = bootstrap_tokenizer.tokenize('lemma "x = x"')
        non_space = [t for t in tokens if t.kind_id != TokenType.SPACE]
        assert non_space[0].kind_id == TokenType.COMMAND
        assert non_space[0].source == "lemma"

    def test_quoted_string(self, bootstrap_tokenizer: Tokenizer) -> None:
        tokens = bootstrap_tokenizer.tokenize('lemma "P = Q"')
        strings = [t for t in tokens if t.kind_id == TokenType.STRING]
        assert len(strings) >= 1
        assert strings[0].source == '"P = Q"'

    def test_control_cartouche(self, bootstrap_tokenizer: Tokenizer) -> None:
        """
        Control symbols like \\<^term>... are KIND_CONTROL.
        """
        tokens = bootstrap_tokenizer.tokenize("text \\<^formal>\\<open>hello\\<close>")
        # The tokens: "text" (COMMAND), " " (SPACE), "\<^formal>" + cartouche (CONTROL)
        controls = [t for t in tokens if t.kind_id == TokenType.CONTROL]
        assert len(controls) >= 1

    def test_ident_long_ident(self, bootstrap_tokenizer: Tokenizer) -> None:
        tokens = bootstrap_tokenizer.tokenize(
            'fun ack :: "[nat, nat] \\<Rightarrow> nat"'
        )
        non_space = [t for t in tokens if t.kind_id != TokenType.SPACE]
        # "fun" is command, "ack" is ident, "::" is sym_ident, etc.
        assert len(non_space) >= 4
        assert non_space[1].source == "ack"
        assert non_space[1].kind_id == TokenType.IDENT


@pytest.mark.unit
class TestSpanParser:
    """
    Tests for the span parser (Outer_Syntax.parse_spans replica).
    """

    def test_parse_ackermann_spans(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        assert len(spans) > 0
        assert all(s.name for s in spans if s.kind not in ("", "ignored"))

    def test_major_commands_in_spans(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        span_names = [s.name for s in spans if s.name]
        assert "theory" in span_names
        assert "fun" in span_names
        assert "lemma" in span_names
        assert "theorem" in span_names
        assert "end" in span_names

    def test_lemma_span_position(
        self, ackermann_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        """
        The ackloop_dom_longer lemma is about 3/4 into the file.
        """
        tokens = bootstrap_tokenizer.tokenize(ackermann_source)
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        lemma_spans = [s for s in spans if s.name == "lemma"]
        assert len(lemma_spans) >= 1
        # The first lemma span should be at a significant offset (past the header + imports)
        assert lemma_spans[0].start > 500  # well past the header
        assert lemma_spans[0].start < len(ackermann_source) - 100  # not at the end

    def test_adhoc_overloading_roundtrip(
        self, adhoc_overloading_source: str, bootstrap_tokenizer: Tokenizer
    ) -> None:
        """
        Parsing into spans and concatenating sources reproduces the original.
        """
        tokens = bootstrap_tokenizer.tokenize(adhoc_overloading_source)
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        reconstructed = "".join(s.source for s in spans)
        assert reconstructed == adhoc_overloading_source
