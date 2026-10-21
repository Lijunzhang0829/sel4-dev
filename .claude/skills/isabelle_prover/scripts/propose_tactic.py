#!/usr/bin/env python3
"""Tactic-cost-model proposer.

Pure host-side Python — no docker exec, no Isabelle, no session lock.
Reads `.thy` text, identifies the search-strength tactic at the given line,
joins static priors from `references/tactic-cost-priors.jsonl` with
empirical aggregates from `logs/impact-*.jsonl` + `logs/attempts-*.jsonl`,
and prints a ranked list of candidate substitutions with patch text in the
format `check-theory.sh --patch` consumes.

Usage:
    propose_tactic.py <file.thy> <line> [session]

Env:
    STRENGTHEN_DISABLE_TACTIC_COST_MODEL=1  → exit 0 with a disabled note
                                              (used by A/B control arm)
"""
from __future__ import annotations  # host runs 3.8; defer annotation evaluation
import json
import os
import re
import sys
import time
from glob import glob
from pathlib import Path

# ───────────────────────────── constants ─────────────────────────────────

ARROW = r"(?:->|→|=>|\bto\b)"

EMPIRICAL_PATTERNS = [
    (r"\bforce\s*" + ARROW + r"\s*fastforce\b",       "force-to-fastforce"),
    (r"\bfastforce\s*" + ARROW + r"\s*force\b",       "fastforce-to-force"),
    (r"\bauto\s*" + ARROW + r"\s*fastforce\b",        "auto-to-fastforce"),
    (r"\bauto\s*" + ARROW + r"\s*force\b",            "auto-to-force"),
    (r"\bblast\s*" + ARROW + r"\s*force\b",           "blast-to-force"),
    (r"\belim!\s*:?\s*" + ARROW + r"\s*elim\s*:?",    "elim-bang-to-elim"),
    (r"\bintro!\s*:?\s*" + ARROW + r"\s*intro\s*:?", "intro-bang-to-intro"),
    (r"\bdest!\s*:?\s*" + ARROW + r"\s*dest\s*:?",   "dest-bang-to-dest"),
    (r"\bsimp_depth_limit\s*[=:]",                    "add-simp-depth-5"),
]

# Tactics the proposer will not engage with (different optimisation domain).
STRUCTURAL_TACTICS = {
    "rule", "subst", "induct", "cases", "erule", "drule", "frule",
    "drule_tac", "rename_tac", "schematic_goal",
}
EISBACH_PREFIXES = ("wp", "wpsimp", "hoare_vcg")

# Search-tactic vocabulary for anchor identification.
ANCHOR_RE = re.compile(
    r"\b(simp|clarsimp|auto|fastforce|force|blast|fast|metis|smt|safe|clarify)\b"
)

# Skip these too — they are structural/Eisbach we explicitly out-of-scope.
SKIP_RE = re.compile(
    r"\b(?:rule|subst|induct|cases|erule|drule|frule|wp|wpsimp|hoare_vcg_\w*)\b"
)

# Read context window: up to N physical lines forward to merge a multi-line tactic.
MERGE_LOOKAHEAD = 8
MERGE_CAP = 6   # max merged physical lines

MAX_CANDIDATES = 5


# ───────────────────────────── repo / paths ──────────────────────────────


