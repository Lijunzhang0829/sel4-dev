# Scala Tokenizer & Span Parser: Porting Notes

## Source files

| File | Purpose |
|------|---------|
| `General/scan.scala` | Lexicon (char trie), parser combinators (repeated, one, many, literal) |
| `General/symbol.scala` | Symbol classification (`is_letter`, `is_digit`, `is_letdig`, `is_blank`, `is_symbolic`, `is_symbolic_char`) |
| `General/comment.scala` | Formal comment parsers (comment_cartouche) |
| `Isar/keyword.scala` | Keyword kind constants, `Keywords` class with major/minor lexicons, `is_before_command`, `is_quasi_command` |
| `Isar/token.scala` | `Token` dataclass, `Token.explode`, `Token.explode_line` |
| `Isar/outer_syntax.scala` | `Outer_Syntax.parse_spans` — turns tokens into command spans |
| `PIDE/command_span.scala` | `Command_Span.Span`, `Command_Span.Command_Span`, `Ignored_Span`, `Malformed_Span` |
| `Thy/thy_header.scala` | `Thy_Header.bootstrap_header` — bootstrap keyword definitions, `read_tokens` — header token extraction |

## Token.explode priority order

```
delimited_token | other_token(keywords)

delimited_token = string | alt_string | comment | formal_comment | cartouche | control_cartouche

other_token = space | recover_delimited | (keyword ||| ident) | var_ | type_ident | type_var | float | nat_ | sym_ident | bad
```

`|||` = longest match. Both major and minor lexicons are scanned; the longer match wins. If equal length, major wins.

## Keyword classification

### Bootstrap keywords (Thy_Header.bootstrap_header)

| Keyword | Kind | Notes |
|---------|------|-------|
| `(`, `)`, `,`, `::`, `=` | (empty/minor) | Punctuation |
| `and` | (empty/minor) | |
| `begin` | QUASI_COMMAND | Minor lexicon |
| `imports` | QUASI_COMMAND | Minor lexicon |
| `keywords` | QUASI_COMMAND | Minor lexicon |
| `abbrevs` | QUASI_COMMAND | Minor lexicon |
| `chapter`, `section`, `subsection`, `subsubsection`, `paragraph`, `subparagraph` | DOCUMENT_HEADING | Major lexicon |
| `text`, `txt` | DOCUMENT_BODY | Major lexicon |
| `text_raw` | DOCUMENT_RAW | Major lexicon |
| `theory` | THY_BEGIN | Major lexicon, tags=["theory"] |
| `ML` | THY_DECL | Major lexicon, tags=["ML"] |

### BEFORE_COMMAND vs QUASI_COMMAND in parse_spans

The critical distinction in `parse_spans`:

```scala
if (keywords.is_before_command(tok) ||
  tok.is_command &&
    (!content.exists(keywords.is_before_command) || content.exists(_.is_command))) {
  flush(); content += tok
}
else { content ++= ignored; ignored.clear(); content += tok }
```

- `is_before_command` = `is_keyword && kind == "before_command"` — triggers a flush, starts new span
- `is_quasi_command` = `is_keyword && kind == "quasi_command"` — does NOT trigger flush, absorbed into current span
- `QUASI_COMMAND` + empty kind → MINOR lexicon → `is_keyword = true`, `is_command = false`

So `imports` (QUASI_COMMAND) does NOT split the span. The theory header `theory T imports ... begin` is ONE span containing all tokens from `theory` through `begin`.

### Keyword sources

Pure.thy (the theory definition file) declares ~150 keywords with their kinds. These are:
- Parsed from `Pure.thy`'s `keywords ... begin` section during session init
- Added to the keyword table via `Keywords.add_keywords(header)`
- The Scala client dynamically builds the full keyword table by processing headers

## Thy_Header.read_tokens special handling

`read_tokens` (thy_header.scala:169-187):
1. Tokenize with `bootstrap_keywords` only
2. Drop everything before the first `theory` command
3. Collect `tokens1` = everything before `begin`, `tokens2` = `begin`
4. Return `tokens1 ::: tokens2` — parsed together as one Thy_Header

This extracts the theory header (name, imports, keywords, abbrevs) from the raw token stream using only bootstrap keywords.

## parse_spans theory header behavior

With correct keyword kinds:
```
theory (COMMAND, THY_BEGIN) → flush(), content += theory
  SPACE → ignored
  Ackermann (IDENT) → content ++= ignored; content += Ackermann
  SPACE → ignored
  imports (KEYWORD, QUASI_COMMAND) → not before_command, not command → content ++= ignored; content += imports
  SPACE → ignored
  "..." (STRING) → content ++= ignored; content += string
  SPACE → ignored
  "..." (STRING) → content ++= ignored; content += string
  SPACE → ignored
  begin (KEYWORD, QUASI_COMMAND) → content ++= ignored; content += begin
```

