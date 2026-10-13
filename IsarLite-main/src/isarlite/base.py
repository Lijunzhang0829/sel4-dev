"""
PIDE (Prover IDE) protocol client — direct Python communication with
the Isabelle/ML process using the shared PIDE protocol layer.

References:
  notes/pide.md                         — architecture overview
  Isabelle2025/src/Pure/PIDE/yxml.scala / yxml.ML             — YXML wire format
  Isabelle2025/src/Pure/PIDE/byte_message.scala / .ML    — message framing
  Isabelle2025/src/Pure/PIDE/xml.scala                        — XML.Encode / Decode
  Isabelle2025/src/Pure/System/isabelle_process.ML            — ML-side init & protocol loop
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from enum import IntEnum
from pathlib import Path
from typing import (
    Any,
    Callable,
    ClassVar,
    Self,
    TypedDict,
    cast,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    DirectoryPath,
    Field,
    FilePath,
    model_validator,
)
from returns.maybe import Maybe, Nothing, Some
from returns.result import Failure, Result, Success

# ── YXML primitives ─────────────────────────────────────────────────────────


class YXMLParseError(ValueError):
    """
    Malformed YXML input.
    """


type YXMLAttr = str | int | float | bool | None
"""
A YXML attribute value — eagerly coerced from raw string at parse time.
"""


class YXMLElemModel(BaseModel):
    """
    A YXML element: name, attributes, child body.
    """

    model_config = ConfigDict(frozen=True)

    # YXML delimiters
    X: ClassVar[str] = "\x05"
    Y: ClassVar[str] = "\x06"
    XY: ClassVar[str] = X + Y
    XYX: ClassVar[str] = X + Y + X

    name: str
    attrs: dict[str, YXMLAttr] = Field(default_factory=dict)
    body: list[YXMLElemModel | str] = Field(default_factory=list)

    @staticmethod
    def _coerce_attr(raw: str) -> YXMLAttr:
        """
        Coerce a raw attribute string to the most specific YXMLAttr type.
        """

        if raw == "true":
            return True
        if raw == "false":
            return False
        if not raw:
            return raw
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw

    def model_dump_yxml(self) -> str:
        """
        Serialize this element to YXML string.
        """

        parts: list[str] = [self.XY, self.name]
        for k, v in self.attrs.items():
            parts.extend([self.Y, k, "=", str(v)])
        parts.append(self.X)
        for child in self.body:
            parts.append(YXMLElemModel.to_str(child))
        parts.append(self.XYX)
        return "".join(parts)

    @classmethod
    def serialize_many(cls, items: list[YXMLElemModel | str]) -> str:
        """
        Serialize a list of YXML body items to a YXML string.
        """

        return "".join(cls.to_str(e) for e in items)

    @classmethod
    def parse_many(cls, source: str) -> list[YXMLElemModel | str]:
        """
        Parse a YXML string into a list of YXML tree nodes.
        """

        stack: list[YXMLElemModel] = [YXMLElemModel(name="")]

        def _add(item: YXMLElemModel | str) -> None:
            stack[-1].body.append(item)

        def _push(name: str, atts: dict[str, YXMLAttr]) -> None:
            if not name:
                raise YXMLParseError("bad element name")
            stack.append(YXMLElemModel(name=name, attrs=atts))

        def _pop() -> None:
            if len(stack) <= 1:
                raise YXMLParseError("unbalanced element")
            elem = stack.pop()
            _add(elem)

        def _parse_attrs(sub: list[str]) -> dict[str, YXMLAttr]:
            """
            Build attribute dict from the sub[2:] chunk parts.
            """

            atts: dict[str, YXMLAttr] = {}
            for a in sub[2:]:
                if "=" in a:
                    k, _, v = a.partition("=")
                    atts[k] = YXMLElemModel._coerce_attr(v)
                else:
                    raise YXMLParseError(f"bad attribute: {a!r}")
            return atts

        chunks = source.split(cls.X)
        for chunk in chunks:
            if not chunk:
                continue
            if chunk == cls.Y:
                _pop()
            else:
                sub = chunk.split(cls.Y)
                if sub[0] == "" and len(sub) >= 2:
                    name = sub[1]
                    _push(name, _parse_attrs(sub))
                else:
                    for txt in sub:
                        _add(txt)

        if len(stack) != 1:
            raise YXMLParseError(f"unbalanced element: {stack[-1].name}")
        return stack[0].body

    @staticmethod
    def to_str(item: YXMLElemModel | str) -> str:
        """
        Serialize one YXML body item: str passes through, element serializes.
        """

        return item if isinstance(item, str) else item.model_dump_yxml()

    @staticmethod
    def content_of(body: list[YXMLElemModel | str]) -> str:
        """Extract all text content from a YXML body, recursing into elements.

        Skips ``xml_body`` children of typing/sorting annotation elements
        (decorative type info from ``show_markup``, not displayed text).
        """

        def _walk_typed_children(
            children: list[YXMLElemModel | str],
        ) -> list[str]:
            """
            Walk children of typing/sorting elements, skipping ``xml_body``.
            """

            r: list[str] = []
            for child in children:
                match child:
                    case YXMLElemModel(name="xml_body"):
                        continue
                    case YXMLElemModel(body=cb):
                        r.extend(_walk(cb))
                    case _:
                        r.extend(_walk([child]))
            return r

        def _walk(nodes: list[YXMLElemModel | str]) -> list[str]:
            result: list[str] = []
            for node in nodes:
                match node:
                    case str(c):
                        result.append(c)
                    case YXMLElemModel(name="xml_elem", attrs=a, body=children):
                        if dict(a).get("xml_name") in ("typing", "sorting"):
                            result.extend(_walk_typed_children(children))
                        else:
                            result.extend(_walk(children))
                    case YXMLElemModel(body=children):
                        result.extend(_walk(children))
            return result

        return "".join(_walk(body))


# ── Type aliases
type ExecID = int  # Execution ID — numeric identifier from the prover
type CommandID = int  # Command ID — assigned sequentially to each command span
type VersionID = int  # Document version ID — incremented per Document.update
type StateStr = str  # Content from STATE markup (proof goals)
type InfoStr = str  # Content from INFORMATION markup (proof outline, etc.)
type PathStr = str  # A filesystem path used as a protocol/string value

# ---------------------------------------------------------------------------
# Isabelle symbol utilities
# ---------------------------------------------------------------------------
# Isabelle symbols are either ASCII characters or named \<foo> symbols.
# All offset/length operations count in symbol units, not code points.


def symbol_length(s: str) -> int:
    """Count Isabelle symbols in *s*.

    A named symbol like ``\\<forall>`` counts as 1 symbol.
    A control symbol like ``\\<^foo>`` counts as 1 symbol.
    Everything else counts per-grapheme.
    """
    i = 0
    n = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            m = _SYMBOL_PAT.match(s, i)
            if m:
                i = m.end()
                n += 1
                continue
        n += 1
        i += 1
    return n


_SYMBOL_PAT = re.compile(r"\\(?:<(?:\^?[A-Za-z_][A-Za-z_0-9]*)?>)")


# ---- symbol table (loaded from etc/symbols) --------------------------------


class SymbolTable:
    """
    Isabelle symbol table loaded from ``etc/symbols``.
    """

    LETTER = "letter"
    DIGIT = "digit"
    BLANK = "blank"
    SYMBOL = "symbol"
    CONTROL = "control"

    def __init__(self, path: Path) -> None:
        self.groups: dict[str, set[str]] = {}
        self._unicode: dict[str, str] = {}
        self._from_unicode: dict[str, str] = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "code:" not in line:
                continue
            parts = line.split()
            name = parts[0]
            groups_set: set[str] = set()
            for i, p in enumerate(parts):
                if p == "group:" and i + 1 < len(parts):
                    groups_set.add(parts[i + 1])
                if p == "code:" and i + 1 < len(parts):
                    try:
                        cp = int(parts[i + 1], 16)
                        self._unicode[name] = chr(cp)
                    except (ValueError, IndexError):
                        pass
            if groups_set:
                self.groups[name] = groups_set
        self._from_unicode = {ch: name for name, ch in self._unicode.items()}

    def is_letter(self, s: str) -> bool:
        """
        True if *s* is a letter (Unicode letter or named letter symbol).
        """

        if len(s) == 1 and s.isalpha():
            return True
        return self.LETTER in self.groups.get(s, set())

    @staticmethod
    def is_digit(s: str) -> bool:
        return len(s) == 1 and s.isdigit()

    def is_blank(self, s: str) -> bool:
        if s in (" ", "\t", "\n", "\r", "\f", ""):
            return True
        return self.BLANK in self.groups.get(s, set())

    def decode(self, text: str) -> str:
        """
        Decode Isabelle symbol names (``\\<name>``) to Unicode.
        """

        def _replace(m: re.Match[str]) -> str:
            sym = m.group(0)
            ch = self._unicode.get(sym)
            return ch if ch is not None else sym

        return re.sub(r"\\<([a-zA-Z_][a-zA-Z_0-9]*)>", _replace, text)

    def to_symbols(self, text: str) -> str:
        """
        Convert known Unicode chars back to ``\\<name>`` format.
        """

        for ch, name in self._from_unicode.items():
            if ch in text:
                text = text.replace(ch, name)
        return text

    def is_symbolic(self, s: str) -> bool:
        # Isabelle2025 sym_chars: ! # $ % & * + - / < = > ? @ ^ _ | ~
        # Note: do NOT include [ ] ( ) { } : ; , ` -- those are NOT symbolic
        if len(s) == 1 and s in "!#$%&*+-./<=>?@^_|~":
            return True
        return self.SYMBOL in self.groups.get(s, set())

    def is_control(self, s: str) -> bool:
        return self.CONTROL in self.groups.get(s, set())

    def is_letdig(self, s: str) -> bool:
        return self.is_letter(s) or self.is_digit(s) or s in ("_", "'")

    _symbols_path: ClassVar[Path] = (
        Path(__file__).parent.parent.parent / "refs/Isabelle2025/etc/symbols"
    )
    _default_table: ClassVar[SymbolTable | None] = None

    @classmethod
    def default(cls) -> SymbolTable:
        """
        Return the singleton ``SymbolTable``, loading from ``etc/symbols`` on first call.
        """

        if cls._default_table is None:
            cls._default_table = cls(cls._symbols_path)  # type: ignore[reportConstantRedefinition]
        return cls._default_table


# Lexicon — character-level trie for keyword matching
# ---------------------------------------------------------------------------
# Mirrors scan.scala's Lexicon: a tree of Map[Char, Node] with longest-match scanning.
#
# Usage:
#   lex = Lexicon()
#   lex.add("then")
#   lex.add("therefore")
#   lex.scan("therefore", 0)  -> "therefore" (longest match)


class Lexicon:
    """
    Character-level trie for keyword longest-match scanning.
    """

    class _Node:
        __slots__ = ("children", "value")

        def __init__(self) -> None:
            self.children: dict[str, Lexicon._Node] = {}
            self.value: str | None = None

    def __init__(self) -> None:
        self._root = Lexicon._Node()

    def add(self, word: str) -> None:
        """
        Insert *word* into the trie.
        """

        node = self._root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = Lexicon._Node()
            node = node.children[ch]
        node.value = word

    def scan(self, input: str, pos: int = 0) -> Maybe[str]:
        """Longest-match forward scan from *pos*.

        Returns the longest keyword found starting at *pos*, or None.
        """
        node = self._root
        best: str | None = None
        i = pos
        while i < len(input):
            ch = input[i]
            if ch in node.children:
                node = node.children[ch]
                i += 1
                if node.value is not None:
                    best = node.value
            else:
                break
        return Some(best) if best else Nothing


# ---------------------------------------------------------------------------
# Keyword table — bootstrap + full keyword management
# ---------------------------------------------------------------------------

# Scala Thy_Header.bootstrap_header — minimal set for theory header parsing.
# Empty kind (""), BEFORE_COMMAND, and QUASI_COMMAND go to the minor lexicon;
# all other kinds go to the major lexicon (COMMAND tokens).
_BOOTSTRAP_KEYWORDS: dict[str, str] = {
    # Punctuation (minor — empty kind)
    "%": "",
    "(": "",
    ")": "",
    ",": "",
    "::": "",
    "=": "",
    # Theory header keywords (minor — quasi_command: don't split the header)
    "and": "quasi_command",
    "begin": "quasi_command",
    "imports": "quasi_command",
    "keywords": "quasi_command",
    "abbrevs": "quasi_command",
    # Document heading commands (major)
    "chapter": "document_heading",
    "section": "document_heading",
    "subsection": "document_heading",
    "subsubsection": "document_heading",
    "paragraph": "document_heading",
    "subparagraph": "document_heading",
    # Document body commands (major)
    "text": "document_body",
    "txt": "document_body",
    "text_raw": "document_raw",
    # Theory structure (major)
    "theory": "thy_begin",
    "ML": "thy_decl",
}

# Common HOL commands not defined in Pure.thy.
# Stopgap: the proper approach is to parse HOL.thy's keyword declarations.
_HOL_EXTRA_KEYWORDS: dict[str, str] = {
    "fun": "thy_decl",
    "function": "thy_decl",
    "termination": "thy_decl",
    "inductive": "thy_decl",
    "coinductive": "thy_decl",
    "primrec": "thy_decl",
    "datatype": "thy_decl",
    "record": "thy_decl",
    "inductive_set": "thy_decl",
    "coinductive_set": "thy_decl",
    "nominal_datatype": "thy_decl",
    "nominal_primrec": "thy_decl",
    "rep_datatype": "thy_decl",
    "specification": "thy_decl",
    "ax_specification": "thy_decl",
    "code_datatype": "thy_decl",
}


class KeywordTable:
    """
    Keyword table with major/minor lexicons.
    """

    def __init__(
        self,
        kinds: dict[str, str] | None = None,
        major: Lexicon | None = None,
        minor: Lexicon | None = None,
    ) -> None:
        self.kinds: dict[str, str] = kinds if kinds is not None else {}
        self.major: Lexicon = major if major is not None else Lexicon()
        self.minor: Lexicon = minor if minor is not None else Lexicon()

    @staticmethod
    def is_major_kind(kind: str | None) -> bool:
        """
        True if *kind* makes a keyword a major command.
        """

        return bool(kind) and kind.lower() not in ("before_command", "quasi_command")

    def build(self) -> None:
        """
        Rebuild major/minor lexicons from current *kinds*.
        """

        self.major = Lexicon()
        self.minor = Lexicon()
        for name, kind in self.kinds.items():
            if self.is_major_kind(kind):
                self.major.add(name)
            else:
                self.minor.add(name)

    @classmethod
    def bootstrap(cls, isabelle_home: Path | None = None) -> "KeywordTable":
        """Create the keyword table by merging bootstrap, Pure.thy, and HOL extras.

        If *isabelle_home* is provided, parse Pure.thy for the authoritative
        keyword list.  Falls back to the Scala bootstrap set otherwise.
        """
        merged: dict[str, str] = dict(_BOOTSTRAP_KEYWORDS)
        if isabelle_home is not None:
            pure_kw = cls._parse_pure_keywords(isabelle_home)
            merged.update(pure_kw)  # Pure.thy overrides bootstrap kinds
        merged.update(_HOL_EXTRA_KEYWORDS)  # HOL adds new commands
        # Remove duplicates from the dict while preserving keys
        kt = cls(kinds=merged)
        kt.build()
        return kt

    def add(self, name: str, kind: str) -> None:
        """
        Add a single keyword.
        """

        self.kinds[name] = kind
        if self.is_major_kind(kind):
            self.major.add(name)
        else:
            self.minor.add(name)

    def lookup_kind(self, name: str) -> Maybe[str]:
        """
        Get the kind for a keyword.
        """

        v = self.kinds.get(name)
        return Some(v) if v else Nothing

    @staticmethod
    def _parse_pure_keywords(isabelle_home: Path) -> dict[str, str]:
        """Parse keyword declarations from ``Pure.thy``'s ``keywords ... begin`` block.

        Returns a dict mapping keyword names to their Isar command kinds.
        """
        pure_thy = isabelle_home / "src/Pure/Pure.thy"
        if not pure_thy.is_file():
            return {}
        text = pure_thy.read_text(encoding="utf-8")

        # Extract the keywords block (between "keywords" and "abbrevs"/"begin")
        kw_start = text.find("\nkeywords\n")
        if kw_start == -1:
            kw_start = text.find("\nkeywords ")
        if kw_start == -1:
            return {}
        # Find the end: "abbrevs" or (if no abbrevs) "begin"
        after = text[kw_start:]
        m_abbrevs = re.search(r"\nabbrevs\b", after)
        m_begin = re.search(r"\nbegin\b", after)
        kw_end = kw_start
        if m_abbrevs:
            kw_end += m_abbrevs.start()
        elif m_begin:
            kw_end += m_begin.start()
        else:
            return {}
        block = text[kw_start:kw_end]

        # Parse each line.  The "keywords" line and each "and" line declare words.
        keywords: dict[str, str] = {}
        raw_lines = block.split("\n")[1:]
        decls: list[str] = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("and ") or not decls:
                decls.append(line)
            else:
                decls[-1] += " " + stripped  # continuation of previous line
        for line in decls:
            decl = re.sub(r"^\s*(and\s+)?", "", line.strip())
            parts = decl.split("::", 1)
            if len(parts) == 2:
                words_part, kind_part = parts
                kind_part = kind_part.strip()
                if "%" in kind_part:
                    kind_part = kind_part.split("%")[0].strip()
                kind = kind_part.strip().strip('"')
            else:
                words_part = parts[0]
                kind = ""
            quoted_words = re.findall(r'"([^"]*)"', words_part)
            for w in quoted_words:
                keywords[w] = kind
        return keywords


# ---------------------------------------------------------------------------
# Tokenizer — Token.explode replica
# ---------------------------------------------------------------------------
# Mirrors token.scala:48-132 — delimited and non-delimited token scanning.


class TokenType(IntEnum):
    """
    Token kind ordinal, matching Token.Kind in token.scala.
    """

    COMMAND = 0
    KEYWORD = 1
    IDENT = 2
    LONG_IDENT = 3
    SYM_IDENT = 4
    VAR = 5
    TYPE_IDENT = 6
    TYPE_VAR = 7
    NAT = 8
    FLOAT = 9
    SPACE = 10
    STRING = 11
    ALT_STRING = 12
    CARTOUCHE = 13
    CONTROL = 14
    INFORMAL_COMMENT = 15
    FORMAL_COMMENT = 16
    ERROR = 17
    UNPARSED = 18


class Tokenizer:
    """
    Isabelle tokenizer. Replicates Token.explode from token.scala.
    """

    _SPACE_PAT = re.compile(r"[ \t\n\r\f\v]+")

    @staticmethod
    def _scan_quoted_string(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a "..." string with escapes. Returns (source, end_pos) or (None, pos).
        """

        if pos >= len(input) or input[pos] != '"':
            return None, pos
        i = pos + 1
        while i < len(input):
            ch = input[i]
            if ch == "\\" and i + 1 < len(input):
                # Escape sequences: \", \\, \ddd
                i += 2
                continue
            if ch == '"':
                return input[pos : i + 1], i + 1
            i += 1
        # Unclosed string (error recovery)
        return None, pos

    @staticmethod
    def _scan_alt_string(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a back-quoted string `` `...` ``.
        """

        if pos >= len(input) or input[pos] != "`":
            return None, pos
        i = pos + 1
        while i < len(input):
            if input[i] == "`":
                return input[pos : i + 1], i + 1
            i += 1
        return None, pos

    @staticmethod
    def _scan_comment(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a ``(* ... *)`` comment with nesting.
        """

        if pos + 1 >= len(input) or input[pos : pos + 2] != "(*":
            return None, pos
        depth = 1
        i = pos + 2
        while i < len(input) and depth > 0:
            if input[i : i + 2] == "(*":
                depth += 1
                i += 2
            elif input[i : i + 2] == "*)":
                depth -= 1
                i += 2
            else:
                i += 1
        if depth == 0:
            return input[pos:i], i
        return None, pos

    @staticmethod
    def _scan_cartouche(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a cartouche ``\\<open>...\\<close>``.
        """

        if not input[pos:].startswith("\\<open>"):
            return None, pos
        depth = 1
        i = pos + 7  # len(\<open>)
        while i < len(input) and depth > 0:
            if input[i:].startswith("\\<open>"):
                depth += 1
                i += 7
            elif input[i:].startswith("\\<close>"):
                depth -= 1
                if depth == 0:
                    i += 8  # len(\<close>)
                else:
                    i += 8
            else:
                i += 1
        if depth == 0:
            return input[pos:i], i
        return None, pos

    @staticmethod
    def _scan_control_token(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a control symbol: ``\\<^name>`` followed by cartouche or string.
        """

        if not input[pos:].startswith("\\<^"):
            return None, pos
        i = pos + 3
        while i < len(input) and input[i] not in ("\n", ">", "\\"):
            i += 1
        if i >= len(input) or input[i] != ">":
            return None, pos
        i += 1  # past >
        s, i2 = Tokenizer._scan_cartouche(input, i)
        if s is not None:
            return input[pos:i2], i2
        s, i2 = Tokenizer._scan_quoted_string(input, i)
        if s is not None:
            return input[pos:i2], i2
        return input[pos:i], i

    @staticmethod
    def _recover_delimited(input: str, pos: int) -> tuple[str | None, int]:
        """
        Recover malformed delimited tokens (unclosed quotes/cartouches).
        """

        ch = input[pos]
        if ch == '"':
            i = pos + 1
            while i < len(input):
                if input[i] == "\n":
                    break
                i += 1
            return input[pos:i], i
        if ch == "`":
            i = pos + 1
            while i < len(input):
                if input[i] == "\n":
                    break
                i += 1
            return input[pos:i], i
        if ch == "(" and pos + 1 < len(input) and input[pos + 1] == "*":
            return input[pos:], len(input)
        return None, pos

    @staticmethod
    def _scan_nat(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a natural number (digits).
        """

        m = re.compile(r"[0-9]+").match(input, pos)
        if m:
            return m.group(), m.end()
        return None, pos

    @staticmethod
    def _scan_ident_pos(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan an identifier starting at *pos*.
        """

        if pos >= len(input):
            return None, pos
        ch = input[pos]
        if not SymbolTable.default().is_letter(ch) and ch not in ("_",):
            return None, pos
        i = pos + 1
        while i < len(input):
            ch = input[i]
            if SymbolTable.default().is_letdig(ch):
                i += 1
            elif (
                ch == "."
                and i + 1 < len(input)
                and SymbolTable.default().is_letter(input[i + 1])
            ):
                # Long identifier: foo.bar
                i += 2
                while i < len(input) and SymbolTable.default().is_letdig(input[i]):
                    i += 1
                break
            else:
                break
        return input[pos:i], i

    @staticmethod
    def _scan_schematic_var(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan ``?id`` or ``?id.nat``.
        """

        if pos >= len(input) or input[pos] != "?":
            return None, pos
        i = pos + 1
        if i < len(input) and input[i] == "'":
            i += 1
        if i >= len(input) or not SymbolTable.default().is_letter(input[i]):
            return None, pos
        while i < len(input) and SymbolTable.default().is_letdig(input[i]):
            i += 1
        return input[pos:i], i

    @staticmethod
    def _scan_type_ident(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan ``'id``.
        """

        if pos >= len(input) or input[pos] != "'":
            return None, pos
        i = pos + 1
        if i >= len(input) or not SymbolTable.default().is_letter(input[i]):
            return None, pos
        while i < len(input) and SymbolTable.default().is_letdig(input[i]):
            i += 1
        return input[pos:i], i

    @staticmethod
    def _scan_float(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan floating-point number.
        """

        s, i = Tokenizer._scan_nat(input, pos)
        if s is None:
            return None, pos
        if i < len(input) and input[i] == ".":
            s2, i2 = Tokenizer._scan_nat(input, i + 1)
            if s2 is not None:
                return s + "." + s2, i2
        return None, pos

    @staticmethod
    def _scan_symbolic(input: str, pos: int) -> tuple[str | None, int]:
        """
        Scan a symbolic identifier (punctuation chars).
        """

        if pos >= len(input):
            return None, pos
        if input[pos] == "\\":
            m = _SYMBOL_PAT.match(input, pos)
            if m:
                return m.group(), m.end()
        ch = input[pos]
        if SymbolTable.default().is_symbolic(ch):
            i = pos + 1
            while i < len(input) and SymbolTable.default().is_symbolic(input[i]):
                i += 1
            return input[pos:i], i
        return None, pos

    def __init__(self, keywords: KeywordTable) -> None:
        self.keywords = keywords

    def tokenize(self, input: str) -> list["Token"]:
        """
        Tokenize *input* into a list of tokens.
        """

        tokens: list[Token] = []
        pos = 0
        while pos < len(input):
            start = pos
            tok, pos = self._next_token(input, pos)
            if tok is not None:
                tok.start = start
                tok.stop = pos
                tokens.append(tok)
        return tokens

    def _next_token(self, input: str, pos: int) -> tuple["Token | None", int]:
        """
        Scan the next token starting at *pos*.
        """

        # 1. Delimited tokens (tried first)
        for scan_fn, kind in (
            (Tokenizer._scan_quoted_string, TokenType.STRING),
            (Tokenizer._scan_alt_string, TokenType.ALT_STRING),
            (Tokenizer._scan_comment, TokenType.INFORMAL_COMMENT),
            (Tokenizer._scan_cartouche, TokenType.CARTOUCHE),
            (Tokenizer._scan_control_token, TokenType.CONTROL),
        ):
            s, pos2 = scan_fn(input, pos)
            if s is not None:
                return Token(kind_id=kind, source=s), pos2

        # 2a. Blank space
        m = self._SPACE_PAT.match(input, pos)
        if m:
            return Token(kind_id=TokenType.SPACE, source=m.group()), m.end()

        # 2b. Error recovery for malformed delimited tokens
        s, pos2 = Tokenizer._recover_delimited(input, pos)
        if s is not None:
            return Token(kind_id=TokenType.ERROR, source=s), pos2

        # 2c. Keyword vs alternative longest-match.
        # Scala uses ``keyword ||| (ident | var_ | ...)`` — the ||| combinator
        # picks the longer match between keyword and the alternative group.
        # Without this, ``in`` (a minor keyword) greedily matches and splits
        # ``induction`` into ``in`` + ``duction``.
        return self._scan_kw_or_alt(input, pos)

    def _scan_kw_or_alt(self, input: str, pos: int) -> tuple["Token", int]:
        """
        Keyword ||| (ident | var_ | ...) longest-match tie-breaker.
        """

        major = self.keywords.major.scan(input, pos).value_or(None)
        minor = self.keywords.minor.scan(input, pos).value_or(None)

        kw: tuple[str, TokenType] | None = None  # (source, kind_id)
        if major is not None and (
            minor is None or symbol_length(major) >= symbol_length(minor)
        ):
            kw = (major, TokenType.COMMAND)
        elif minor is not None and minor != ".":
            kw = (minor, TokenType.KEYWORD)

        # Alternative chain: first non-None result wins.
        alt: tuple[str, TokenType, int] | None = None  # (source, kind_id, end_pos)
        scans: list[
            tuple[
                Callable[[str, int], tuple[str | None, int]],
                Callable[[str], TokenType],
            ]
        ] = [
            (
                Tokenizer._scan_ident_pos,
                lambda s: TokenType.LONG_IDENT if "." in s else TokenType.IDENT,
            ),
            (Tokenizer._scan_schematic_var, lambda _: TokenType.VAR),
            (Tokenizer._scan_type_ident, lambda _: TokenType.TYPE_IDENT),
            (Tokenizer._scan_float, lambda _: TokenType.FLOAT),
            (Tokenizer._scan_nat, lambda _: TokenType.NAT),
            (Tokenizer._scan_symbolic, lambda _: TokenType.SYM_IDENT),
        ]
        for scan_fn, kind_fn in scans:
            s, p2 = scan_fn(input, pos)
            if s is not None:
                alt = (s, kind_fn(s), p2)
                break

        # Longer match wins; on tie, keyword takes priority.
        kw_len = len(kw[0]) if kw else 0
        alt_len = symbol_length(alt[0]) if alt else 0
        if kw and kw_len >= alt_len:
            return Token(kind_id=kw[1], source=kw[0]), pos + kw_len
        if alt:
            return Token(kind_id=alt[1], source=alt[0]), alt[2]

        # Single character (ERROR fallback)
        return Token(kind_id=TokenType.ERROR, source=input[pos]), pos + 1

    def tokenize_spans(self, input: str) -> list["CommandSpan"]:
        """
        Convenience: tokenize then parse spans.
        """

        tokens = self.tokenize(input)
        return parse_spans(self.keywords, tokens)


class Token(BaseModel):
    """
    A single Isabelle token.
    """

    kind_id: TokenType = TokenType.ERROR  # token kind ordinal
    source: str = ""
    kind_name: str = ""
    start: int = 0  # char offset in original input
    stop: int = 0  # char end offset in original input

    @model_validator(mode="after")
    def _set_kind_name(self):
        if not self.kind_name:
            self.kind_name = self.kind_id.name.lower()
        return self

    @property
    def is_command(self) -> bool:
        return self.kind_id == TokenType.COMMAND

    @property
    def is_keyword(self) -> bool:
        return self.kind_id == TokenType.KEYWORD

    @property
    def is_ignored(self) -> bool:
        return self.kind_id in (TokenType.SPACE, TokenType.INFORMAL_COMMENT)

    @property
    def is_error(self) -> bool:
        return self.kind_id == TokenType.ERROR


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Command span parser — CommandSpan dataclass + parse_spans
# ---------------------------------------------------------------------------


class CommandSpan(BaseModel):
    """
    A single command span — the result of parsing token list into spans.
    """

    id: int  # sequential command ID
    name: str  # command keyword source text (e.g. "theory", "lemma")
    kind: str  # span kind (e.g. "thy_begin", "prf_goal", "qed")
    source: str  # full source text of this span
    tokens: list[Token]  # list of Token objects
    start: int = 0  # char offset in source
    stop: int = 0  # char end offset in source

    @property
    def command_keyword(self) -> str:
        return next((t.source for t in self.tokens if t.is_command), "")

    @staticmethod
    def line_offset(source: str, line: int) -> int:
        """
        Return the byte offset of the start of *line* (1-based) in *source*.
        """

        offset = 0
        for _ in range(line - 1):
            idx = source.find("\n", offset)
            if idx == -1:
                break
            offset = idx + 1
        return offset

    @staticmethod
    def at_offset(
        source: str, spans: list[CommandSpan], offset: int
    ) -> Maybe[CommandSpan]:
        """
        Return the named *span* covering or preceding *offset*.
        """

        if not spans or offset >= len(source):
            return Nothing
        best: CommandSpan | None = None
        for s in spans:
            if s.start <= offset < s.stop and s.name:
                return Some(s)
            if s.start <= offset:
                best = s if s.name else best
        return Some(best) if best else Nothing


def parse_spans(keywords: KeywordTable, tokens: list[Token]) -> list[CommandSpan]:
    """Split tokens into command spans.

    Replicates Outer_Syntax.parse_spans from outer_syntax.scala:158-201.
    """
    spans: list[CommandSpan] = []
    content: list[Token] = []
    ignored: list[Token] = []

    def _flush(
        content: list[Token], ignored: list[Token], spans: list[CommandSpan]
    ) -> tuple[list[Token], list[Token]]:
        if content:
            spans.append(_make_span(keywords, len(spans) + 1, list(content)))
        if ignored:
            s = "".join(t.source for t in ignored)
            spans.append(
                CommandSpan(
                    id=len(spans) + 1,
                    name="",
                    kind="ignored",
                    source=s,
                    tokens=list(ignored),
                )
            )
        return [], []

    for tok in tokens:
        if tok.is_ignored:
            ignored.append(tok)
            continue
        is_before = (
            tok.is_keyword
            and keywords.lookup_kind(tok.source).value_or("").lower()
            == "before_command"
        )
        starts_new = is_before or (
            tok.is_command
            and (
                not any(
                    keywords.lookup_kind(t.source).value_or("").lower()
                    == "before_command"
                    for t in content
                )
                or any(t.is_command for t in content)
            )
        )
        if starts_new:
            content, ignored = _flush(content, ignored, spans)
        if content:
            content.extend(ignored)
            ignored.clear()
        content.append(tok)
    content, ignored = _flush(content, ignored, spans)
    return spans


def _make_span(
    keywords: KeywordTable, span_id: int, tokens: list[Token]
) -> CommandSpan:
    cmd_name = ""
    cmd_kind = ""
    cmd_token = next((t for t in tokens if t.is_command), None)
    if cmd_token is not None:
        cmd_name = cmd_token.source
        cmd_kind = keywords.lookup_kind(cmd_name).value_or("")
    start = tokens[0].start if tokens else 0
    stop = tokens[-1].stop if tokens else 0
    return CommandSpan(
        id=span_id,
        name=cmd_name,
        kind=cmd_kind,
        source="".join(t.source for t in tokens),
        tokens=tokens,
        start=start,
        stop=stop,
    )


# ---- encode helpers ------------------------------------------------------


# ---------------------------------------------------------------------------
# PIDEInteractiveOptions — concrete-field options model
# ---------------------------------------------------------------------------


class PIDEInteractiveOptions(BaseModel):
    """Isabelle system options with concrete typed fields for PIDE use.

    Encode format: ``list(pair(properties, pair(string, pair(string, string))))``,
    matching ``Options.encode`` in Scala and ``Options.decode`` in ML.
    """

    # ── Prover Output ──
    pide_reports: bool = True
    show_types: bool = False
    show_sorts: bool = False
    show_brackets: bool = False
    show_question_marks: bool = True
    show_consts: bool = False
    show_main_goal: bool = False
    goals_limit: int = 10
    show_states: bool = False
    names_long: bool = False
    names_short: bool = False
    names_unique: bool = True
    eta_contract: bool = True
    print_mode: str = ""

    # ── Parallel Processing and Timing ──
    threads: int = 0
    threads_trace: int = 0
    threads_stack_limit: float = 0.25
    parallel_limit: int = 0
    parallel_print: bool = True
    parallel_proofs: int = 1
    parallel_subproofs_threshold: float = 0.01
    command_timing_threshold: float = 0.1
    timeout_scale: float = 1.0
    context_theory_tracing: bool = False
    context_proof_tracing: bool = False
    context_data_timing: bool = False

    # ── Detail of Proof Checking ──
    record_proofs: int = -1
    quick_and_dirty: bool = False
    skip_proofs: bool = False
    strict_facts: bool = False

    # ── Global Session Parameters ──
    context: str = ""
    condition: str = ""
    process_policy: str = ""

    # ── ML System ──
    ML_print_depth: int = 20
    ML_exception_trace: bool = False
    ML_exception_debugger: bool = False
    ML_debugger: bool = False

    # ── Editor Session ──
    editor_load_delay: float = 0.5
    editor_input_delay: float = 0.2
    editor_generated_input_delay: float = 1.0
    editor_output_delay: float = 0.1
    editor_consolidate_delay: float = 1.0
    editor_prune_delay: float = 15.0
    editor_prune_size: int = 0
    editor_update_delay: float = 0.5
    editor_reparse_limit: int = 10000
    editor_tracing_messages: int = 1000
    editor_chart_delay: float = 3.0
    editor_continuous_checking: bool = True
    editor_output_state: bool = False
    editor_auto_hovering: bool = True
    editor_document_session: str = ""
    editor_document_auto: bool = False
    editor_document_delay: float = 2.0
    editor_execution_delay: float = 0.02
    editor_syslog_limit: int = 100

    # ── Headless Session ──
    headless_consolidate_delay: float = 2.0
    headless_prune_delay: float = 30.0
    headless_check_delay: float = 0.5
    headless_check_limit: int = 0
    headless_nodes_status_delay: float = -1.0
    headless_watchdog_timeout: float = 600.0
    headless_commit_cleanup_delay: float = 60.0
    headless_load_limit: float = 5.0

    # ── Miscellaneous Tools ──
    find_theorems_limit: int = 40
    find_theorems_tactic_limit: int = 5

    # ── Completion ──
    completion_limit: int = 40
    completion_path_ignore: str = "*~:*.marks:*.orig:*.rej:.DS_Store"

    # ── Spell Checker ──
    spell_checker: bool = True
    spell_checker_dictionary: str = "en"
    spell_checker_include: str = (
        "words,comment,comment1,comment2,comment3,ML_comment,SML_comment"
    )
    spell_checker_exclude: str = "document_marker,antiquoted,raw_text"

    # ── SSH ──
    ssh_batch_mode: bool = True
    ssh_multiplexing: bool = True
    ssh_compression: bool = True
    ssh_alive_interval: float = 30.0
    ssh_alive_count_max: int = 3

    # ── Theory Export ──
    export_theory: bool = False
    export_standard_proofs: bool = False
    export_proofs: bool = False
    prune_proofs: bool = False

    # ── Theory Update ──
    update_inner_syntax_cartouches: bool = False
    update_mixfix_cartouches: bool = False
    update_control_cartouches: bool = False
    update_path_cartouches: bool = False
    update_cite: bool = False

    # ── System Channel ──
    system_channel_address: str = ""
    system_channel_password: str = ""

    # ── Bash Process ──
    bash_process_debugging: bool = False
    bash_process_address: str = ""
    bash_process_password: str = ""

    # ── Thy Output ──
    thy_output_display: bool = False
    thy_output_break: bool = False
    thy_output_cartouche: bool = False
    thy_output_quotes: bool = False
    thy_output_margin: int = 76
    thy_output_indent: int = 0
    thy_output_source: bool = False
    thy_output_source_cartouche: bool = False
    thy_output_modes: str = ""

    # ---- Validation ------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _coerce_types(cls, data: object) -> dict[str, object]:
        """
        Coerce string values from YXML deserialization to typed fields.
        """

        if not isinstance(data, dict):
            return data  # type: ignore[reportReturnType]
        d = cast(dict[str, object], data)
        for field_name, fi in cls.model_fields.items():
            v = d.get(field_name)
            if not isinstance(v, str):
                continue
            target = fi.annotation
            if target is bool:
                d[field_name] = v.lower() == "true"
            elif target is int:
                try:
                    d[field_name] = int(v)
                except (ValueError, TypeError):
                    del d[field_name]  # best-effort coercion, skip on failure
            elif target is float:
                try:
                    d[field_name] = float(v)
                except (ValueError, TypeError):
                    del d[field_name]  # best-effort coercion, skip on failure
        return d

    # ---- Serialization ---------------------------------------------------------

    @classmethod
    def from_yxml(cls, source: str) -> PIDEInteractiveOptions:
        """Parse YXML-encoded options into a ``PIDEInteractiveOptions`` instance.

        Args:
            source: YXML body string (from ``isabelle options -x`` or an
                    ``ISABELLE_PROCESS_OPTIONS`` file).
        """
        body = YXMLElemModel.parse_many(source)
        kwargs: dict[str, Any] = {}
        known = cls.model_fields

        for item in body:
            if (
                isinstance(item, YXMLElemModel)
                and item.name == ":"
                and len(item.body) >= 2
                and isinstance(item.body[1], YXMLElemModel)
                and item.body[1].name == ":"
                and len(item.body[1].body) >= 1
                and isinstance(item.body[1].body[0], YXMLElemModel)
            ):
                n_body = item.body[1].body[0].body
                typval_node = item.body[1].body[1]
                name = YXMLElemModel.content_of(n_body)
                if (
                    isinstance(typval_node, YXMLElemModel)
                    and typval_node.name == ":"
                    and len(typval_node.body) >= 2
                    and isinstance(typval_node.body[0], YXMLElemModel)
                    and isinstance(typval_node.body[1], YXMLElemModel)
                ):
                    t_body = typval_node.body[0].body
                    v_body = typval_node.body[1].body
                    typ_str = YXMLElemModel.content_of(t_body)
                    val_str = YXMLElemModel.content_of(v_body)
                else:
                    continue

                if not name or not typ_str or name not in known:
                    continue

                kwargs[name] = val_str  # validator _coerce_types does type coercion

        return cls(**kwargs)

    _PY_TYPE_TO_ISABELLE: ClassVar[dict[type[bool | int | float | str], str]] = {
        bool: "bool",
        int: "int",
        float: "real",
        str: "string",
    }

    @staticmethod
    def _format_yxml_value(value: bool | int | float | str) -> str:
        """
        Format a Python value to its Isabelle YXML string representation.
        """

        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _encode_option_body(
        options: list[tuple[list[tuple[str, str]], tuple[str, tuple[str, str]]]],
    ) -> list[YXMLElemModel | str]:
        """
        Encode option entries as XML body elements.
        """

        result: list[YXMLElemModel | str] = []
        for props, (name, (typ, value)) in options:
            props_elem = YXMLElemModel(name=":", attrs=dict(props), body=[])
            name_elem = YXMLElemModel(name=":", body=[name])
            type_elem = YXMLElemModel(name=":", body=[typ])
            value_elem = YXMLElemModel(name=":", body=[value])
            typval = YXMLElemModel(name=":", body=[type_elem, value_elem])
            inner = YXMLElemModel(name=":", body=[name_elem, typval])
            result.append(YXMLElemModel(name=":", body=[props_elem, inner]))
        if not result:
            result.append(YXMLElemModel(name=":", body=[]))
        return result

    @staticmethod
    def patch_yxml(raw_yxml: str, updates: dict[str, str]) -> str:
        """Update specific option values in a full options YXML body.

        Unlike ``PIDEInteractiveOptions.from_yxml`` + re-encode (which drops
        options not in the model), this preserves *all* options and only
        changes the value of named entries.
        """
        body = YXMLElemModel.parse_many(raw_yxml)

        for idx, item in enumerate(body):
            if (
                isinstance(item, YXMLElemModel)
                and item.name == ":"
                and len(item.body) >= 2
                and isinstance(item.body[1], YXMLElemModel)
                and item.body[1].name == ":"
                and item.body[1].body
                and isinstance(item.body[1].body[0], YXMLElemModel)
                and item.body[1].body[0].name == ":"
            ):
                name_body = item.body[1].body[0].body
                typval_node = item.body[1].body[1]
                name = YXMLElemModel.content_of(name_body)
                if name not in updates:
                    continue
                if (
                    isinstance(typval_node, YXMLElemModel)
                    and typval_node.name == ":"
                    and len(typval_node.body) >= 2
                    and isinstance(typval_node.body[1], YXMLElemModel)
                    and typval_node.body[1].name == ":"
                ):
                    new_val = updates[name]
                    new_body: list[YXMLElemModel | str] = (
                        [] if new_val == "" else [new_val]
                    )
                    new_value_item = YXMLElemModel(
                        name=typval_node.body[1].name,
                        attrs=typval_node.body[1].attrs,
                        body=new_body,
                    )
                    new_typval = YXMLElemModel(
                        name=typval_node.name,
                        attrs=typval_node.attrs,
                        body=[typval_node.body[0], new_value_item],
                    )
                    new_inner = YXMLElemModel(
                        name=item.body[1].name,
                        attrs=item.body[1].attrs,
                        body=[item.body[1].body[0], new_typval],
                    )
                    body[idx] = YXMLElemModel(
                        name=item.name,
                        attrs=item.attrs,
                        body=[item.body[0], new_inner],
                    )

        return "".join(YXMLElemModel.to_str(e) for e in body)

    def encode_yxml(self) -> str:
        """
        Encode all options as YXML in field-declaration order.
        """

        entries: list[tuple[list[tuple[str, str]], tuple[str, tuple[str, str]]]] = []
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            isabelle_type = self._PY_TYPE_TO_ISABELLE[type(value)]
            formatted = self._format_yxml_value(value)
            entries.append(([], (field_name, (isabelle_type, formatted))))
        return "".join(
            YXMLElemModel.to_str(e) for e in self._encode_option_body(entries)
        )


class IsarYXMLUtils:
    """
    PIDE-specific protocol YXML encoders, grouped as static methods.
    """

    @staticmethod
    def encode_document_update(
        old_id: str,
        new_id: str,
        edit_yxmls: list[str],
        consolidate: list[str] | None = None,
    ) -> list[bytes]:
        """
        Build the wire-format chunks for ``Document.update``.
        """

        if consolidate is None:
            consolidate = []
        cons_yxml = "".join(
            YXMLElemModel.to_str(YXMLElemModel(name=":", body=[s])) for s in consolidate
        )
        args = [old_id.encode(), new_id.encode(), cons_yxml.encode()]
        for e in edit_yxmls:
            args.append(e.encode())
        return [b"Document.update"] + args

    @staticmethod
    def encode_empty_session_yxml() -> str:
        """
        Build a minimal (empty) session init YXML with all 7 sections empty.
        """

        empty_elem = YXMLElemModel(name=":", body=[])
        body: list[YXMLElemModel | str] = [empty_elem, empty_elem]
        for _ in range(5):
            body = [empty_elem, YXMLElemModel(name=":", body=body)]
        return "".join(YXMLElemModel.to_str(e) for e in body)

    @staticmethod
    def build_deps_yxml(
        node_name: str, theory_name: str, master_dir: PathStr, imports: list[str]
    ) -> str:
        """
        Build the Deps variant YXML for ``Document.update``.
        """

        def _enc_str(s: str) -> list[YXMLElemModel | str]:
            return [] if not s else [s]

        def _enc_list(xs: list[str]) -> list[YXMLElemModel | str]:
            return [YXMLElemModel(name=":", body=_enc_str(s)) for s in xs]

        def _pair(
            a: list[YXMLElemModel | str], b: list[YXMLElemModel | str]
        ) -> list[YXMLElemModel | str]:
            return [YXMLElemModel(name=":", body=a), YXMLElemModel(name=":", body=b)]

        imports_sub = _pair([], [])
        imports_part = _pair(_enc_list(imports), imports_sub)
        theory_part = _pair(_enc_str(theory_name), imports_part)
        master_part = _pair(_enc_str(master_dir), theory_part)
        variant_body = YXMLElemModel(name="1", body=master_part)
        body = _pair(_enc_str(node_name), [variant_body])
        return "".join(YXMLElemModel.to_str(e) for e in body)

    @staticmethod
    def build_edits_yxml(node_name: str, spans: list[CommandSpan]) -> str:
        """
        Build the Edits variant YXML for ``Document.update``.
        """

        named = [s for s in spans if s.name]
        pairs: list[tuple[int | None, int]] = []
        prev_id: int | None = None
        for s in named:
            pairs.append((prev_id, s.id))
            prev_id = s.id

        def _encode_id(val: int | None) -> list[YXMLElemModel | str]:
            if val is None:
                return []
            return [YXMLElemModel(name=":", body=[str(val)])]

        def _encode_edit_pair(p: tuple[int | None, int]) -> list[YXMLElemModel | str]:
            return [
                YXMLElemModel(name=":", body=_encode_id(p[0])),
                YXMLElemModel(name=":", body=_encode_id(p[1])),
            ]

        pair_elems: list[YXMLElemModel | str] = [
            YXMLElemModel(name=":", body=_encode_edit_pair(x)) for x in pairs
        ]
        variant_body = YXMLElemModel(name="0", body=pair_elems)
        body = [
            YXMLElemModel(name=":", body=[node_name]),
            YXMLElemModel(name=":", body=[variant_body]),
        ]
        return "".join(YXMLElemModel.to_str(e) for e in body)

    @staticmethod
    def build_perspective_yxml(node_name: str, ids: list[int]) -> str:
        """
        Build the Perspective variant YXML for ``Document.update``.
        """

        attrs: dict[str, YXMLAttr] = {
            str(i): a for i, a in enumerate(["1"] + [str(i) for i in ids])
        }
        variant_body = YXMLElemModel(name="2", attrs=attrs, body=[])
        body = [
            YXMLElemModel(name=":", body=[node_name]),
            YXMLElemModel(name=":", body=[variant_body]),
        ]
        return "".join(YXMLElemModel.to_str(e) for e in body)

    @staticmethod
    def build_define_command(span: CommandSpan) -> list[bytes]:
        """
        Build wire chunks for ``Document.define_command``.
        """

        id_bytes = str(span.id).encode()
        name_bytes = span.name.encode()
        parents_bytes = "".encode()
        blobs_bytes = "".join(
            YXMLElemModel.to_str(e)
            for e in [
                YXMLElemModel(name=":", body=[]),
                YXMLElemModel(name=":", body=["0"]),
            ]
        ).encode()
        tok_elems: list[YXMLElemModel] = [
            YXMLElemModel(
                name=":",
                body=[
                    YXMLElemModel(name=":", body=[str(t.kind_id)]),
                    YXMLElemModel(name=":", body=[str(symbol_length(t.source))]),
                ],
            )
            for t in span.tokens
        ]
        toks_bytes = "".join(e.model_dump_yxml() for e in tok_elems).encode()
        sources_bytes = [t.source.encode() for t in span.tokens]
        return [
            b"Document.define_command",
            id_bytes,
            name_bytes,
            parents_bytes,
            blobs_bytes,
            toks_bytes,
        ] + sources_bytes

    @staticmethod
    def send_define_commands(conn: socket.socket, spans: list[CommandSpan]) -> int:
        """
        Send ``Document.define_command`` for each named span.
        """

        last_id = 0
        for span in spans:
            if not span.name:
                continue
            chunks = IsarYXMLUtils.build_define_command(span)
            write_message(conn, chunks)
            last_id = span.id
        return last_id

    @staticmethod
    def _parse_body_chunks(chunks: list[bytes]) -> list[YXMLElemModel | str]:
        """
        Parse YXML body chunks into a list of YXML tree nodes.
        """

        body: list[YXMLElemModel | str] = []
        for chunk in chunks:
            if chunk:
                try:
                    body.extend(
                        YXMLElemModel.parse_many(
                            chunk.decode("utf-8", errors="replace")
                        )
                    )
                except YXMLParseError:
                    pass
        return body

    @staticmethod
    def build_command_message(name: str, args: list[str]) -> list[bytes]:
        """
        Build the chunks for a protocol command.
        """

        return [name.encode("utf-8")] + [a.encode("utf-8") for a in args]


class AssignUpdateBody(BaseModel):
    """Parsed assign/update message body.

    ML encoder: ``triple int (list string) (list encode_upd)`` producing
    three body elements: version_id, edited_nodes, assignments.
    """

    version_id: int = 0
    assignments: dict[CommandID, list[ExecID]] = {}

    @model_validator(mode="before")
    @classmethod
    def _from_yxml(cls, data: object) -> object:
        if isinstance(data, dict):
            return cast(dict[str, object], data)
        body = cast(list[YXMLElemModel | str], data)
        if len(body) < 3:
            raise ValueError(f"expected >=3 body elements, got {len(body)}")

        if not (
            isinstance(body[0], YXMLElemModel)
            and body[0].name == ":"
            and len(body[0].body) == 1
            and isinstance(body[0].body[0], str)
        ):
            raise ValueError("expected version_id in first body element")
        version_id = int(body[0].body[0])

        assign: dict[CommandID, list[ExecID]] = {}
        if (
            len(body) >= 3
            and isinstance(body[2], YXMLElemModel)
            and body[2].name == ":"
        ):
            items = body[2].body
            for item in items:
                if (
                    isinstance(item, YXMLElemModel)
                    and item.name == ":"
                    and len(item.body) == 1
                    and isinstance(item.body[0], str)
                ):
                    txt = item.body[0]
                    parts = txt.split(",")
                    if parts:
                        cmd_id: CommandID = int(parts[0])
                        assign[cmd_id] = [int(x) for x in parts[1:]]
        return {"version_id": version_id, "assignments": assign}

    def encode_yxml(self) -> str:
        """
        Serialize to YXML string matching the ML encoder format.
        """

        items: list[YXMLElemModel | str] = [
            YXMLElemModel(name=":", body=[str(self.version_id)]),
            YXMLElemModel(name=":"),
        ]
        assign_items: list[YXMLElemModel | str] = [
            YXMLElemModel(
                name=":",
                body=[",".join([str(cmd_id)] + [str(e) for e in exec_ids])],
            )
            for cmd_id, exec_ids in self.assignments.items()
        ]
        items.append(YXMLElemModel(name=":", body=assign_items))
        return YXMLElemModel.serialize_many(items)


# ---------------------------------------------------------------------------
# Byte-message framing
# ---------------------------------------------------------------------------
# Messages are length-prefixed multi-chunk frames:
#   "<len1>,<len2>,...,<lenN>\n<chunk1><chunk2>...<chunkN>"
# Each chunk is arbitrary binary data.  The header line is ASCII.


def _read_exactly(stream: socket.socket, n: int, timeout: float | None = None) -> bytes:
    """
    Read exactly *n* bytes from *stream*, or raise EOFError.
    """

    buf = bytearray()
    deadline = (time.time() + timeout) if timeout else None
    while len(buf) < n:
        # Compute remaining time for timeout
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"timed out reading chunk ({len(buf)} of {n} bytes)")
            try:
                stream.settimeout(remaining)
            except OSError:
                pass  # best-effort timeout set on some platforms

        try:
            chunk = stream.recv(n - len(buf))
        except socket.timeout:
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError(f"timed out reading chunk ({len(buf)} of {n} bytes)")
            continue
        if not chunk:
            raise EOFError(f"expected {n} bytes, got {len(buf)}")
        buf.extend(chunk)
    return bytes(buf)


def _read_line(stream: socket.socket, timeout: float | None = None) -> bytes:
    """
    Read bytes until newline (ASCII 10).  Strip trailing CR.  Return empty on EOF.
    """

    buf = bytearray()
    deadline = (time.time() + timeout) if timeout else None
    while True:
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                return bytes(buf)
            try:
                stream.settimeout(max(remaining, 0.01))
            except OSError:
                pass

        try:
            b = stream.recv(1)
        except socket.timeout:
            if deadline is not None and time.time() >= deadline:
                return bytes(buf)
            continue
        except (ConnectionResetError, BrokenPipeError):
            return bytes(buf)
        if not b:
            return bytes(buf)
        if b[0] == 10:  # \n
            break
        buf.append(b[0])
    # Strip trailing \r
    result = bytes(buf)
    if result.endswith(b"\r"):
        result = result[:-1]
    return result


def read_message(
    stream: socket.socket, timeout: float | None = None
) -> Result[list[bytes], str]:
    """Read one length-prefixed multi-chunk message.

    Returns Success(list of chunk bytes) or Failure("eof"/"timeout").
    """
    line = _read_line(stream, timeout=timeout)
    if not line:
        return Failure("eof")

    header = line.decode("ascii", errors="replace")
    try:
        lengths = [int(x) for x in header.split(",")]
    except ValueError:
        return Failure(f"malformed message header: {header!r}")

    chunks: list[bytes] = []
    for n in lengths:
        try:
            chunks.append(_read_exactly(stream, n, timeout=timeout))
        except (EOFError, TimeoutError) as exc:
            return Failure(str(exc))
    return Success(chunks)


def write_message(stream: socket.socket, chunks: list[bytes]) -> None:
    """
    Write a length-prefixed multi-chunk message.
    """

    header = ",".join(str(len(c)) for c in chunks).encode("ascii") + b"\n"
    stream.sendall(header)
    for c in chunks:
        stream.sendall(c)


# ---------------------------------------------------------------------------
# Protocol messages
# ---------------------------------------------------------------------------


class MarkupKind:
    """Known PIDE message kinds. Subclass to add custom kinds.

    Every prover output message has a "kind" string (its element name in the
    wire format) identifying its role in the protocol.  This class holds the
    canonical constant for each known kind.  Use like an extensible enum::

        if msg.kind == MarkupKind.STATE:
            ...
    """

    # --- Output message kinds (first chunk of every prover message) ----------
    # Source: markup.scala:587-603, markup.ML:755-766, isabelle_process.ML:103-141

    INIT: str = "init"

    EXIT: str = "exit"

    PROTOCOL: str = "protocol"

    STATUS: str = "status"

    REPORT: str = "report"

    RESULT: str = "result"

    WRITELN: str = "writeln"

    STATE: str = "state"

    INFORMATION: str = "information"

    TRACING: str = "tracing"

    WARNING: str = "warning"

    LEGACY: str = "legacy"

    ERROR: str = "error"

    SYSTEM: str = "system"

    STDOUT: str = "stdout"

    STDERR: str = "stderr"

    # --- Protocol function names (FUNCTION property in PROTOCOL messages) ----

    ML_statistics: str = "ML_statistics"

    command_timing: str = "command_timing"

    theory_timing: str = "theory_timing"

    loading_theory: str = "loading_theory"

    commands_accepted: str = "commands_accepted"

    assign_update: str = "assign_update"

    removed_versions: str = "removed_versions"

    # --- Exec lifecycle markers (carried in STATUS message body) -------------

    running: str = "running"

    finished: str = "finished"

    failed: str = "failed"

    joined: str = "joined"

    consolidating: str = "consolidating"

    consolidated: str = "consolidated"


class Markup(YXMLElemModel):
    """A parsed message from the prover.

    ``name`` is the message kind (e.g. ``"state"``, ``"information"``),
    ``attrs`` are the message properties (key-value metadata from the wire),
    and ``body`` is the YXML element body.
    """

    @model_validator(mode="before")
    @classmethod
    def _from_chunks(cls, data: object) -> object:
        """Accept ``list[bytes]`` (raw chunks) or a dict.

        Supports two wire formats:

        1. **New-style** — chunk[0] is a YXML element carrying the message
           kind (element name) and its attributes (properties)::

               <state serial=4772774 id=300>...body...</state>

        2. **Old-style** — chunk[0] is a plain string (message kind),
           chunk[1] is the property count, chunks 2..N-1 are ``"key=value"``
           strings, and remaining chunks are the YXML body.
        """
        if isinstance(data, dict):
            return cast(dict[str, object], data)
        if not isinstance(data, list) or not data or not isinstance(data[0], bytes):
            type_name = type(cast(object, data)).__name__  # type: ignore[redundant-cast]
            raise ValueError(f"expected list[bytes] or dict, got {type_name}")  # type: ignore[unreachable]
        chunks = cast(list[bytes], data)

        # Parse header YXML (chunk[0])
        header_yxml = chunks[0].decode("utf-8", errors="replace")
        header_trees = YXMLElemModel.parse_many(header_yxml)
        match header_trees:
            case [YXMLElemModel(name=kname, attrs=kattrs)]:
                # New-style: header is a YXML element
                return {
                    "name": kname,
                    "attrs": kattrs,
                    "body": IsarYXMLUtils._parse_body_chunks(chunks[1:]),  # type: ignore[reportPrivateUsage]
                }

            case [str(kind)]:
                # Old-style: chunk[0] is a plain string (message kind)
                pass
            case _:
                raise ValueError(f"invalid message header, got {header_trees!r}")

        # Chunk 1: property count
        if len(chunks) < 2:
            return {"name": kind, "attrs": {}, "body": []}
        count_yxml = chunks[1].decode("utf-8", errors="replace")
        count_trees = YXMLElemModel.parse_many(count_yxml)
        n_props: int = 0
        if len(count_trees) == 1 and isinstance(count_trees[0], str):
            try:
                n_props = int(count_trees[0])
            except ValueError:
                n_props = 0

        # Chunks 2..(2+N-1): property strings "name=value" or "name = value"
        attrs: dict[str, YXMLAttr] = {}
        prop_end = 2 + n_props
        for i in range(2, min(prop_end, len(chunks))):
            prop_trees = YXMLElemModel.parse_many(
                chunks[i].decode("utf-8", errors="replace")
            )
            if len(prop_trees) == 1 and isinstance(prop_trees[0], str):
                txt = prop_trees[0]
                sep = " = " if " = " in txt else "="
                if sep in txt:
                    k, v = txt.split(sep, 1)
                    attrs[k] = YXMLElemModel._coerce_attr(v)

        # Remaining chunks: body
        body = IsarYXMLUtils._parse_body_chunks(chunks[prop_end:])  # type: ignore[reportPrivateUsage]

        return {"name": kind, "attrs": attrs, "body": body}


class PIDEPayload(dict[ExecID, list[Markup]]):
    """Accumulated prover output keyed by execution ID.

    ``pid[exec_id]``        -> list[Markup] for that execution
    ``pid.by_kind("state")`` -> all STATE markups across all exec IDs
    """

    @classmethod
    def from_yxml(cls, data: bytes) -> "PIDEPayload":
        """Parse raw protocol message bytes into a PIDEPayload.

        Reads a stream of length-prefixed multi-chunk messages (the wire
        format produced by ``read_message``), parses each into a
        :class:`Markup`, and groups them by execution ID from the
        ``"id"`` attribute.
        """
        result: PIDEPayload = cls()
        offset = 0
        while offset < len(data):
            nl = data.find(b"\n", offset)
            if nl < 0:
                break
            header = data[offset:nl].decode("ascii", errors="replace")
            offset = nl + 1
            if not header:
                continue
            try:
                lengths = [int(x) for x in header.split(",")]
            except ValueError:
                break

            chunks: list[bytes] = []
            for n in lengths:
                if offset + n > len(data):
                    break
                chunks.append(data[offset : offset + n])
                offset += n
            if len(chunks) != len(lengths):
                break

            try:
                msg = Markup.model_validate(chunks)
            except (ValueError, YXMLParseError):
                continue

            eid_raw = msg.attrs.get("id")
            if eid_raw is not None:
                exec_id = int(eid_raw) if not isinstance(eid_raw, int) else eid_raw
                if exec_id not in result:
                    result[exec_id] = []
                result[exec_id].append(msg)
        return result

    def by_kind(self, kind: str) -> list[Markup]:
        """
        Return all markups of the given *kind* across all exec IDs.
        """

        return [m for ms in self.values() for m in ms if m.name == kind]


# ---------------------------------------------------------------------------
# Prover process launcher
# ---------------------------------------------------------------------------


class IsabelleEnv(BaseModel):
    """
    Essential Isabelle environment variables.
    """

    isabelle_home: Path
    polyml_exe: Path
    ml_home: Path
    ml_identifier: str
    ml_platform: str
    ml_system: str
    heaps_system: Path
    heaps_user: Path
    ml_options: str
    isabelle_tmp_prefix: Path

    @property
    def pure_heap(self) -> Path:
        """
        Find the Pure session heap file.
        """

        for base in [self.heaps_system, self.heaps_user]:
            path = base / f"{self.ml_identifier}/Pure"
            if path.is_file():
                return path
        raise FileNotFoundError(
            f"Pure heap not found. Build it with: isabelle build -b Pure\n"
            f"  Looked in: {self.heaps_system}/{self.ml_identifier}/Pure\n"
            f"            {self.heaps_user}/{self.ml_identifier}/Pure"
        )

    @classmethod
    def from_isabelle_home(cls, isabelle_home: Path) -> Result["IsabelleEnv", str]:
        """
        Discover the Isabelle environment by shelling out to `isabelle getenv`.
        """

        isabelle_bin = isabelle_home / "bin/isabelle"
        if not isabelle_bin.is_file():
            return Failure(f"isabelle script not found at {isabelle_bin}")

        vars_needed = [
            "ISABELLE_HOME",
            "POLYML_EXE",
            "ML_HOME",
            "ML_IDENTIFIER",
            "ML_PLATFORM",
            "ML_SYSTEM",
            "ISABELLE_HEAPS_SYSTEM",
            "ISABELLE_HEAPS",
            "ML_OPTIONS",
            "ISABELLE_TMP_PREFIX",
        ]

        # Run isabelle getenv -b to get just values
        try:
            result = subprocess.run(
                [str(isabelle_bin), "getenv", "-b"] + vars_needed,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "ISABELLE_HOME": str(isabelle_home)},
            )
        except subprocess.TimeoutExpired:
            return Failure("isabelle getenv timed out")

        if result.returncode != 0:
            return Failure(f"isabelle getenv failed: {result.stderr}")

        values = result.stdout.strip().split("\n")
        if len(values) != len(vars_needed):
            return Failure(
                f"Expected {len(vars_needed)} values, got {len(values)}: {values!r}"
            )

        env = dict(zip([v.lower() for v in vars_needed], values))

        return Success(
            cls(
                isabelle_home=Path(env["isabelle_home"]),
                polyml_exe=Path(env["polyml_exe"]),
                ml_home=Path(env["ml_home"]),
                ml_identifier=env["ml_identifier"],
                ml_platform=env["ml_platform"],
                ml_system=env["ml_system"],
                heaps_system=Path(env["isabelle_heaps_system"]),
                heaps_user=Path(env["isabelle_heaps"]),
                ml_options=env["ml_options"],
                isabelle_tmp_prefix=Path(env["isabelle_tmp_prefix"]),
            )
        )


class ProverProcess:
    """Launch and manage the Isabelle/ML prover process via PIDE protocol.

    Usage::

        env = IsabelleEnv.from_isabelle_home("/path/to/Isabelle2025")
        with ProverProcess(env) as prover:
            prover.protocol_command("Prover.echo", "hello")
            for msg in prover.read_messages(timeout=2.0):
                print(f"{msg.kind}: {XML.content_of(msg.body)}")
    """

    def __init__(
        self,
        isabelle_env: IsabelleEnv,
        *,
        verbose: bool = False,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.env = isabelle_env
        self.password = str(uuid.uuid4())
        self._server_socket: socket.socket | None = None
        self._prover_socket: socket.socket | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._ready = threading.Event()
        self.verbose = verbose
        self._log: Callable[[str], None] = log or (
            lambda msg: print(msg) if verbose else None
        )  # type: ignore  # print is wider type

        # The port we're listening on
        self.port: int = 0

        # Lazily set by _write_options_yxml after patching
        self._patched_options_yxml: str | None = None

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    @property
    def socket(self) -> socket.socket | None:
        """
        The connected prover socket (available after :meth:`start`).
        """

        return self._prover_socket

    @property
    def stderr(self) -> list[str]:
        """
        Collected stderr lines from the prover process.
        """

        return self._stderr_lines

    def start(self, heap: Path | tuple[Path, ...] | None = None) -> None:
        """
        Launch the prover and establish the PIDE connection.
        """

        match self._setup_environment(heap):
            case Success((tmp, cmd, opts)):
                self._spawn_process(tmp, cmd)
                self._handshake(opts)
            case Failure(e):
                raise RuntimeError(e)
            case _:
                raise RuntimeError("unexpected setup failure")

    def _setup_environment(
        self, heap: Path | tuple[Path, ...] | None
    ) -> Result[tuple[Path, list[str], PIDEInteractiveOptions], str]:
        """
        Set up temp dir, socket, options/session files, and Poly/ML command.
        """

        self._tmpdir = tempfile.TemporaryDirectory(prefix="isabelle_pide_")
        tmp = Path(self._tmpdir.name)

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("127.0.0.1", 0))
        self._server_socket.listen(1)
        self.port = self._server_socket.getsockname()[1]

        opts_res = self._write_options_yxml(tmp)
        if isinstance(opts_res, Failure):
            return opts_res
        opts: PIDEInteractiveOptions = opts_res.unwrap()

        options_path = str(tmp / "options.yxml")

        session_path = Path(tmp) / "session.yxml"
        session_yxml = IsarYXMLUtils.encode_empty_session_yxml()
        with open(session_path, "w", encoding="utf-8") as f:
            f.write(session_yxml)

        self._log(f"PIDE options → {options_path}")
        self._log(f"PIDE session → {session_path}")

        if heap is None:
            heap = self.env.pure_heap
        if isinstance(heap, tuple):
            heap_list_str = ", ".join(_ml_string(str(h)) for h in heap)
            hierarchy_eval = f"(PolyML.SaveState.loadHierarchy [{heap_list_str}]; ())"
            self._log(f"Hierarchy: {heap}")
        else:
            hierarchy_eval = (
                f"(PolyML.SaveState.loadHierarchy [{_ml_string(str(heap))}]; ())"
            )
            self._log(f"Heap: {heap}")

        ml_options = self.env.ml_options.split()
        ml_options = [o for o in ml_options if not o.startswith("--stackspace")]
        if not any("gcthreads" in o for o in ml_options):
            ml_options.extend(["--gcthreads", "2"])

        cmd = (
            [str(self.env.polyml_exe), "-q"]
            + ml_options
            + [
                "--eval",
                hierarchy_eval,
                "--eval",
                "Options.load_default ()",
                "--eval",
                "Resources.init_session_env ()",
                "--eval",
                "Isabelle_Process.init ()",
            ]
        )
        self._log(
            f"Launching: {self.env.polyml_exe} -q ... --eval Isabelle_Process.init ()"
        )
        return Success((tmp, cmd, opts))

    def _spawn_process(self, tmp: Path, cmd: list[str]) -> None:
        """
        Launch the Poly/ML subprocess and start stderr reader.
        """

        env = os.environ.copy()
        env["ISABELLE_PROCESS_OPTIONS"] = str(tmp / "options.yxml")
        env["ISABELLE_INIT_SESSION"] = str(tmp / "session.yxml")
        env["ISABELLE_TMP"] = str(tmp)
        env["POLYSTATSDIR"] = str(tmp)
        env["ML_HOME"] = str(self.env.ml_home)
        env["ML_SYSTEM"] = self.env.ml_system
        env["ML_PLATFORM"] = self.env.ml_platform
        env["ISABELLE_HOME"] = str(self.env.isabelle_home)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=tmp,
        )
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    def _handshake(self, opts: PIDEInteractiveOptions) -> None:
        """
        Accept prover connection, verify password, wait for init, sync options.
        """

        assert self._server_socket is not None
        self._server_socket.settimeout(30.0)
        try:
            self._prover_socket, addr = self._server_socket.accept()
            self._log(f"Prover connected from {addr}")
        except socket.timeout:
            self.cleanup()
            raise TimeoutError("prover did not connect within 30s")

        pw_line = self._read_password_line()
        if pw_line.strip() != self.password:
            self.cleanup()
            raise ValueError(
                f"password mismatch: expected {self.password!r}, got {pw_line!r}"
            )

        self._wait_for_init()
        self._log("PIDE session initialized")
        self._sync_options(opts)

    def _wait_for_init(self, timeout: float = 60.0) -> Markup:
        """
        Wait for the ``init`` message from the prover.
        """

        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.read_protocol_message(
                timeout=min(5.0, deadline - time.time())
            ).value_or(None)
            if msg is None:
                time.sleep(0.05)
                continue
            if msg.name == MarkupKind.INIT:
                welcome = YXMLElemModel.content_of(msg.body)
                self._log(f"Welcome: {welcome}")
                return msg
            if msg.name in (MarkupKind.PROTOCOL, MarkupKind.SYSTEM):
                self._handle_system_message(msg)
                continue

        raise TimeoutError("did not receive init message within timeout")

    def _sync_options(self, opts: PIDEInteractiveOptions) -> None:
        """Send ``Prover.options`` protocol command.

        This triggers ``Isabelle_Process.init_options_interactive()`` in the ML
        process, which is the ONLY place runtime ML state (threads,
        parallel_proofs, print_depth, etc.) gets set in PIDE mode.
        Must be called early, right after ``_wait_for_init()``.

        Uses the raw patched YXML from ``_write_options_yxml`` (which preserves
        all options) instead of re-encoding from the Pydantic model (which drops
        options not in the 100-field ``PIDEInteractiveOptions`` class).
        """
        full_yxml = self._patched_options_yxml or opts.encode_yxml()
        self.send_command("Prover.options", [full_yxml])

    def _read_password_line(self, timeout: float = 10.0) -> str:
        """
        Read the password line from the prover.
        """

        assert self._prover_socket is not None
        buf = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                b = self._prover_socket.recv(1)
            except socket.timeout:
                continue  # expected in poll loop, retry
            if not b:
                raise ConnectionError(
                    "prover closed connection during password exchange"
                )
            if b[0] == 10:  # \n
                break
            buf.append(b[0])
        result = bytes(buf).decode("utf-8")
        if result.endswith("\r"):
            result = result[:-1]
        return result

    def _read_stderr(self) -> None:
        """
        Read stderr from the prover, watching for STX (0x02) and errors.
        """

        pending: list[bytes] = []
        try:
            while self._process and self._process.poll() is None:
                if self._process.stderr is None:
                    break
                byte = self._process.stderr.read(1)
                if not byte:
                    break
                if byte == b"\x02":  # STX — prover is ready
                    self._ready.set()
                elif byte == b"\n":
                    if pending:
                        line = b"".join(pending).decode("utf-8", errors="replace")
                        self._stderr_lines.append(line)
                        pending.clear()
                else:
                    pending.append(byte)
        except Exception:
            pass  # daemon thread — errors are logged via stderr_lines

    def _write_options_yxml(self, tmp: Path) -> Result[PIDEInteractiveOptions, str]:
        """Write the full options YXML file and return the options model.

        Shells out to ``isabelle options -x`` to dump all system defaults,
        then updates the three custom options required for the PIDE
        connection (``system_channel_address``, ``system_channel_password``,
        ``editor_output_state``).

        Uses ``PIDEInteractiveOptions.patch_yxml`` to modify the raw YXML
        directly so that unknown options are preserved — the model class has
        only 100 of 333+ options.
        """
        isabelle_bin = self.env.isabelle_home / "bin" / "isabelle"

        # Dump full system defaults
        dump_path = Path(tmp) / "options_dump.yxml"
        result = None
        try:
            result = subprocess.run(
                [isabelle_bin, "options", "-x", dump_path],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "ISABELLE_HOME": str(self.env.isabelle_home)},
            )
            result.check_returncode()
            defaults_yxml = dump_path.read_text(encoding="utf-8")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr_info = result.stderr if result else str(exc)
            return Failure(f"Failed to dump Isabelle system options: {stderr_info}")

        # Patch the raw YXML directly — preserves ALL options, not just the
        # 100 fields known to PIDEInteractiveOptions (which silently drops
        # 233+ options like ``profiling`` that loaded heaps depend on).
        patched_yxml = PIDEInteractiveOptions.patch_yxml(
            defaults_yxml,
            {
                "system_channel_address": self.address,
                "system_channel_password": self.password,
                "editor_output_state": "true",
            },
        )

        # Write patched YXML to file for ISABELLE_PROCESS_OPTIONS
        options_path = Path(tmp) / "options.yxml"
        options_path.write_text(patched_yxml, encoding="utf-8")

        # Store the full patched YXML for _sync_options
        self._patched_options_yxml = patched_yxml

        # Parse into typed model for Python-side convenience
        opts = PIDEInteractiveOptions.from_yxml(patched_yxml)
        return Success(opts)

    def read_message_raw(
        self, timeout: float | None = None
    ) -> Result[list[bytes], str]:
        """
        Read one raw multi-chunk message from the prover.
        """

        if self._prover_socket is None:
            return Failure("no socket")
        return read_message(self._prover_socket, timeout=timeout)

    def read_protocol_message(
        self, timeout: float | None = None
    ) -> Result[Markup, str]:
        """
        Read and parse one protocol message.
        """

        result = self.read_message_raw(timeout=timeout)
        match result:
            case Success(c):
                try:
                    return Success(Markup.model_validate(c))
                except (ValueError, YXMLParseError) as e:
                    return Failure(str(e))
            case Failure(e):
                return Failure(str(e))
            case _:
                return Failure("unexpected")

    def send_command(self, name: str, args: list[str]) -> None:
        """Send a protocol command to the prover.

        *args* are strings, each encoded as UTF-8 and sent as a separate chunk.
        """
        if self._prover_socket is None:
            raise RuntimeError("prover not connected")
        chunks = IsarYXMLUtils.build_command_message(name, args)
        write_message(self._prover_socket, chunks)

    def send_message(self, chunks: list[bytes]) -> None:
        """
        Send pre-built message chunks to the prover.
        """

        if self._prover_socket is None:
            raise RuntimeError("prover not connected")
        write_message(self._prover_socket, chunks)

    def send_command_raw(self, name: str, args: list[bytes]) -> None:
        """Send a protocol command with pre-encoded byte chunks.

        *args* are raw byte chunks (not YXML-encoded by us; the prover
        receives them as-is). Use for ``Document.define_commands``,
        ``Document.update``, etc.
        """
        if self._prover_socket is None:
            raise RuntimeError("prover not connected")
        chunks = [name.encode("utf-8")] + args
        write_message(self._prover_socket, chunks)

    def _handle_system_message(self, msg: Markup) -> None:
        """
        Handle internal protocol/system messages.
        """

        if msg.name == MarkupKind.PROTOCOL:
            func = msg.attrs.get("function", "?")
            assert isinstance(func, str)
            if func in (MarkupKind.ML_statistics, MarkupKind.command_timing):
                return  # silently ignore periodic stats
            elif func == MarkupKind.removed_versions:
                return
            else:
                body_text = YXMLElemModel.content_of(msg.body) if msg.body else ""
                self._log(f"protocol {func}: {body_text[:200]}")

    # ---- high-level interface --------------------------------------------

    def protocol_command(self, name: str, *args: str) -> None:
        """Send a protocol command (Scala-style).

        Example::

            prover.protocol_command("Prover.echo", "hello world")
        """
        self.send_command(name, list(args))

    def read_messages(
        self, timeout: float = 1.0, *, skip_stats: bool = True
    ) -> list[Markup]:
        """Read all available messages within *timeout* seconds.

        Returns a list of :class:`Markup`, empty if none arrived.
        ML statistics messages are silently skipped when *skip_stats* is True.
        """
        messages: list[Markup] = []
        deadline = time.time() + timeout

        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            msg = self.read_protocol_message(timeout=min(remaining, 0.5)).value_or(None)
            if msg is None:
                break
            if skip_stats and msg.name == MarkupKind.PROTOCOL:
                func = msg.attrs.get("function", "")
                assert isinstance(func, str)
                if func in (MarkupKind.ML_statistics, MarkupKind.command_timing):
                    continue
            messages.append(msg)

        return messages

    def wait_for_protocol(self, function_name: str, timeout: float = 10.0) -> Markup:
        """
        Wait for a PROTOCOL message with a specific function name.
        """

        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            msg = self.read_protocol_message(timeout=min(remaining, 1.0)).value_or(None)
            if msg is None:
                time.sleep(0.05)
                continue
            if msg.name == MarkupKind.PROTOCOL:
                func = msg.attrs.get("function", "")
                assert isinstance(func, str)
                if func == function_name:
                    return msg
            self._handle_system_message(msg)
        raise TimeoutError(
            f"did not receive protocol '{function_name}' within {timeout}s"
        )

    def iterate_messages(
        self, callback: Callable[[Markup], bool], timeout: float | None = None
    ) -> None:
        """Read messages repeatedly, calling *callback* for each.

        The callback should return True to continue, False to stop.
        If *timeout* is set, stop after that many seconds of inactivity.
        """
        deadline = (time.time() + timeout) if timeout else None
        while True:
            remaining = (deadline - time.time()) if deadline else 1.0
            if remaining <= 0:
                break
            msg = self.read_protocol_message(timeout=min(remaining, 1.0)).value_or(None)
            if msg is None:
                if deadline:
                    break
                time.sleep(0.05)
                continue
            if not callback(msg):
                break

    def cleanup(self) -> None:
        """
        Clean up resources.
        """

        if self._prover_socket:
            try:
                self._prover_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._prover_socket.close()
            self._prover_socket = None

        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None

        if self._process:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            except ProcessLookupError:
                pass
            self._process = None

        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ml_string(s: str) -> str:
    """
    Format a string as an ML string literal.
    """

    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Session source integrity
# ---------------------------------------------------------------------------


class SessionVerifier:
    """
    Session source integrity verification and ISABELLE_HOME discovery.
    """

    @staticmethod
    def read_sources(db_path: Path) -> list[tuple[str, str]]:
        """Read the ``isabelle_sources`` table from a session ``.db`` file.

        Returns ``[(logical_path, sha1_hex), ...]`` where *logical_path* uses
        the ``~~/`` prefix convention (``~~`` = Isabelle home directory).
        """
        import sqlite3

        db = sqlite3.connect(str(db_path))
        rows = db.execute(
            "SELECT name, digest FROM isabelle_sources ORDER BY name"
        ).fetchall()
        db.close()
        return [(name, digest) for name, digest in rows]

    @staticmethod
    def recompute_sha1(source_path: Path, isabelle_home: Path) -> str | None:
        """Recompute the SHA1 digest of a single source file.

        *source_path* may use the ``~~/`` prefix (which is resolved relative to
        *isabelle_home*).  Returns the hex digest string, or **None** if the file
        does not exist.
        """
        import hashlib

        resolved = SessionVerifier._resolve_path(source_path, isabelle_home)
        if not resolved or not resolved.is_file():
            return None
        data = resolved.read_bytes()
        return hashlib.sha1(data).hexdigest()

    @staticmethod
    def verify_sources(
        db_path: Path,
        isabelle_home: Path,
    ) -> list[tuple[str, str, str, bool]]:
        """Compare stored SHA1 digests against recomputed ones.

        Returns ``[(logical_path, stored_sha1, actual_sha1, ok), ...]``
        where *ok* is True if they match (or if the source file is missing
        a flag is set — the actual_sha1 is None in that case).
        """
        sources = SessionVerifier.read_sources(db_path)
        results: list[tuple[str, str, str, bool]] = []
        for logical_path, stored_sha1 in sources:
            actual = SessionVerifier.recompute_sha1(Path(logical_path), isabelle_home)
            if actual is None:
                results.append((logical_path, stored_sha1, "", False))
            else:
                results.append(
                    (logical_path, stored_sha1, actual, actual == stored_sha1)
                )
        return results

    @staticmethod
    def _resolve_path(source_path: Path, isabelle_home: Path) -> Path:
        """
        Resolve a source path (possibly with the ``~~/`` prefix) to an absolute path.
        """

        s = str(source_path)
        if s.startswith("~~/"):
            return isabelle_home / s[3:]
        return Path(s)

    @staticmethod
    def _which_isabelle() -> Result[Path, str]:
        """
        Resolve ISABELLE_HOME via ``which isabelle``.
        """

        try:
            result = subprocess.run(
                ["which", "isabelle"], capture_output=True, text=True, timeout=5
            )
        except Exception as e:
            return Failure(str(e))
        if result.returncode != 0:
            return Failure(f"which isabelle exited with code {result.returncode}")
        p = Path(result.stdout.strip()).resolve()
        candidate = p.parent.parent
        if not (candidate / "bin" / "isabelle").is_file():
            return Failure(f"no isabelle binary at {candidate}/bin/isabelle")
        return Success(candidate)

    @staticmethod
    def discover_home() -> Maybe[Path]:
        """
        Try to discover ISABELLE_HOME from common locations.
        """

        if "ISABELLE_HOME" in os.environ:
            return Some(Path(os.environ["ISABELLE_HOME"]))
        match SessionVerifier._which_isabelle():
            case Success(p):
                return Some(p)
            case _:
                return Nothing


class SessionInfo(BaseModel):
    """
    Resolved session information for a .thy file.
    """

    name: str
    session_dir: DirectoryPath
    root_file: FilePath
    dependencies: list[str] = []


class _RootSession(TypedDict):
    """
    Raw session entry parsed from a ROOT file.
    """

    name: str
    path: str
    dirs: list[str]


class SessionResolver:
    """
    Session resolution — ROOT/ROOTS parsing, dependency resolution, heap verification.
    """

    _SESSION_RE = re.compile(
        r"^\s*session\s+(\S+)"  # name
        r"(?:\s*\([^)]*\))?"  # optional groups
        r"(?:\s+in\s+(\S+))?"  # optional path override
        r"\s*="
    )

    @staticmethod
    def parse_roots(roots_path: Path) -> list[str]:
        """
        Parse a ROOTS file, returning directory entries.
        """

        entries: list[str] = []
        for raw in roots_path.read_text(encoding="utf-8").split("\n"):
            line = raw.strip()
            if line and not line.startswith("#"):
                entries.append(line)
        return entries

    @staticmethod
    def parse_root_sessions(root_path: Path) -> list[_RootSession]:
        """
        Parse a ROOT file, returning session info dicts.
        """

        text = root_path.read_text(encoding="utf-8")
        sessions: list[_RootSession] = []
        current: _RootSession | None = None
        state = "idle"

        for raw in text.split("\n"):
            m = SessionResolver._SESSION_RE.match(raw)
            if m:
                current = {
                    "name": m.group(1).strip('"'),
                    "path": (m.group(2) or "").strip('"'),
                    "dirs": [],
                }
                sessions.append(current)
                state = "idle"
                continue

            if current is None:
                continue

            stripped = raw.strip()

            if stripped == "directories":
                state = "dirs"
                continue

            if state == "dirs":
                if not raw or stripped.startswith("#"):
                    continue
                if stripped.startswith('"') and stripped.endswith('"'):
                    current["dirs"].append(stripped[1:-1])
                elif stripped and not raw.startswith((" ", "\t")):
                    state = "idle"

        return sessions

    @staticmethod
    def _walk_up_collect(
        thy_path: Path, bound: Path | None = None
    ) -> tuple[list[Path], list[Path]]:
        """
        Walk up from ``thy_path`` collecting ROOTS and ROOT file paths.
        """

        roots_files: list[Path] = []
        root_files: list[Path] = []
        current = thy_path.resolve().parent

        while True:
            rts = current / "ROOTS"
            if rts.is_file():
                roots_files.append(rts)
            rt = current / "ROOT"
            if rt.is_file():
                root_files.append(rt)
            if bound and current.samefile(bound):
                break
            if current.parent == current:
                break
            current = current.parent

        return roots_files, root_files

    @staticmethod
    def _is_subdir(child: Path, parent: Path) -> bool:
        """
        Return True if *child* is *parent* or a subdirectory of *parent*.
        """

        try:
            child.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _session_dirs(session: _RootSession, root_dir: Path) -> list[Path]:
        """
        Compute all directories that a session covers.
        """

        sp = session["path"]
        base = root_dir / sp if sp else root_dir
        return [base] + [base / d for d in session.get("dirs", [])]

    @staticmethod
    def _session_contains_thy(
        session: _RootSession, root_dir: Path, thy_path: Path
    ) -> int | None:
        """Check if a session's directory tree contains the .thy file.

        Returns the depth (number of path components) of the matching base dir,
        or ``None`` if no match.  Longer = more specific.
        """
        thy_parent = thy_path.resolve().parent
        for d in SessionResolver._session_dirs(session, root_dir):
            if SessionResolver._is_subdir(thy_parent, d):
                return len(d.parts)
        return None

    @staticmethod
    def _match_session(
        root_files: list[Path], thy_path: Path
    ) -> tuple[_RootSession, Path, Path] | None:
        """Find the most specific session that contains *thy_path*.

        Returns ``(session_dict, root_dir, root_file)`` or **None**.
        Picks the session with the deepest matching base directory.
        """
        best: tuple[int, _RootSession, Path, Path] | None = None

        for rf in root_files:
            root_dir = rf.parent.resolve()
            for session in SessionResolver.parse_root_sessions(rf):
                depth = SessionResolver._session_contains_thy(
                    session, root_dir, thy_path
                )
                if depth is not None and (best is None or depth > best[0]):
                    best = (depth, session, root_dir, rf)

        if best is None:
            return None
        return (best[1], best[2], best[3])

    @staticmethod
    def _roots_contains(roots_file: Path, target_dir: Path) -> bool:
        """
        Check if any ROOTS entry's subtree contains *target_dir*.
        """

        roots_dir = roots_file.parent.resolve()
        return any(
            (roots_dir / entry).resolve() == target_dir
            or SessionResolver._is_subdir(target_dir, (roots_dir / entry).resolve())
            for entry in SessionResolver.parse_roots(roots_file)
        )

    @staticmethod
    def _find_session_dir(roots_files: list[Path], root_file: Path) -> Path:
        """Determine session_dir: highest ROOTS file's parent that covers the
        ROOT file, else the ROOT file's parent dir."""
        root_parent = root_file.parent.resolve()

        # Sort shallowest first = closest to filesystem root
        sorted_roots = sorted(roots_files, key=lambda p: len(p.parent.parts))

        for rf in sorted_roots:
            if SessionResolver._roots_contains(rf, root_parent):
                return rf.parent.resolve()

        return root_parent

    @staticmethod
    def resolve(thy_path: Path, session_dir_bound: Path | None = None) -> SessionInfo:
        """Resolve the session that contains *thy_path*.

        Walks up from the .thy file's parent directory, finds ROOT/ROOTS files,
        determines the session and session directory.

        Raises ``RuntimeError`` if no session is found.
        """
        thy = thy_path.resolve()
        roots_files, root_files = SessionResolver._walk_up_collect(
            thy, session_dir_bound
        )

        if not root_files:
            raise RuntimeError(f"No session definition found for {thy}")

        match = SessionResolver._match_session(root_files, thy)
        if match is None:
            names = ", ".join(str(f) for f in root_files)
            raise RuntimeError(f"None of the sessions in {names} contain {thy}")

        session, _, root_file = match
        session_dir = SessionResolver._find_session_dir(roots_files, root_file)

        # Ensure session_dir is valid
        if (
            not (session_dir / "ROOT").is_file()
            and not (session_dir / "ROOTS").is_file()
        ):
            raise RuntimeError(
                f"{session_dir} is not a valid Isabelle session directory "
                "(no ROOT or ROOTS file)"
            )

        return SessionInfo(
            name=session["name"],
            session_dir=session_dir,
            root_file=root_file,
        )

    @staticmethod
    def resolve_dependencies(info: SessionInfo, isabelle_home: Path) -> SessionInfo:
        """
        Populate *info.dependencies* by running ``isabelle sessions -b``.
        """

        isabelle_bin = isabelle_home / "bin/isabelle"
        sd = info.session_dir

        ih = isabelle_home.resolve()
        try:
            sd.resolve().relative_to(ih)
            args = [str(isabelle_bin), "sessions", "-b", info.name]
        except ValueError:
            args = [str(isabelle_bin), "sessions", "-d", str(sd), "-b", info.name]

        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            cmd = " ".join(str(a) for a in args)
            raise RuntimeError(f"isabelle sessions failed: {cmd}\n{result.stderr}")

        sessions = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
        # Last entry is the target session itself; everything before is deps
        info.dependencies = (
            sessions[:-1] if sessions and sessions[-1] == info.name else sessions
        )
        return info

    @staticmethod
    def verify_heaps(dep_names: list[str], env: IsabelleEnv) -> list[Path]:
        """Verify each dependency heap exists and sources are synced.

        Returns list of absolute heap file paths (in dependency order).
        Raises ``RuntimeError`` on the first missing or mismatched heap.
        """
        heaps: list[Path] = []
        for name in dep_names:
            heap_path: Path | None = None
            for base in [env.heaps_system, env.heaps_user]:
                p = base / env.ml_identifier / name
                if p.is_file():
                    heap_path = p
                    break

            if heap_path is None:
                raise RuntimeError(
                    f"Heap '{name}' not found. Build it with: isabelle build -b {name}"
                )

            db_path = heap_path.parent / "log" / f"{name}.db"
            if not db_path.is_file():
                raise RuntimeError(
                    f"Source database for '{name}' not found at {db_path}. "
                    f"The heap may be corrupted or built with an older version."
                )

            results = SessionVerifier.verify_sources(db_path, env.isabelle_home)
            mismatches = [(p, s, a) for p, s, a, ok in results if not ok]
            if mismatches:
                raise RuntimeError(
                    f"Heap '{name}' sources are out of sync: "
                    f"{len(mismatches)}/{len(results)} files mismatched. "
                    f"Rebuild with: isabelle build -b {name}"
                )

            heaps.append(heap_path)

        return heaps
