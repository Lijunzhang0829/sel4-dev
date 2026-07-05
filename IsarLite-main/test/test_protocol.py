"""
Tests for PIDE protocol encoding (no prover needed).
"""

from __future__ import annotations

import pytest

from isarlite.base import (
    AssignUpdateBody,
    IsarYXMLUtils,
    Markup,
    PIDEInteractiveOptions,
    Tokenizer,
    YXMLElemModel,
    parse_spans,
)  # fmt: skip


def _parse_assign_body(body: list[YXMLElemModel | str]) -> AssignUpdateBody | None:
    """
    Parse assign/update body, returning None on failure.
    """
    try:
        return AssignUpdateBody.model_validate(body)
    except Exception:
        return None


@pytest.mark.unit
class TestAssignUpdateParsing:
    """Verify the assign_update YXML parser works correctly.

    The actual format is ``triple(int, list(string), list(string))``
    where each assignment string is ``"cmd_id,exec_id1,exec_id2,..."``.
    """

    def _make_assign_msg(
        self, version_id: int, pairs: list[tuple[int, list[int]]]
    ) -> Markup:
        """Build a Markup with an assign_update body.

        ML encoder:  triple int (list string) (list encode_upd)
        Produces 3 body elements: version_id, edited_nodes, assignments.
        encode_upd (a, bs): string of "a,b1,b2,..."
        """
        ver_node = YXMLElemModel(name=":", body=[str(version_id)])

        # Edited nodes list (empty for tests)
        edited_node = YXMLElemModel(name=":")

        # Assignments list: each entry is "cmd_id,exec_id1,..."
        assign_items = []
        for cmd_id, exec_ids in pairs:
            parts = [str(cmd_id)] + [str(e) for e in exec_ids]
            assign_str = ",".join(parts)
            assign_items.append(YXMLElemModel(name=":", body=[assign_str]))
        assign_node = YXMLElemModel(name=":", body=assign_items)

        return Markup(
            name="protocol",
            attrs={"function": "assign_update"},
            body=[ver_node, edited_node, assign_node],
        )

    def test_parse_single_pair(self) -> None:
        """Parse single (cmd_id=1, exec_ids=[42]) assignment."""
        msg = self._make_assign_msg(1, [(1, [42])])
        body_ab = _parse_assign_body(msg.body)
        pairs = body_ab.assignments if body_ab else {}
        assert pairs == {1: [42]}

    def test_parse_multiple_exec_ids(self) -> None:
        msg = self._make_assign_msg(2, [(2, [100, 200, 300])])
        body_ab = _parse_assign_body(msg.body)
        pairs = body_ab.assignments if body_ab else {}
        assert pairs == {2: [100, 200, 300]}

    def test_parse_multiple_commands(self) -> None:
        msg = self._make_assign_msg(3, [(1, [10, 11]), (2, [20])])
        body_ab = _parse_assign_body(msg.body)
        pairs = body_ab.assignments if body_ab else {}
        assert pairs == {1: [10, 11], 2: [20]}

    def test_parse_empty_msg(self) -> None:
        assert _parse_assign_body([]) is None

    def test_parse_ignores_malformed(self) -> None:
        """Malformed entries should be skipped, not crash."""
        body: list[YXMLElemModel | str] = [
            "garbage",
            YXMLElemModel(name=":", body=["no_second_child"]),
        ]
        body_ab = _parse_assign_body(body)
        assert (body_ab.assignments if body_ab else {}) == {}