def find_repo_root() -> Path:
    """Walk up from this script until we find the seL4-docker-main repo root.

    Identified by the combination of a Dockerfile and a verification/ directory
    (the project's source tree). Falls back to docker-compose.yml if present.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if ((parent / "Dockerfile").exists() and (parent / "verification").is_dir()) \
                or (parent / "docker-compose.yml").exists():
            return parent
    sys.stderr.write(
        "ERROR: cannot locate repo root (no Dockerfile+verification/ or "
        f"docker-compose.yml in any parent of {here}). Aborting.\n"
    )
    sys.exit(2)


def normalize_target_path(target: str, repo_root: Path) -> Path:
    """Accept either /workspace/verification/... (container path) or
    verification/... (repo-rel) or absolute host path. Return absolute host Path."""
    if target.startswith("/workspace/"):
        # Container path → host path
        return repo_root / target[len("/workspace/"):]
    p = Path(target)
    if not p.is_absolute():
        return repo_root / p
    return p


def detect_session(target: Path) -> str:
    """Best-effort session detection from path. Caller may override."""
    s = str(target)
    if "/proof/access-control" in s:
        return "Access"
    if "/proof/invariant-abstract" in s:
        return "AInvs"
    if "/proof/infoflow" in s:
        return "InfoFlow"
    if "/proof/refine" in s:
        return "Refine"
    if "/proof/crefine" in s:
        return "CRefine"
    return "AInvs"  # safe-ish default


def slow_proofs_report_path(repo_root: Path, target: Path) -> Path | None:
    """Map a .thy under proof/<dir>/... to reports/slow-proofs-<dir>.md."""
    s = str(target)
    for dir_slug, fname in [
        ("/proof/access-control", "slow-proofs-access-control.md"),
        ("/proof/invariant-abstract", "slow-proofs-invariant-abstract.md"),
        ("/proof/infoflow", "slow-proofs-infoflow.md"),
        ("/proof/refine", "slow-proofs-refine.md"),
        ("/proof/crefine", "slow-proofs-crefine.md"),
    ]:
        if dir_slug in s:
            p = repo_root / "reports" / fname
            return p if p.exists() else None
    return None


# ───────────────────────────── parsing ───────────────────────────────────


def read_thy_lines(path: Path) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        sys.stderr.write(f"ERROR: file not found: {path}\n")
        sys.exit(2)


def merge_tactic_block(lines: list[str], anchor_idx: int) -> tuple[str, int, int]:
    """Starting at anchor_idx (0-based), greedily merge up to MERGE_LOOKAHEAD
    forward physical lines while paren depth (`(`/`)` or `[`/`]`) stays > 0.
    Returns (merged_text, start_idx, end_idx) — both 0-based indices into lines.
    """
    if anchor_idx >= len(lines):
        return ("", anchor_idx, anchor_idx)

    merged: list[str] = []
    depth_paren = 0
    depth_bracket = 0
    in_string = False
    end = anchor_idx
    for i in range(anchor_idx, min(anchor_idx + MERGE_LOOKAHEAD, len(lines))):
        if i - anchor_idx >= MERGE_CAP:
            break
        line = lines[i]
        merged.append(line)
        end = i
        # Walk chars to update depth
        for ch in line:
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "(":
                depth_paren += 1
            elif ch == ")":
                depth_paren -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1
        if depth_paren <= 0 and depth_bracket <= 0:
            break
    return ("\n".join(merged), anchor_idx, end)


def identify_anchor(merged_text: str) -> tuple[str | None, str]:
    """Return (anchor_tactic, reason). reason ∈ {'ok', 'structural', 'eisbach', 'none'}."""
    if SKIP_RE.search(merged_text):
        m = SKIP_RE.search(merged_text)
        token = m.group(0) if m else "?"
        if token.startswith(("wp", "hoare_vcg")):
            return (None, f"eisbach:{token}")
        return (None, f"structural:{token}")
    m = ANCHOR_RE.search(merged_text)
    if not m:
        return (None, "none")
    return (m.group(1), "ok")


# ───────────────────────────── data loaders ──────────────────────────────


def load_priors(repo_root: Path) -> list[dict]:
    # Resolve references file relative to this script so the lookup works
    # regardless of where the .claude/ tree is anchored within the repo.
    # Layout: scripts/propose_tactic.py → ../references/tactic-cost-priors.jsonl
    p = Path(__file__).resolve().parent.parent / "references" / "tactic-cost-priors.jsonl"
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                sys.stderr.write(f"WARN: priors line skipped ({e}): {line[:80]}\n")
    return out


def load_empirical(repo_root: Path, session: str) -> tuple[dict, int]:
    """Walk impact + attempts logs and aggregate per pattern_key.
    Returns (per_pattern_dict, unparsed_count)."""
    patterns = [(re.compile(rx, re.IGNORECASE), key) for rx, key in EMPIRICAL_PATTERNS]
    count_pre = re.compile(r"\b(\d+)\s+(?=" + "|".join(rx for rx, _ in EMPIRICAL_PATTERNS) + ")",
                           re.IGNORECASE)

    agg: dict[str, dict] = {}
    unparsed = 0

    log_dir = repo_root / "logs"
    impact_files = sorted(glob(str(log_dir / f"impact-*{session.lower()}*.jsonl"))
                         + glob(str(log_dir / f"impact-{_session_path_slug(session)}*.jsonl")))
    # Also accept generic "impact-<anything>" but filter by record's session field
    impact_files = sorted(set(impact_files))
    if not impact_files:
        impact_files = sorted(glob(str(log_dir / "impact-*.jsonl")))

    attempts_files = sorted(glob(str(log_dir / "attempts-*.jsonl")))

    # Also walk archive subdirs
    archive_dir = log_dir / "archive"
    if archive_dir.is_dir():
        impact_files += sorted(glob(str(archive_dir / "**" / "impact-*.jsonl"),
                                    recursive=True))
        attempts_files += sorted(glob(str(archive_dir / "**" / "attempts-*.jsonl"),
                                       recursive=True))

    for fp in impact_files:
        try:
            with open(fp) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("session") and rec["session"].lower() != session.lower():
                        continue
                    notes = rec.get("notes", "")
                    delta = rec.get("delta_pct")
                    n_mult = 1
                    cm = count_pre.search(notes)
                    if cm:
                        try:
                            n_mult = int(cm.group(1))
                        except Exception:
                            n_mult = 1
                    matched = False
                    for rx, key in patterns:
                        if rx.search(notes):
                            d = agg.setdefault(key, {"n_apply": 0, "deltas": [], "n_fail": 0})
                            d["n_apply"] += n_mult
                            if delta is not None:
                                d["deltas"].extend([float(delta)] * n_mult)
                            matched = True
                    if not matched and notes:
                        unparsed += 1
        except FileNotFoundError:
            continue

    for fp in attempts_files:
        try:
            with open(fp) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("session") and rec["session"].lower() != session.lower():
                        continue
                    if rec.get("verdict") not in ("fail", "timeout"):
                        continue
                    notes = rec.get("notes", "")
                    for rx, key in patterns:
                        if rx.search(notes):
                            d = agg.setdefault(key, {"n_apply": 0, "deltas": [], "n_fail": 0})
                            d["n_fail"] += 1
        except FileNotFoundError:
            continue

    # finalize
    out = {}
    for key, d in agg.items():
        deltas = d["deltas"]
        out[key] = {
            "n_apply": d["n_apply"],
            "n_fail": d["n_fail"],
            "mean_delta_pct": (sum(deltas) / len(deltas)) if deltas else None,
        }
    return out, unparsed


def _session_path_slug(session: str) -> str:
    """Map session name to the path slug used in log filenames.
    Logs use the directory slug, e.g. 'access-control' for Access."""
    return {
        "Access": "access-control",
        "AInvs": "invariant-abstract",
        "InfoFlow": "infoflow",
        "Refine": "refine",
        "CRefine": "crefine",
    }.get(session, session.lower())


# ───────────────────────────── proof context ─────────────────────────────


def lookup_proof_at_line(repo_root: Path, target: Path, line_no: int) -> str | None:
    """Scan upward from `line_no` to find the most recent `lemma <name>:` /
    `theorem <name>:` / `corollary <name>:` declaration. Returns the name or None."""
    try:
        lines = read_thy_lines(target)
    except Exception:
        return None
    decl_re = re.compile(r"^\s*(?:lemma|theorem|corollary)\s+([A-Za-z_][\w']*)")
    # 1-based to 0-based: subtract 1, then scan upward
    for i in range(min(line_no - 1, len(lines) - 1), -1, -1):
        m = decl_re.match(lines[i])
        if m:
            return m.group(1)
    return None


def parse_slow_proofs_for_file(slow_md: Path, file_basename: str) -> tuple[dict, int | None]:
    """Return (per_proof_cost_dict, file_baseline_ms).
    per_proof_cost_dict: { proof_name -> cost_ms } from the file's section.
    """
    per_proof = {}
    baseline = None
    if not slow_md or not slow_md.exists():
        return per_proof, None
    section_header = "## " + Path(file_basename).stem  # e.g. "## Ipc_AC"
    try:
        text = slow_md.read_text()
    except Exception:
        return per_proof, None
    # Find this file's section
    idx = text.find(section_header + "\n")
    if idx < 0:
        return per_proof, None
    # Section ends at next "## " heading
    next_idx = text.find("\n## ", idx + 1)
    section = text[idx : next_idx if next_idx > 0 else len(text)]

    bm = re.search(r"\*\*Baseline\*\*:\s*(\d+)ms", section)
    if bm:
        baseline = int(bm.group(1))

    # Per-proof cost rows (in the Sorry-substitution table):
    #   "  proof_name<spaces>NN L<spaces>sorry= NNNNNms  cost=  NNNNms  [...]"
    proof_re = re.compile(
        r"^\s*([A-Za-z_][\w'\[\]]*)\s+\d+L\s+sorry=\s*\d+ms\s+cost=\s*(-?\d+)ms",
        re.MULTILINE,
    )
    for m in proof_re.finditer(section):
        per_proof[m.group(1)] = int(m.group(2))
    return per_proof, baseline


# ───────────────────────────── candidate scoring ─────────────────────────


def applicability_score(prior: dict, proof_cost_ms: int | None,
                         file_baseline_ms: int | None, merged_text: str) -> tuple[float, list[str]]:
    """Return (applicability_in_[0,1], anti_flags_triggered)."""
    aw = prior.get("applies_when") or {}
    score = 1.0
    flags = []

    if proof_cost_ms is not None and aw.get("min_proof_cost_ms"):
        if proof_cost_ms < aw["min_proof_cost_ms"]:
            score *= 0.4
            flags.append(f"proof_cost_ms={proof_cost_ms} < required {aw['min_proof_cost_ms']}")
    elif proof_cost_ms is None and aw.get("min_proof_cost_ms"):
        # Unknown cost — give partial credit, tag uncertainty
        score *= 0.7

    if file_baseline_ms is not None and aw.get("min_file_baseline_ms"):
        if file_baseline_ms < aw["min_file_baseline_ms"]:
            score *= 0.5
            flags.append(f"file_baseline_ms={file_baseline_ms} < required {aw['min_file_baseline_ms']}")

    if aw.get("requires_modifier_args"):
        # Need at least one of simp:/dest:/intro:/elim: in the merged tactic
        if not re.search(r"\b(?:simp|dest|intro|elim|split)!?\s*:", merged_text):
            score *= 0.6
            flags.append("bare tactic with no hint args")

    # Hard small-proof penalty (anti-pattern from priors)
    if proof_cost_ms is not None and proof_cost_ms < 5000:
        flags.append("proof_cost_ms < 5000")

    return score, flags


def compute_score(prior: dict, empirical: dict, applicability: float,
                  n_anti_flags: int) -> tuple[float, dict]:
    """Combine static + empirical + applicability per the plan's weighting."""
    expected = prior.get("expected_delta_pct", 0.0)
    n_static = prior.get("n_prior_static", 0)
    emp_n = empirical.get("n_apply", 0) if empirical else 0
    emp_mean = empirical.get("mean_delta_pct") if empirical else None

    # Switch weights once empirical signal is mature.
    if emp_n >= 5:
        w_stat, w_emp, w_appl = 0.2, 0.7, 0.1
    else:
        w_stat, w_emp, w_appl = 0.4, 0.5, 0.1

    # We want to maximize negative delta → invert sign so larger score = better.
    static_term = -expected if expected else 0.0
    emp_term = (-emp_mean) if (emp_mean is not None) else static_term  # fallback to static
    appl_term = applicability * 10.0  # scale to 0-10 range

    raw = w_stat * static_term + w_emp * emp_term + w_appl * appl_term
    raw -= 0.1 * n_anti_flags

    return raw, {
        "weights": {"static": w_stat, "empirical": w_emp, "applicability": w_appl},
        "static_term": static_term,
        "empirical_term": emp_term,
        "applicability_term": appl_term,
        "anti_penalty": 0.1 * n_anti_flags,
    }


