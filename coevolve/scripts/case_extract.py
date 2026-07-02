#!/usr/bin/env python3
"""case_extract.py — fork-① harness, deterministic half.

For a co-change l4v commit C (artifact + proof in one diff):
  split diff -> delta_artifact.diff (trigger) + delta_proof.diff (held-out)
  parse the proof half -> ground_truth.json:
      which proof files were touched, which lemma blocks, and per lemma:
      statement_evolved / body_changed / kind(added|deleted|modified)

Ground truth = the human fix C_p (here: the proof half of the same commit).
Python 3.8 compatible. Usage:
  python3 case_extract.py --l4v <repo> --commit <hash> --out <dir>
"""
import argparse, json, os, re, subprocess, sys

ARTIFACT_PREFIXES = ("spec/",)
PROOF_PREFIXES = ("proof/",)

SESSION_MAP = [
    ("proof/invariant-abstract/", "AInvs"), ("proof/refine/", "Refine"),
    ("proof/crefine/", "CRefine"), ("proof/access-control/", "Access"),
    ("proof/infoflow/", "InfoFlow"), ("proof/drefine/", "DRefine"),
    ("proof/bisim/", "Bisim"),
]

LEMMA_KEYWORDS = ("lemma", "theorem", "corollary", "proposition", "schematic_goal")
# tokens that begin the PROOF BODY when found at line start (stripped)
BODY_START = re.compile(
    r"^(apply|proof|by|using|unfolding|supply|including|subgoal|oops|sorry|done)\b")
# same-line proof tail: lemma foo: "..." by simp
INLINE_BODY = re.compile(r'("\s+)(by|using|apply)\b')
BLOCK_START = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_']*)")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True, check=True).stdout


def classify(path):
    if any(path.startswith(p) for p in ARTIFACT_PREFIXES):
        return "artifact"
    if any(path.startswith(p) for p in PROOF_PREFIXES):
        return "proof"
    return "other"


def session_of(path):
    for pref, s in SESSION_MAP:
        if path.startswith(pref):
            return s
    return "proof-other"


def parse_blocks(text):
    """Split a .thy file into top-level blocks.
    Returns list of dicts {kw, name, start, end(excl), lines}."""
    lines = text.split("\n")
    starts = []
    for i, ln in enumerate(lines):
        m = BLOCK_START.match(ln)
        if m:
            starts.append((i, m.group(1)))
    blocks = []
    for j, (i, kw) in enumerate(starts):
        end = starts[j + 1][0] if j + 1 < len(starts) else len(lines)
        name = None
        if kw in LEMMA_KEYWORDS or kw == "lemmas":
            rest = lines[i][len(kw):].strip()
            tok = re.split(r"[\s\[:\"(]", rest, 1)[0] if rest else ""
            if tok and NAME_RE.match(tok):
                name = tok
        blocks.append({"kw": kw, "name": name, "start": i, "end": end,
                       "lines": lines[i:end]})
    return blocks


def split_stmt_body(block_lines):
    """Return (statement_text, body_text) for a lemma block."""
    stmt, body = [], []
    in_body = False
    for k, ln in enumerate(block_lines):
        if not in_body and k > 0 and BODY_START.match(ln.strip()):
            in_body = True
        if not in_body:
            m = INLINE_BODY.search(ln)
            if m:  # statement and proof share this line
                cut = m.start(2)
                stmt.append(ln[:cut])
                body.append(ln[cut:])
                in_body = True
                continue
        (body if in_body else stmt).append(ln)
    norm = lambda ls: re.sub(r"\s+", " ", "\n".join(ls)).strip()
    return norm(stmt), norm(body)


def hunk_ranges(diff_text):
    """Yield (pre_start, pre_count, post_start, post_count) per hunk (1-based)."""
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
                         diff_text, re.M):
        a, b, c, d = m.groups()
        yield (int(a), int(b or "1"), int(c), int(d or "1"))


def touched_names(blocks, ranges):
    """Given blocks and 1-based (start,count) line ranges, return the set of
    (kw,name-or-position) block keys they intersect."""
    out = set()
    for (s, c) in ranges:
        if c == 0:
            continue
        lo, hi = s - 1, s - 1 + c  # 0-based [lo,hi)
        for b in blocks:
            if b["start"] < hi and lo < b["end"]:
                key = b["name"] if b["name"] else "%s@%d" % (b["kw"], b["start"] + 1)
                out.add((b["kw"], key))
    return out


