"""Parse l4v ROOT files into a session DAG + theory directory map.

Outputs a dict:
  {
    sessions: {
      name: {
        parent: str | None,
        imported_sessions: [str, ...],
        session_dir: "<abs path>",     # l4v_root + 'in' path, $L4V_ARCH expanded
        directories: ["<abs path>", ...],   # extra theory search dirs
        unconditional_theories: [str, ...], # theory names from the unconditional `theories` block
        root_file: "<abs path>",
      },
      ...
    },
    theory_search_paths: {
      "<abs dir>": [session_name, ...],   # which sessions own this dir
    }
  }

Designed for the build pipeline's L4V_ARCH=ARM. Pass arch=... to override.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Order matters: longest first so $L4V_ARCH does not eat $L4V (none exists, but be safe)
_VAR_RE = re.compile(r"\$(L4V_ARCH|ISABELLE_HOME|L4V_HOME)")


def _expand(s: str, env: dict[str, str]) -> str:
    return _VAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), s)


# Strip Isabelle (* ... *) block comments, including nested ones.
def strip_isabelle_comments(src: str) -> str:
    out = []
    i = 0
    depth = 0
    n = len(src)
    while i < n:
        if depth == 0 and src.startswith("(*", i):
            depth = 1
            i += 2
            continue
        if depth > 0:
            if src.startswith("(*", i):
                depth += 1
                i += 2
                continue
            if src.startswith("*)", i):
                depth -= 1
                i += 2
                continue
            i += 1
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


# A `session NAME in "DIR" = PARENT +` header. PARENT and "in DIR" are optional in
# theory; in l4v they are always present, but be tolerant.
_SESSION_HEADER_RE = re.compile(
    r"""
    \bsession\s+
    (?:"(?P<name_q>[^"]+)"|(?P<name>[A-Za-z_][A-Za-z0-9_'-]*))
    (?:\s*\([^)]*\))?                             # optional `(group, ...)` tags — discarded
    (?:\s+in\s+(?:"(?P<dir_q>[^"]+)"|(?P<dir_u>[A-Za-z_][\w./$-]*)))?
    (?:\s*=\s*(?:"(?P<parent_q>[^"]+)"|(?P<parent>[A-Za-z_][A-Za-z0-9_'-]*)))?
    \s*\+
    """,
    re.VERBOSE,
)


# A run of quoted strings (theory paths). Used inside `theories ...` and `directories ...`.
_QSTRING_RE = re.compile(r'"([^"]*)"')


def _split_sessions(clean_root: str) -> list[tuple[int, re.Match]]:
    """Return list of (start_offset, header_match) for each session in the file."""
    return [(m.start(), m) for m in _SESSION_HEADER_RE.finditer(clean_root)]


def _section_text(clean_root: str, header_end: int, next_session_start: int) -> str:
    return clean_root[header_end:next_session_start]


# Find each `<keyword> [optional [...]] <quoted strings>` block within a session body.
# kw is one of `theories`, `directories`, `sessions`.
def _collect_blocks(body: str, kw: str) -> list[tuple[bool, list[str]]]:
    """Return list of (has_condition, items). For `sessions` block, items are bare names
    (not quoted), so we handle that specially."""
    out = []
    pos = 0
    pat = re.compile(r"\b" + kw + r"\b")
    while True:
        m = pat.search(body, pos)
        if not m:
            break
        # skip whitespace, optional [ ... ]
        i = m.end()
        # consume whitespace
        while i < len(body) and body[i].isspace():
            i += 1
        has_condition = False
        if i < len(body) and body[i] == "[":
            depth = 1
            i += 1
            while i < len(body) and depth > 0:
                if body[i] == "[":
                    depth += 1
                elif body[i] == "]":
                    depth -= 1
                i += 1
            has_condition = True
        # Now collect either quoted strings (for theories/directories) or bare names (for sessions),
        # until the next outer block keyword or end-of-section.
        # We stop when we hit another block keyword or the end of body.
        stop_re = re.compile(
            r"\b(theories|directories|sessions|description|document_files|export_files|export_classpath)\b"
        )
        # Find the next stop within (i, end_of_body)
        end_match = stop_re.search(body, i)
        end = end_match.start() if end_match else len(body)
        chunk = body[i:end]

        items: list[str] = []
        if kw == "sessions":
            # strip Isabelle \<open>...\<close> just in case
            chunk_clean = re.sub(r"\\<open>.*?\\<close>", " ", chunk, flags=re.DOTALL)
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_']*", chunk_clean):
                items.append(tok)
        else:
            for sm in _QSTRING_RE.finditer(chunk):
                items.append(sm.group(1))
        out.append((has_condition, items))
        pos = end
    return out


def parse_root_file(path: Path, l4v_root: Path, env: dict[str, str]) -> list[dict]:
    raw = path.read_text(errors="replace")
    clean = strip_isabelle_comments(raw)
    headers = _split_sessions(clean)
    sessions = []
    for idx, (start, m) in enumerate(headers):
        next_start = headers[idx + 1][0] if idx + 1 < len(headers) else len(clean)
        body = _section_text(clean, m.end(), next_start)

        name = m.group("name_q") or m.group("name")
        rel_dir = m.group("dir_q") or m.group("dir_u") or ""
        parent = m.group("parent_q") or m.group("parent")

        # Resolve session_dir: if rel_dir is absolute-ish use as-is, else relative to ROOT file's dir.
        rel_dir_expanded = _expand(rel_dir, env)
        if rel_dir_expanded:
            session_dir = (path.parent / rel_dir_expanded).resolve()
        else:
            session_dir = path.parent.resolve()

        # directories block
        dir_blocks = _collect_blocks(body, "directories")
        extra_dirs = []
        for _has_cond, items in dir_blocks:
            for d in items:
                d_expanded = _expand(d, env)
                extra_dirs.append((session_dir / d_expanded).resolve())

        # imported sessions
        sess_blocks = _collect_blocks(body, "sessions")
        imported = []
        for _has_cond, items in sess_blocks:
            imported.extend(items)

        # theories: take the LAST unconditional block; fall back to any if none.
        theory_blocks = _collect_blocks(body, "theories")
        unconditional = None
        for has_cond, items in theory_blocks:
            if not has_cond:
                unconditional = items
        if unconditional is None and theory_blocks:
            unconditional = theory_blocks[-1][1]
        unconditional = unconditional or []
        # expand $L4V_ARCH inside theory paths
        unconditional = [_expand(t, env) for t in unconditional]

        sessions.append(
            dict(
                name=name,
                parent=parent,
                imported_sessions=imported,
                session_dir=str(session_dir),
                directories=[str(d) for d in extra_dirs],
                unconditional_theories=unconditional,
                root_file=str(path),
            )
        )
    return sessions


def parse_all_roots(l4v_root: Path, arch: str = "ARM") -> dict:
    env = {
        "L4V_ARCH": arch,
        "L4V_HOME": str(l4v_root),
        "ISABELLE_HOME": os.environ.get("ISABELLE_HOME", ""),
    }
    sessions: dict[str, dict] = {}
    duplicate_names: list[tuple[str, str]] = []
    for root_file in sorted(l4v_root.rglob("ROOT")):
        if not root_file.is_file():
            continue
        try:
            for sess in parse_root_file(root_file, l4v_root, env):
                name = sess["name"]
                if name in sessions:
                    duplicate_names.append((name, sess["root_file"]))
                    # keep the first one; record duplicate
                    continue
                sessions[name] = sess
        except Exception as e:  # parser is heuristic — never crash on a weird ROOT
            print(f"[warn] failed to parse {root_file}: {e}")

    # Build theory_search_paths: dir → [session_name, ...]
    theory_search_paths: dict[str, list[str]] = {}
    for name, sess in sessions.items():
        for d in [sess["session_dir"], *sess["directories"]]:
            theory_search_paths.setdefault(d, []).append(name)

    return dict(
        sessions=sessions,
        theory_search_paths=theory_search_paths,
        duplicate_names=duplicate_names,
        arch=arch,
        l4v_root=str(l4v_root),
    )


def assign_session_for_thy(thy_path: Path, parsed: dict) -> str | None:
    """Map a .thy file to its owning session by deepest session_dir prefix match.

    Returns session name or None if no session claims this directory.
    """
    abs_p = thy_path.resolve()
    best: tuple[int, str] | None = None
    for d, names in parsed["theory_search_paths"].items():
        try:
            d_path = Path(d)
            # py3.8-safe containment test: relative_to raises ValueError when
            # not contained, which the enclosing except already treats as skip
            # (Path.is_relative_to needs 3.9+; server B runs 3.8).
            abs_p.relative_to(d_path)
            depth = len(d_path.parts)
            if best is None or depth > best[0]:
                # If multiple sessions share same dir, just pick the first deterministically.
                best = (depth, sorted(names)[0])
        except ValueError:
            continue
    return best[1] if best else None


if __name__ == "__main__":
    import json
    import sys

    l4v = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("verification/l4v")
    out = parse_all_roots(l4v.resolve())
    print(json.dumps(out, indent=2, default=str))