# ───────────────────────────── patch construction ────────────────────────


def build_simple_substitution_patch(prior: dict, merged_text: str,
                                     start_line_1based: int,
                                     end_line_1based: int) -> tuple[str, str]:
    """Apply prior's regex substitution to merged_text. Returns (patch_block, replacement_text).
    Falls back to empty replacement if pattern doesn't actually match (shouldn't happen
    if the candidate was selected based on a match upstream)."""
    rx = re.compile(prior["from_pattern"])
    template = prior["to_template"]

    if rx.search(merged_text):
        replacement = rx.sub(template, merged_text)
    else:
        replacement = merged_text  # caller should have filtered already

    patch_block = f"{start_line_1based} {end_line_1based}\n{replacement}\n"
    return patch_block, replacement


# ───────────────────────────── output ────────────────────────────────────


def render_human(file: Path, line: int, session: str, anchor: str | None,
                  reason: str, merged_text: str, start_1b: int, end_1b: int,
                  proof_name: str | None, proof_cost_ms: int | None,
                  proof_rank: int | None, file_baseline_ms: int | None,
                  parse_confidence: str, candidates: list[dict],
                  unparsed_notes_count: int) -> str:
    out = []
    out.append(f"file: {file}")
    out.append(f"line: {line}")
    out.append(f"session: {session}")
    if reason != "ok":
        out.append(f"out-of-scope: {reason}")
        return "\n".join(out) + "\n"
    out.append(f"detected_tactic: {anchor}")
    if start_1b == end_1b:
        out.append(f"detected_block_lines: {start_1b}-{end_1b}")
    else:
        out.append(f"detected_block_lines: {start_1b}-{end_1b}  (multi-line, {end_1b - start_1b + 1} physical lines)")
    if proof_name:
        rank_s = f"  (rank #{proof_rank} in file)" if proof_rank else ""
        out.append(f"proof_name: {proof_name}{rank_s}")
    if proof_cost_ms is not None:
        out.append(f"proof_sorry_cost_ms: {proof_cost_ms}")
    if file_baseline_ms is not None:
        out.append(f"file_baseline_ms: {file_baseline_ms}")
    out.append(f"parse_confidence: {parse_confidence}")
    out.append("")
    if not candidates:
        out.append("candidates: (none — no priors matched the detected tactic)")
        return "\n".join(out) + "\n"

    out.append(f"candidates (top {len(candidates)}):")
    out.append("")
    for i, c in enumerate(candidates, start=1):
        out.append(f"{i}. {c['id']}  [score {c['score']:.2f}]")
        prior_n = c.get("prior_n", 0)
        prior_mean = c.get("prior_mean")
        prior_str = f"n={prior_n}  mean={prior_mean:+.1f}%" if prior_mean is not None else f"n={prior_n}  mean=?"
        out.append(f"   prior  {prior_str}")
        emp_n = c.get("empirical_n", 0)
        emp_mean = c.get("empirical_mean")
        emp_str = f"n={emp_n}  mean={emp_mean:+.1f}%" if emp_mean is not None else f"n={emp_n}"
        emp_fail = c.get("empirical_fail", 0)
        if emp_fail:
            emp_str += f"  (fails={emp_fail})"
        out.append(f"   empir  {emp_str}")
        flags = c.get("anti_flags", [])
        if flags:
            out.append(f"   anti   {'; '.join(flags)}")
        else:
            out.append(f"   anti   none")
        out.append(f"   patch:")
        for pline in c["patch"].splitlines():
            out.append(f"     {pline}")
        out.append("")
    if unparsed_notes_count:
        out.append(f"unparsed_notes_count: {unparsed_notes_count}  (notes that didn't match any empirical regex; refine SUB_PATTERNS in propose_tactic.py)")
    return "\n".join(out) + "\n"