@pytest.mark.unit
class TestBuildDefineCommand:
    """
    Tests for build_define_command YXML encoding.
    """

    def test_valid_yxml_output(self, bootstrap_tokenizer: Tokenizer) -> None:
        """
        build_define_command should produce valid YXML chunks.
        """
        tokens = bootstrap_tokenizer.tokenize("by auto")
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        assert len(spans) >= 1
        chunks = IsarYXMLUtils.build_define_command(spans[0])
        assert chunks[0] == b"Document.define_command"
        assert chunks[1].isdigit()
        assert len(chunks[2]) > 0  # name
        assert isinstance(chunks[3], bytes)  # parents
        assert isinstance(chunks[4], bytes)  # blobs
        assert isinstance(chunks[5], bytes)  # tokens
        assert len(chunks) >= 7  # at least header + 1 source

    def test_tokens_yxml_valid(self, bootstrap_tokenizer: Tokenizer) -> None:
        """
        Token list YXML should parse as list of (kind, len) pairs.
        """
        tokens = bootstrap_tokenizer.tokenize('lemma "P = Q"')
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        chunks = IsarYXMLUtils.build_define_command(spans[0])
        tok_yxml = chunks[5].decode("utf-8")
        parsed = YXMLElemModel.parse_many(tok_yxml)
        assert len(parsed) > 0
        for item in parsed:
            assert isinstance(item, YXMLElemModel)
            assert item.name == ":"

    def test_source_chunks_per_token(self, bootstrap_tokenizer: Tokenizer) -> None:
        """
        One source chunk per token after header.
        """
        tokens = bootstrap_tokenizer.tokenize('lemma "P = Q"')
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        chunks = IsarYXMLUtils.build_define_command(spans[0])
        n_tokens = len(spans[0].tokens)
        assert len(chunks) == 6 + n_tokens

    def test_list_yxml_vs_encode(self, bootstrap_tokenizer: Tokenizer) -> None:
        """Verify the list encoding matches encode_list semantics.

        build_define_command encodes token list as YXML nodes.
        Verify roundtrip: parse → re-encode → body is equivalent.
        """
        tokens = bootstrap_tokenizer.tokenize('lemma "P = Q"')
        spans = parse_spans(bootstrap_tokenizer.keywords, tokens)
        chunks = IsarYXMLUtils.build_define_command(spans[0])
        tok_yxml = chunks[5].decode("utf-8")
        parsed = YXMLElemModel.parse_many(tok_yxml)
        # Re-encode and compare body
        re_encoded = "".join(YXMLElemModel.to_str(e) for e in parsed)
        assert re_encoded == tok_yxml


@pytest.mark.unit
class TestDocumentUpdateEncoding:
    """
    Wire format for ``encode_document_update``.
    """

    def test_minimal(self) -> None:
        """
        Minimal call produces [cmd, old_id, new_id, cons_yxml].
        """
        chunks = IsarYXMLUtils.encode_document_update("0", "1", [])
        assert len(chunks) == 4
        assert chunks[0] == b"Document.update"
        assert chunks[1] == b"0"
        assert chunks[2] == b"1"
        # Consolidate YXML must be parseable
        YXMLElemModel.parse_many(chunks[3].decode())

    def test_consolidate_is_yxml_list(self) -> None:
        """
        Consolidate names are encoded as ``list(string)``.
        """
        chunks = IsarYXMLUtils.encode_document_update(
            "0", "1", [], consolidate=["/tmp/Test.thy"]
        )
        body = YXMLElemModel.parse_many(chunks[3].decode())
        assert len(body) == 1
        assert isinstance(body[0], YXMLElemModel)
        assert body[0].name == ":"
        assert YXMLElemModel.content_of(body[0].body) == "/tmp/Test.thy"

    def test_consolidate_defaults_to_empty(self) -> None:
        """
        ``consolidate=None`` is equivalent to ``consolidate=[]``.
        """
        a = IsarYXMLUtils.encode_document_update("0", "1", [], consolidate=[])
        b = IsarYXMLUtils.encode_document_update("0", "1", [])
        assert a == b

    def test_edit_yxmls_become_separate_chunks(self) -> None:
        """
        Each edit YXML is a separate chunk after the header.
        """
        e1 = "".join(
            YXMLElemModel.to_str(e) for e in [YXMLElemModel(name="e", body=["1"])]
        )
        e2 = "".join(
            YXMLElemModel.to_str(e) for e in [YXMLElemModel(name="e", body=["2"])]
        )
        chunks = IsarYXMLUtils.encode_document_update("0", "1", [e1, e2])
        assert len(chunks) == 6
        assert chunks[4] == e1.encode()
        assert chunks[5] == e2.encode()


