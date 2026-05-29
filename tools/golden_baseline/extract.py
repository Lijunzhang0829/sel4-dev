#!/usr/bin/env python3
"""
tools/golden_baseline/extract.py
================================

Build the four golden-baseline artifacts from a completed build:

    reports/golden-baseline/walls.json        per-session wall+cpu+factor
    reports/golden-baseline/env.json          build environment
    reports/golden-baseline/heap-sizes.txt    per-heap size + sha256
    reports/golden-baseline/lemma-inventory.db   SQLite snapshot of lemmas

Designed to be re-run incrementally:
  - safe to call mid-build (walls.json gets the events available so far)
  - safe to call without all logs present
  - heap fingerprints reflect whatever's in the heap dir right now

Inputs (read-only):
  reports/golden-baseline/build-logs/round*.log
  container:/root/.isabelle/etc/settings
  container:/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/
  verification/l4v (for source commit hash)
  /proc/cpuinfo, /proc/meminfo, uname (for host info)
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
OUT_DIR = REPO / "reports" / "golden-baseline"
LOG_DIR = OUT_DIR / "build-logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
CONTAINER = "sel4-l4v"
HEAP_DIR_CONTAINER = "/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux"

FINISH_RE = re.compile(
    r"^Finished\s+([A-Za-z_][\w-]*)\s+"
    r"\((\d+):(\d+):(\d+)\s+elapsed\s+time,\s+"
    r"(\d+):(\d+):(\d+)\s+cpu\s+time,\s+factor\s+([\d.]+)\)\s*$"
)
ROUND_TOTAL_RE = re.compile(
    r"^(\d+):(\d+):(\d+)\s+elapsed\s+time,\s+"
    r"(\d+):(\d+):(\d+)\s+cpu\s+time,\s+factor\s+([\d.]+)\s*$"
)


def dx(*cmd: str) -> str:
    """Run `docker exec sel4-l4v <cmd>` and return stdout."""
    return subprocess.check_output(
        ["docker", "exec", CONTAINER, *cmd], text=True,
    )


def parse_round_log(log_path: Path) -> dict:
    """Extract Finished events and final summary from one round's log."""
    if not log_path.exists():
        return {"round": log_path.stem, "missing": True, "events": [], "total": None}
    content = log_path.read_text()
    events = []
    for line in content.splitlines():
        m = FINISH_RE.match(line)
        if m:
            wall = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
            cpu = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7))
            events.append({
                "session": m.group(1),
                "wall_s": wall,
                "cpu_s": cpu,
                "factor": float(m.group(8)),
            })
    total = None
    for line in content.splitlines()[::-1]:
        m = ROUND_TOTAL_RE.match(line)
        if m:
            total = {
                "wall_s": int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)),
                "cpu_s": int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6)),
                "factor": float(m.group(7)),
            }
            break
    return {
        "round": log_path.stem,
        "n_finished": len(events),
        "total": total,
        "events": events,
    }


def build_walls() -> dict:
    rounds = []
    per_session: dict[str, dict] = {}
    for log in sorted(LOG_DIR.glob("round*.log")):
        r = parse_round_log(log)
        rounds.append(r)
        for ev in r["events"]:
            ev2 = dict(ev)
            ev2["round"] = r["round"]
            per_session[ev2["session"]] = ev2
    totals_when_complete = None
    if rounds:
        total_wall = sum(s["wall_s"] for s in per_session.values())
        total_cpu = sum(s["cpu_s"] for s in per_session.values())
        totals_when_complete = {
            "wall_s_sum_per_session": total_wall,
            "cpu_s_sum_per_session": total_cpu,
            "session_count": len(per_session),
        }
    return {
        "schema_version": 2,
        "build_run_id": "golden-20260529-30gb-j2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "j": 2,
            "skip_duplicated_proofs": True,
            "ml_options": "--maxheap 12000 --stackspace 64",
            "isabelle_build_options": "document=false",
            "l4v_arch": "ARM",
            "threads_per_session": "auto-detect (Isabelle default)",
        },
        "rounds": rounds,
        "per_session": dict(sorted(per_session.items())),
        "totals_when_complete": totals_when_complete,
    }