def analyze_proof_file(repo, parent, commit, path):
    pre_text = git(repo, "show", "%s:%s" % (parent, path)) \
        if path_in(repo, parent, path) else ""
    post_text = git(repo, "show", "%s:%s" % (commit, path)) \
        if path_in(repo, commit, path) else ""
    pre_blocks, post_blocks = parse_blocks(pre_text), parse_blocks(post_text)
    diff = git(repo, "diff", "-U0", parent, commit, "--", path)
    pre_r, post_r = [], []
    for (a, b, c, d) in hunk_ranges(diff):
        pre_r.append((a, b)); post_r.append((c, d))
    touched = touched_names(pre_blocks, pre_r) | touched_names(post_blocks, post_r)

    pre_by = {}
    for b in pre_blocks:
        if b["name"]:
            pre_by[(b["kw"], b["name"])] = b
    post_by = {}
    for b in post_blocks:
        if b["name"]:
            post_by[(b["kw"], b["name"])] = b

    lemmas = {}
    for (kw, key) in sorted(touched):
        entry = {"kw": kw}
        pb, qb = pre_by.get((kw, key)), post_by.get((kw, key))
        if kw in LEMMA_KEYWORDS and pb and qb:
            ps, pbo = split_stmt_body(pb["lines"])
            qs, qbo = split_stmt_body(qb["lines"])
            kind = "modified" if (ps != qs or pbo != qbo) else "relocated"
            entry.update(kind=kind,
                         statement_evolved=(ps != qs), body_changed=(pbo != qbo),
                         stmt_pre=ps[:400], stmt_post=qs[:400])
        elif kw in LEMMA_KEYWORDS and qb and not pb:
            qs, _ = split_stmt_body(qb["lines"])
            entry.update(kind="added", statement_evolved=True,
                         body_changed=True, stmt_post=qs[:400])
        elif kw in LEMMA_KEYWORDS and pb and not qb:
            entry.update(kind="deleted", statement_evolved=True, body_changed=True)
        else:
            entry.update(kind="other-block")
        lemmas[key] = entry
    return lemmas


def path_in(repo, rev, path):
    r = subprocess.run(["git", "-C", repo, "cat-file", "-e", "%s:%s" % (rev, path)],
                       capture_output=True)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l4v", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    repo = args.l4v

    full = git(repo, "rev-parse", args.commit).strip()
    parent = git(repo, "rev-parse", full + "^").strip()
    meta_line = git(repo, "log", "-1", "--format=%H%x09%ci%x09%s", full).strip()
    _, date, subject = meta_line.split("\t", 2)
    files = [f for f in git(repo, "diff", "--name-only", parent, full).split("\n") if f]

    arts = [f for f in files if classify(f) == "artifact"]
    proofs = [f for f in files if classify(f) == "proof"]
    others = [f for f in files if classify(f) == "other"]
    if not arts or not proofs:
        print("NOT a co-change commit (artifact=%d proof=%d) — abort" %
              (len(arts), len(proofs)))
        sys.exit(2)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "delta_artifact.diff"), "w") as f:
        f.write(git(repo, "diff", parent, full, "--", *arts))
    with open(os.path.join(args.out, "delta_proof.diff"), "w") as f:
        f.write(git(repo, "diff", parent, full, "--", *proofs))

    gt = {}
    for p in proofs:
        gt[p] = {"session": session_of(p),
                 "lemmas": analyze_proof_file(repo, parent, full, p)}

    meta = {"commit": full, "parent": parent, "date": date, "subject": subject,
            "artifact_files": arts, "proof_files": proofs, "other_files": others,
            "sessions": sorted({session_of(p) for p in proofs})}
    json.dump(meta, open(os.path.join(args.out, "meta.json"), "w"),
              indent=1, ensure_ascii=False)
    json.dump(gt, open(os.path.join(args.out, "ground_truth.json"), "w"),
              indent=1, ensure_ascii=False)

    n_lem = sum(len(v["lemmas"]) for v in gt.values())
    n_evo = sum(1 for v in gt.values() for e in v["lemmas"].values()
                if e.get("statement_evolved"))
    print("OK %s | %s" % (full[:9], subject[:60]))
    print("   artifact=%d proof=%d other=%d | lemma-blocks touched=%d, statement_evolved=%d"
          % (len(arts), len(proofs), len(others), n_lem, n_evo))


if __name__ == "__main__":
    main()