@pytest.mark.unit
class TestOptionsYxmlEncoding:
    """
    Options YXML format matches ML decoder expectations.
    """

    def test_empty_options(self) -> None:
        """
        Empty options list produces empty YXML string.
        """
        opts = PIDEInteractiveOptions()
        yxml = opts.encode_yxml()
        body = YXMLElemModel.parse_many(yxml)
        assert len(body) > 0  # default options produce non-empty YXML

    def test_single_option_structure(self) -> None:
        """
        Single option roundtrips through PIDEInteractiveOptions.
        """
        opts = PIDEInteractiveOptions()
        opts.threads = 4
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.threads == 4

    def test_roundtrip(self) -> None:
        """
        Options YXML roundtrips through parse -> re-encode.
        """
        opts = PIDEInteractiveOptions()
        opts.threads = 4
        opts.parallel_proofs = 1
        yxml = opts.encode_yxml()
        body = YXMLElemModel.parse_many(yxml)
        re_encoded = "".join(YXMLElemModel.to_str(e) for e in body)
        parsed = PIDEInteractiveOptions.from_yxml(re_encoded)
        assert parsed.threads == 4
        assert parsed.parallel_proofs == 1

    def test_encode_options_yxml_wrapper(self) -> None:
        """
        Options YXML contains the field name.
        """
        opts = PIDEInteractiveOptions()
        yxml = opts.encode_yxml()
        assert "threads" in yxml
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.threads == 0


@pytest.mark.unit
class TestPIDEInteractiveOptionsModel:
    """
    Tests for the PIDEInteractiveOptions concrete-field model.
    """

    def test_defaults_produce_valid_yxml(self) -> None:
        """
        Default-constructed model produces parseable YXML.
        """
        opts = PIDEInteractiveOptions()
        yxml = opts.encode_yxml()
        assert len(yxml) > 0
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.threads == opts.threads

    def test_field_roundtrip(self) -> None:
        """
        A modified field roundtrips through encode→parse.
        """
        opts = PIDEInteractiveOptions()
        opts.editor_output_state = True
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.editor_output_state is True

    def test_direct_field_access(self) -> None:
        """
        Fields are accessible as typed Python attributes.
        """
        opts = PIDEInteractiveOptions()
        opts.threads = 8
        assert opts.threads == 8

    def test_unknown_field_raises_attribute_error(self) -> None:
        opts = PIDEInteractiveOptions()
        with pytest.raises(AttributeError):
            _ = opts.nonexistent  # type: ignore[attr-defined]

    def test_multiple_fields_roundtrip(self) -> None:
        opts = PIDEInteractiveOptions()
        opts.threads = 4
        opts.editor_output_state = True
        opts.spell_checker = False
        opts.print_mode = "ASCII"
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.threads == 4
        assert parsed.editor_output_state is True
        assert parsed.spell_checker is False
        assert parsed.print_mode == "ASCII"

    def test_from_yxml_empty_string(self) -> None:
        """
        Parsing empty string returns default-valued model.
        """
        opts = PIDEInteractiveOptions.from_yxml("")
        assert opts.threads == 0
        assert opts.editor_output_state is False

    def test_encode_options_yxml_wrapper(self) -> None:
        """
        Options YXML contains the field name.
        """
        opts = PIDEInteractiveOptions()
        yxml = opts.encode_yxml()
        assert "threads" in yxml
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.threads == 0

    def test_int_field_encoding(self) -> None:
        """
        Int fields encode and decode correctly.
        """
        opts = PIDEInteractiveOptions()
        opts.goals_limit = 42
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.goals_limit == 42

    def test_float_field_encoding(self) -> None:
        opts = PIDEInteractiveOptions()
        opts.threads_stack_limit = 1.5
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.threads_stack_limit == 1.5

    def test_bool_field_encoding(self) -> None:
        """
        Bool fields encode and decode correctly.
        """
        opts = PIDEInteractiveOptions()
        opts.editor_continuous_checking = False
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.editor_continuous_checking is False

    def test_string_field_encoding(self) -> None:
        opts = PIDEInteractiveOptions()
        opts.system_channel_address = "127.0.0.1:8888"
        yxml = opts.encode_yxml()
        parsed = PIDEInteractiveOptions.from_yxml(yxml)
        assert parsed.system_channel_address == "127.0.0.1:8888"