def build_env() -> dict:
    try:
        settings = dx("cat", "/root/.isabelle/etc/settings")
    except subprocess.CalledProcessError:
        settings = ""
    # Parse ML_OPTIONS from settings. The file has TWO ML_OPTIONS lines
    # branched on polyml version; we want the polyml-5.9 (else-branch) value,
    # which by convention appears LAST. Take the last non-comment hit.
    ml_options = None
    for line in settings.splitlines():
        if "ML_OPTIONS=" in line and not line.lstrip().startswith("#"):
            ml_options = line.split("=", 1)[1].split("  #", 1)[0].strip().strip('"')
    # (no break — keep going to the last match)
    skip_dups = "1" if "export SKIP_DUPLICATED_PROOFS=1" in settings else "0"
    build_opts = None
    for line in settings.splitlines():
        if line.strip().startswith("export ISABELLE_BUILD_OPTIONS"):
            build_opts = line.split("=", 1)[1].strip().strip('"')

    # Host info
    host_kernel = subprocess.check_output(["uname", "-r"], text=True).strip()
    cpuinfo = Path("/proc/cpuinfo").read_text()
    n_cores = cpuinfo.count("processor\t:")
    cpu_model = next(
        (line.split(":", 1)[1].strip()
         for line in cpuinfo.splitlines() if line.startswith("model name")),
        "unknown",
    )
    meminfo = Path("/proc/meminfo").read_text()
    mem_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
    swap_kb = int(re.search(r"SwapTotal:\s+(\d+)", meminfo).group(1))

    l4v_commit = subprocess.check_output(
        ["git", "-C", str(REPO / "verification" / "l4v"), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    l4v_dirty = subprocess.check_output(
        ["git", "-C", str(REPO / "verification" / "l4v"), "status", "--short"],
        text=True,
    ).strip()

    return {
        "schema_version": 2,
        "build_run_id": "golden-20260529-30gb-j2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "l4v_commit": l4v_commit,
            "l4v_clean_at_build_start": (l4v_dirty == ""),
            "l4v_uncommitted_files": l4v_dirty or None,
        },
        "isabelle": {
            "settings_path": "/root/.isabelle/etc/settings",
            "settings_content": settings,
            "ml_options": ml_options,
            "isabelle_build_options": build_opts,
        },
        "build_flags": {
            "skip_duplicated_proofs": skip_dups,
            "l4v_arch": "ARM",
            "j": 2,
            "threads_per_session": "auto",
        },
        "host": {
            "kernel": host_kernel,
            "cpu_model": cpu_model,
            "n_cores": n_cores,
            "ram_gb": mem_kb // 1024 // 1024,
            "swap_gb": swap_kb // 1024 // 1024,
            "container": CONTAINER,
        },
    }


def build_heap_sizes() -> str:
    """Capture sha256 + size of each heap file."""
    try:
        listing = dx("ls", "-1", HEAP_DIR_CONTAINER).strip().splitlines()
    except subprocess.CalledProcessError:
        return "# (heap dir not accessible)\n"
    rows = []
    for name in sorted(listing):
        if name == "log":
            continue
        full = f"{HEAP_DIR_CONTAINER}/{name}"
        size_str = dx("stat", "-c", "%s", full).strip()
        sha = dx("sha256sum", full).split()[0]
        rows.append((name, int(size_str), sha))
    if not rows:
        return f"# golden-baseline heap fingerprints — captured {datetime.now(timezone.utc).isoformat()}\n# (heap dir empty)\n"
    lines = [
        "# golden-baseline heap fingerprints",
        f"# captured {datetime.now(timezone.utc).isoformat()}",
        "# format: <size_bytes>  <sha256>  <name>",
        "",
    ]
    total = 0
    for name, size, sha in rows:
        lines.append(f"{size:>12d}  {sha}  {name}")
        total += size
    lines.append("")
    lines.append(f"# total {total} bytes across {len(rows)} heaps")
    return "\n".join(lines) + "\n"


def run_lemma_inventory() -> Path:
    db_out = OUT_DIR / "lemma-inventory.db"
    summary_out = OUT_DIR / "lemma-inventory-summary.md"
    cmd = [
        sys.executable,
        str(REPO / "tools" / "lemma_inventory" / "build.py"),
        "--l4v", str(REPO / "verification" / "l4v"),
        "--out", str(db_out),
        "--summary", str(summary_out),
    ]
    print(f"running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    return db_out


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--no-lemma-inv", action="store_true",
                   help="Skip lemma-inventory.db generation (slow, ~30s)")
    p.add_argument("--no-heap-sizes", action="store_true",
                   help="Skip heap fingerprinting (when heaps incomplete)")
    args = p.parse_args()

    print(f"[1/4] walls.json")
    walls = build_walls()
    (OUT_DIR / "walls.json").write_text(json.dumps(walls, indent=2))
    n_sess = len(walls["per_session"])
    print(f"     {n_sess} sessions across {len(walls['rounds'])} round logs")

    print(f"[2/4] env.json")
    env = build_env()
    (OUT_DIR / "env.json").write_text(json.dumps(env, indent=2))
    print(f"     l4v={env['source']['l4v_commit'][:8]} "
          f"ml_options={env['isabelle']['ml_options']!r}")

    if not args.no_heap_sizes:
        print(f"[3/4] heap-sizes.txt")
        text = build_heap_sizes()
        (OUT_DIR / "heap-sizes.txt").write_text(text)
        n_heaps = len([l for l in text.splitlines()
                       if l and not l.startswith("#")])
        print(f"     {n_heaps} heaps fingerprinted")
    else:
        print("[3/4] heap-sizes.txt skipped")

    if not args.no_lemma_inv:
        print(f"[4/4] lemma-inventory.db")
        run_lemma_inventory()
    else:
        print("[4/4] lemma-inventory.db skipped")

    print()
    for f in sorted(OUT_DIR.glob("*")):
        if f.is_file():
            print(f"  {f.relative_to(REPO)}  ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