Result: ONE span [theory, SPACE, Ackermann, SPACE, imports, SPACE, strings, SPACE, begin] with kind=THY_BEGIN.

## Symbol classification functions

From symbol.scala:

```scala
def is_letter(sym: Symbol): Boolean = symbols.letters.contains(sym)
def is_digit(sym: Symbol): Boolean = sym.length == 1 && '0' <= sym(0) && sym(0) <= '9'
def is_quasi(sym: Symbol): Boolean = sym == "_" || sym == "'"
def is_letdig(sym: Symbol): Boolean = is_letter(sym) || is_digit(sym) || is_quasi(sym)
def is_blank(sym: Symbol): Boolean = symbols.blanks.contains(sym)
def is_symbolic_char(sym: Symbol): Boolean = symbols.sym_chars.contains(sym)
def is_symbolic(sym: Symbol): Boolean =
  !is_open(sym) && !is_close(sym) && (raw_symbolic(sym) || symbols.symbolic.contains(sym))
```

`is_letdig` includes `_` and `'` (quasi characters). This matters for identifiers like `foo_bar'`.

## Lexicon (Scan.Lexicon)

Char-level trie for longest-match keyword scanning. Not a regex trie — each node is a `Map[Char, (String, Tree)]` where:
- `String` = the keyword ending at this node (empty if intermediate node)
- `Tree` = subtree for longer keywords

`scan(in: Reader[Char])` does a forward walk from the current position, returning the longest matching keyword string.

## Key differences between our Python and Scala (all resolved)

1. ~~**`parse_spans` flush**: Our `if starts_new and content:` vs Scala's unconditional `flush()`~~ → Fixed: `if starts_new:` (2026-05-19)
2. ~~**Keyword kinds**: `imports`/`keywords`/`abbrevs`/`and` → QUASI_COMMAND (not BEFORE_COMMAND)~~ → Fixed: match Scala bootstrap (2026-05-19)
3. ~~**Longest keyword match**: We always prefer major; Scala picks max length~~ → Fixed: `|||` semantics (2026-05-19)
4. ~~**Missing full keyword set**: Our BOOTSTRAP_KEYWORDS is sparse and wrong; we need full Pure.thy keywords~~ → Fixed: `_parse_pure_keywords()` parses Pure.thy at runtime; `_HOL_EXTRA_KEYWORDS` stopgap for HOL commands (2026-05-19)
5. ~~**`is_letdig`**: Should include `_` and `'` (quasi characters), matching Scala — not yet causing bugs, low priority~~ → Fixed: `SymbolTable.is_letdig` includes `_` and `'` (2026-05-19)

### Detailed bug descriptions (from debugging log)

The following bugs were identified and fixed during the Edits variant debugging session.
Each bug contributed to the `state_at(76, 1)` call returning `None`.

#### Bug A — `parse_spans` flush skipped when content empty

`if starts_new and content:` skipped `flush()` when content was empty, causing
leading comments/spaces to be absorbed into the first command span.  Scala always
calls `flush()`.  Fix: `if starts_new:`.

#### Bug B — `imports`/`keywords`/`and` classified as BEFORE_COMMAND

Scala's `Thy_Header.bootstrap_header` defines these as `QUASI_COMMAND` (minor
lexicon, does NOT trigger span flush).  We had them as `BEFORE_COMMAND` (triggers
flush → theory header split).  Fix: match Scala bootstrap kinds.

#### Bug C — Case-sensitive kind comparison

Pure.thy uses lowercase `"quasi_command"`/`"before_command"`.  Our `is_major_kind`
and `is_before_command` checks used uppercase.  Keywords like `where` (kind=
`"quasi_command"` from Pure.thy) ended up in the major lexicon, classified as
COMMAND, and split spans.  Fix: case-insensitive comparison.

#### Bug D — Keyword vs identifier longest-match

`induction` was split into `in` (minor keyword) + `duction` (ident) because
keyword match was tried before ident.  Scala uses `keyword ||| ident` — scans both,
picks the longer match.  Fix: scan both, longer wins.

#### Bug E — `XML.content_of` not recursive

Only extracted Text from top-level body elements, missing all text in nested YXML.
WRITELN messages appeared empty.  Fix: recurse into Elem children.

#### Bug F — `_span_at_offset` gap handling

When cursor landed in whitespace between two named spans, returned None.
Fix: return nearest preceding named span.