def write_jsonl(repo_root: Path, session: str, payload: dict) -> Path | None:
    """Append one record to logs/tactic-proposer-<session>-<rid>.jsonl.
    rid = .current-run-id sentinel content if present, else 'standalone'."""
    sentinel = repo_root / "logs" / ".current-run-id"
    rid = "standalone"
    if sentinel.exists():
        try:
            rid = sentinel.read_text().strip() or "standalone"
        except Exception:
            pass
    out = repo_root / "logs" / f"tactic-proposer-{session}-{rid}.jsonl"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a") as f:
            f.write(json.dumps(payload) + "\n")
        return out
    except Exception as e:
        sys.stderr.write(f"WARN: could not write proposer jsonl: {e}\n")
        return None


# ───────────────────────────── main ──────────────────────────────────────


def main() -> int:
    if os.environ.get("STRENGTHEN_DISABLE_TACTIC_COST_MODEL") == "1":
        print("cost-model disabled (env: STRENGTHEN_DISABLE_TACTIC_COST_MODEL=1)")
        return 0

    if len(sys.argv) < 3:
        sys.stderr.write("Usage: propose_tactic.py <file.thy> <line> [session]\n")
        return 2

    target_arg = sys.argv[1]
    try:
        line_1b = int(sys.argv[2])
    except ValueError:
        sys.stderr.write(f"ERROR: line must be integer, got {sys.argv[2]!r}\n")
        return 2

    repo_root = find_repo_root()
    target = normalize_target_path(target_arg, repo_root)
    session = sys.argv[3] if len(sys.argv) >= 4 else detect_session(target)

    if not target.exists():
        sys.stderr.write(f"ERROR: target file does not exist: {target}\n")
        return 2

    lines = read_thy_lines(target)
    if line_1b < 1 or line_1b > len(lines):
        sys.stderr.write(f"ERROR: line {line_1b} out of range (file has {len(lines)} lines)\n")
        return 2

    # Phase: merge tactic block
    merged_text, start_idx, end_idx = merge_tactic_block(lines, line_1b - 1)
    start_1b, end_1b = start_idx + 1, end_idx + 1
    anchor, reason = identify_anchor(merged_text)

    # Default low-confidence baseline; bumped to high if all signals clean.
    parse_confidence = "med"

    # Out-of-scope short-circuit
    if reason != "ok":
        text = render_human(target, line_1b, session, None, reason, merged_text,
                             start_1b, end_1b, None, None, None, None,
                             "n/a", [], 0)
        sys.stdout.write(text)
        write_jsonl(repo_root, session, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file": str(target.relative_to(repo_root) if str(target).startswith(str(repo_root)) else target),
            "line": line_1b, "session": session,
            "out_of_scope": reason, "candidates": [],
        })
        return 0

    # Phase: proof + file context
    proof_name = lookup_proof_at_line(repo_root, target, line_1b)
    slow_md = slow_proofs_report_path(repo_root, target)
    per_proof, file_baseline_ms = parse_slow_proofs_for_file(slow_md, target.name)
    proof_cost_ms = per_proof.get(proof_name) if proof_name else None
    proof_rank = None
    if proof_cost_ms is not None and per_proof:
        ranked = sorted(per_proof.items(), key=lambda kv: kv[1], reverse=True)
        for i, (n, _) in enumerate(ranked, start=1):
            if n == proof_name:
                proof_rank = i
                break

    # Phase: priors + empirical
    priors = load_priors(repo_root)
    empirical, unparsed = load_empirical(repo_root, session)

    # Phase: candidate construction
    candidates = []
    for prior in priors:
        try:
            rx = re.compile(prior["from_pattern"])
        except re.error as e:
            sys.stderr.write(f"WARN: bad regex in prior {prior.get('id')}: {e}\n")
            continue
        if not rx.search(merged_text):
            continue
        # `to_template` may reference a complex transformation (depth-limit wrapper, split).
        # For v1 we only build patches when to_template is a simple word-substitution token.
        complex_template = (
            "wrapper" in (prior.get("to_template") or "") or
            ";" in (prior.get("to_template") or "") or
            "(fastforce simp_depth_limit:" in (prior.get("to_template") or "")
        )
        appl_score, anti_flags = applicability_score(prior, proof_cost_ms,
                                                      file_baseline_ms, merged_text)
        # Add anti-pattern flag for batch context — heuristic: if the merged tactic
        # spans 1 line AND proof_cost is high, single-proof patch is fine.
        # We can't detect "user will batch this" — so we just surface the warning.

        emp = empirical.get(prior["id"], {})
        if emp.get("n_fail", 0) > 0:
            anti_flags.append(f"empirical_fails={emp['n_fail']}")
        if prior.get("n_prior_static", 0) == 0 and not emp.get("n_apply"):
            anti_flags.append("unmeasured — speculative")
        if complex_template:
            anti_flags.append("complex template — patch needs manual review")

        score, _breakdown = compute_score(prior, emp, appl_score, len(anti_flags))

        if complex_template:
            # Do not auto-build patch; suggest the substitution textually.
            patch_text = (
                f"{start_1b} {end_1b}\n"
                f"# complex template: {prior['to_template']}\n"
                f"# original block:\n# " + merged_text.replace("\n", "\n# ") + "\n"
                f"# (proposer cannot auto-construct — apply manually)\n"
            )
        else:
            patch_text, _ = build_simple_substitution_patch(prior, merged_text, start_1b, end_1b)

        candidates.append({
            "id": prior["id"],
            "score": score,
            "rank": 0,  # set after sort
            "prior_n": prior.get("n_prior_static", 0),
            "prior_mean": prior.get("expected_delta_pct"),
            "empirical_n": emp.get("n_apply", 0),
            "empirical_mean": emp.get("mean_delta_pct"),
            "empirical_fail": emp.get("n_fail", 0),
            "anti_flags": anti_flags,
            "patch": patch_text,
            "complex_template": complex_template,
            "applicability_score": round(appl_score, 3),
        })

    # Rank
    candidates.sort(key=lambda c: c["score"], reverse=True)
    candidates = candidates[:MAX_CANDIDATES]
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i

    # Confidence: high if proof was found in slow-proofs AND no parse anomalies AND
    # at least one candidate is non-speculative; low if proof missing AND no empirical;
    # else med.
    if proof_name and per_proof and any(c["empirical_n"] > 0 or c["prior_n"] > 0 for c in candidates):
        parse_confidence = "high"
    elif not per_proof and not empirical:
        parse_confidence = "low"

    # Output
    text = render_human(target, line_1b, session, anchor, reason, merged_text,
                         start_1b, end_1b, proof_name, proof_cost_ms, proof_rank,
                         file_baseline_ms, parse_confidence, candidates, unparsed)
    sys.stdout.write(text)

    write_jsonl(repo_root, session, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file": str(target.relative_to(repo_root) if str(target).startswith(str(repo_root)) else target),
        "line": line_1b, "session": session,
        "detected_tactic": anchor, "detected_block_lines": [start_1b, end_1b],
        "proof_name": proof_name, "proof_cost_ms": proof_cost_ms, "proof_rank": proof_rank,
        "file_baseline_ms": file_baseline_ms,
        "parse_confidence": parse_confidence,
        "unparsed_notes_count": unparsed,
        "candidates": candidates,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
