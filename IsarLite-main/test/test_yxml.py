"""
Tests for YXML encode/decode.
"""

import pytest

from isarlite.base import Markup, YXMLElemModel, YXMLParseError


@pytest.mark.unit
class TestYXMLCodec:
    """
    YXML string encoding and decoding roundtrip.
    """

    def test_empty_body(self) -> None:
        assert YXMLElemModel.parse_many("") == []

    def test_text_only(self) -> None:
        body = YXMLElemModel.parse_many("hello world")
        assert len(body) == 1
        assert isinstance(body[0], str)
        assert body[0] == "hello world"

    def test_single_element(self) -> None:
        body = YXMLElemModel.parse_many(
            f"{YXMLElemModel.XY}name{YXMLElemModel.Y}attr=val{YXMLElemModel.X}content{YXMLElemModel.XYX}"
        )
        assert len(body) == 1
        elem = body[0]
        assert isinstance(elem, YXMLElemModel)
        assert elem.name == "name"
        assert dict(elem.attrs) == {"attr": "val"}
        assert len(elem.body) == 1
        assert isinstance(elem.body[0], str)
        assert elem.body[0] == "content"

    def test_roundtrip_text(self) -> None:
        original: list[YXMLElemModel | str] = ["hello"]
        encoded = "".join(YXMLElemModel.to_str(e) for e in original)
        decoded = YXMLElemModel.parse_many(encoded)
        assert len(decoded) == 1
        assert isinstance(decoded[0], str)
        assert decoded[0] == "hello"

    def test_roundtrip_elem(self) -> None:
        original: list[YXMLElemModel | str] = [
            YXMLElemModel(name="test", attrs={"a": "1"}, body=["body"])
        ]
        encoded = "".join(YXMLElemModel.to_str(e) for e in original)
        decoded = YXMLElemModel.parse_many(encoded)
        assert len(decoded) == 1
        elem = decoded[0]
        assert isinstance(elem, YXMLElemModel)
        assert elem.name == "test"
        assert dict(elem.attrs) == {"a": 1}
        assert YXMLElemModel.content_of(elem.body) == "body"

    def test_unbalanced_pop(self) -> None:
        with pytest.raises(YXMLParseError):
            YXMLElemModel.parse_many(str(YXMLElemModel.Y))

    def test_node_encoding(self) -> None:
        """
        The ``<:>`` node wrapper used by protocol encoders.
        """
        # Correct YXML: <:>X</:> = XY + name + X + body + XYX
        #                = \x05\x06 + ":" + \x05 + "X" + \x05\x06\x05
        body = YXMLElemModel.parse_many(
            f"{YXMLElemModel.XY}:{YXMLElemModel.X}X{YXMLElemModel.XYX}"
        )
        assert len(body) == 1
        elem = body[0]
        assert isinstance(elem, YXMLElemModel)
        assert elem.name == ":"
        assert len(elem.body) == 1
        assert isinstance(elem.body[0], str)
        assert elem.body[0] == "X"

    def test_node_roundtrip(self) -> None:
        """node() wrapper roundtrip via yxml_string_of_body."""
        node = YXMLElemModel(name=":", body=["hi"])
        enc = "".join(YXMLElemModel.to_str(e) for e in [node])
        decoded = YXMLElemModel.parse_many(enc)
        assert len(decoded) == 1
        assert isinstance(decoded[0], YXMLElemModel)
        assert decoded[0].name == ":"
        assert YXMLElemModel.content_of(decoded[0].body) == "hi"


@pytest.mark.unit
class TestMessageParsing:
    """
    Protocol message parsing from raw chunks (new wire format only).
    """

    def _make_header(self, name: str, attrs: dict[str, str] | None = None) -> bytes:
        """
        Encode a YXML element header (name + attrs, no body).
        """
        a = attrs or {}
        parts: list[str] = ["\x05\x06", name]
        for k, v in a.items():
            parts.extend(["\x06", k, "=", v])
        parts.append("\x05")
        parts.append("\x05\x06\x05")
        return "".join(parts).encode()

    def test_parse_writeln(self) -> None:
        """
        A writeln message with text body.
        """
        chunks = [
            self._make_header("writeln"),
            YXMLElemModel(name=":", body=["hello world"]).model_dump_yxml().encode(),
        ]
        msg = Markup.model_validate(chunks)
        assert msg.name == "writeln"
        assert msg.attrs == {}
        assert YXMLElemModel.content_of(msg.body) == "hello world"

    def test_parse_init(self) -> None:
        """
        Init message.
        """
        chunks = [
            self._make_header("init"),
            YXMLElemModel(name=":", body=["Isabelle/ML"]).model_dump_yxml().encode(),
        ]
        msg = Markup.model_validate(chunks)
        assert msg.name == "init"

    def test_parse_protocol_function(self) -> None:
        """
        Protocol messages carry the function name in attributes.
        """
        chunks = [
            self._make_header("protocol", attrs={"function": "commands_accepted"})
        ]
        msg = Markup.model_validate(chunks)
        assert msg.name == "protocol"
        assert msg.attrs.get("function") == "commands_accepted"


@pytest.mark.unit
class TestContentOf:
    """
    content_of extracts text from YXML trees.
    """

    def test_flat_text(self) -> None:
        assert YXMLElemModel.content_of(["hello"]) == "hello"

    def test_concatenated(self) -> None:
        assert YXMLElemModel.content_of(["a", "b"]) == "ab"

    def test_elem_text(self) -> None:
        assert YXMLElemModel.content_of([YXMLElemModel(name=":", body=["x"])]) == "x"

    def test_nested_elems(self) -> None:
        tree = YXMLElemModel(
            name="root",
            body=[
                YXMLElemModel(name="a", body=["1"]),
                YXMLElemModel(name="b", body=["2"]),
            ],
        )
        assert YXMLElemModel.content_of([tree]) == "12"

    def test_real_prover_yxml(self) -> None:
        """
        Parse real YXML and extract text.
        """
        yxml = "\x05\x06:\x05Alice\x05\x06\x05"
        body = YXMLElemModel.parse_many(yxml)
        assert YXMLElemModel.content_of(body) == "Alice"

    def test_strips_typing_annotation(self) -> None:
        """
        content_of strips xml_body children of typing/sorting annotations.
        """
        tree = YXMLElemModel(
            name="xml_elem",
            attrs={"xml_name": "typing"},
            body=[
                YXMLElemModel(name="xml_body", body=["boolackloop_dom"]),
                "content",
            ],
        )
        assert YXMLElemModel.content_of([tree]) == "content"

    def test_strips_sorting_annotation(self) -> None:
        """
        Same for sorting annotations.
        """
        tree = YXMLElemModel(
            name="xml_elem",
            attrs={"xml_name": "sorting"},
            body=[
                YXMLElemModel(name="xml_body", body=["real_text"]),
                "",
            ],
        )
        assert YXMLElemModel.content_of([tree]) == ""

    def test_keeps_non_annotation_xml_elem(self) -> None:
        """
        Non-typing/sorting xml_elem passes through.
        """
        tree = YXMLElemModel(
            name="xml_elem",
            attrs={"xml_name": "other"},
            body=[
                YXMLElemModel(name="xml_body", body=["visible"]),
            ],
        )
        assert YXMLElemModel.content_of([tree]) == "visible"
