#!/usr/bin/env python3
"""Supervise the two-hour VERO seal-idempotency acceptance run.

The supervisor never mutates the test workspace.  It pauses the test at the
wall-clock deadline, audits the append-only ledger, and starts the fresh formal
run only when every acceptance predicate holds.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import time


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def property_value(service: str, name: str) -> str:
    result = run(
        ["systemctl", "--user", "show", service, "--property", name, "--value"],
        check=False,
    )
    return result.stdout.strip()


def ledger_rows(output: Path) -> list[dict]:
    rows = []
    for path in sorted((output / "events").glob("*.json")):
        rows.append(json.loads(path.read_text()))
    return rows


def inspect_test_containers(test_root: Path) -> list[str]:
    listed = run(["docker", "ps", "-q"], check=False)
    ids = [item for item in listed.stdout.splitlines() if item]
    leaked = []
    needle = str(test_root)
    for container_id in ids:
        inspected = run(["docker", "inspect", container_id], check=False)
        if needle in inspected.stdout:
            leaked.append(container_id)
    return leaked


def audit(args: argparse.Namespace, stopped_at: float) -> dict:
    rows = ledger_rows(args.test_output)
    kinds = Counter(row.get("kind") for row in rows)
    failures: list[str] = []
    if stopped_at + 1 < args.deadline_unix:
        failures.append("test stopped before the two-hour wall deadline")
    if kinds["plan_accepted"] != 1:
        failures.append(f"expected one accepted plan, observed {kinds['plan_accepted']}")
    if kinds["response"] < 1:
        failures.append("no provider response was durably recorded")

    attempts: dict[tuple[str, str], list[dict]] = defaultdict(list)
    sealed: dict[tuple[str, str], list[dict]] = defaultdict(list)
    reused: list[dict] = []
    sealed_event_ids: set[str] = set()
    sealed_theories: set[str] = set()
    for row in rows:
        data = row.get("data") or {}
        key = (data.get("theory"), data.get("source_identity"))
        if row.get("kind") == "seal_attempt":
            attempts[key].append(row)
        elif row.get("kind") == "seal_result" and data.get("status") == "sealed":
            sealed[key].append(row)
            sealed_event_ids.add(row.get("event_id"))
            if data.get("theory") != "Vero_All":
                sealed_theories.add(data.get("theory"))
            evidence = data.get("evidence") or {}
            if data.get("source_identity") is None or len(data.get("source_identity", "")) != 64:
                failures.append(f"invalid source identity on sealed result {row.get('event_id')}")
            if evidence.get("returncode") != 0:
                failures.append(f"sealed result lacks successful build evidence {row.get('event_id')}")
        elif row.get("kind") == "seal_reused":
            reused.append(row)

    duplicate_attempts = {
        f"{key[0]}:{key[1]}": len(value) for key, value in attempts.items() if len(value) > 1
    }
    duplicate_results = {
        f"{key[0]}:{key[1]}": len(value) for key, value in sealed.items() if len(value) > 1
    }
    if duplicate_attempts:
        failures.append(f"duplicate content-bound seal attempts: {duplicate_attempts}")
    if duplicate_results:
        failures.append(f"duplicate successful content-bound seal results: {duplicate_results}")
    if not sealed:
        failures.append("no successful content-bound seal occurred")
    if not reused:
        failures.append("no exact-identity seal reuse occurred; live idempotency was not exercised")
    for row in reused:
        data = row.get("data") or {}
        key = (data.get("theory"), data.get("source_identity"))
        if key not in sealed:
            failures.append(f"seal reuse has no matching successful receipt: {key}")
        elif min(item.get("sequence", 0) for item in sealed[key]) >= row.get("sequence", 0):
            failures.append(f"seal reuse precedes its successful receipt: {row.get('event_id')}")
        # Same-process receipts intentionally cache event_id=None; recovery
        # reconstructs it from the ledger. A present reference must resolve,
        # while the content identity and ordering remain mandatory either way.
        if data.get("sealed_event_id") is not None and data.get("sealed_event_id") not in sealed_event_ids:
            failures.append(f"seal reuse points to an unknown receipt: {row.get('event_id')}")

    clock_sealed = {
        (row.get("data") or {}).get("theory")
        for row in rows
        if row.get("kind") == "time_budget_sealed"
    }
    missing_clock_state = sorted(sealed_theories - clock_sealed)
    if missing_clock_state:
        failures.append(f"successful seals missing recoverable clock state: {missing_clock_state}")
    if not rows or rows[-1].get("kind") not in {"experiment_paused", "experiment_finished"}:
        failures.append("ledger lacks a clean terminal pause/finish event")

    active_state = property_value(args.test_service, "ActiveState")
    main_status = property_value(args.test_service, "ExecMainStatus")
    if active_state not in {"inactive", "failed"}:
        failures.append(f"test service did not stop: {active_state}")
    if main_status not in {"", "0"}:
        failures.append(f"test service exit status was {main_status}")
    leaked = inspect_test_containers(args.test_root)
    if leaked:
        failures.append(f"test-owned containers remain: {leaked}")

    return {
        "schema": "vero-seal-idempotency-acceptance-v1",
        "passed": not failures,
        "failures": failures,
        "started_unix": args.started_unix,
        "deadline_unix": args.deadline_unix,
        "stopped_unix": stopped_at,
        "elapsed_wall_seconds": stopped_at - args.started_unix,
        "test_service": args.test_service,
        "test_output": str(args.test_output),
        "ledger_events": len(rows),
        "event_counts": dict(sorted(kinds.items())),
        "successful_seal_identities": len(sealed),
        "seal_reuse_events": len(reused),
        "duplicate_attempts": duplicate_attempts,
        "duplicate_results": duplicate_results,
        "clock_sealed_theories": sorted(item for item in clock_sealed if item),
        "service_active_state": active_state,
        "service_exec_main_status": main_status,
        "test_owned_running_containers": leaked,
    }


def launch_formal(args: argparse.Namespace) -> dict:
    command = [
        "systemd-run", "--user", f"--unit={args.formal_service.removesuffix('.service')}",
        f"--property=WorkingDirectory={args.formal_project}",
        "--property=KillMode=mixed", "--property=TimeoutStopSec=180",
        f"--property=StandardOutput=append:{args.formal_log}",
        f"--property=StandardError=append:{args.formal_log}",
        f"--setenv=PYTHONPATH={args.formal_project}",
        str(args.python), "-m", "regen.tools.vero_serial_application",
        "--vero-source", str(args.formal_vero_source),
        "--benchmark", str(args.benchmark), "--dataset", str(args.dataset),
        "--output", str(args.formal_output), "--client", str(args.client),
        "--auth", str(args.auth), "--image", args.image, "--proxy", args.proxy,
        "--model", "gpt-5.6-sol", "--reasoning", "medium",
        "--expected-contracts", "287", "--active-wall-seconds", "172800",
        "--check-timeout", "900",
    ]
    launched = run(command, check=False)
    time.sleep(5)
    active_state = property_value(args.formal_service, "ActiveState")
    result = {
        "schema": "vero-formal-launch-after-acceptance-v1",
        "started": launched.returncode == 0 and active_state in {"active", "activating"},
        "launched_unix": time.time(),
        "service": args.formal_service,
        "release": str(args.formal_root),
        "output": str(args.formal_output),
        "active_state": active_state,
        "systemd_run_returncode": launched.returncode,
        "systemd_run_stdout": launched.stdout,
        "systemd_run_stderr": launched.stderr,
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "expected_contracts": 287,
        "active_wall_seconds": 172800,
        "source_acceptance": str(args.acceptance),
    }
    atomic_json(args.formal_root / "LAUNCH.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-service", required=True)
    parser.add_argument("--test-root", required=True, type=Path)
    parser.add_argument("--test-output", required=True, type=Path)
    parser.add_argument("--started-unix", required=True, type=float)
    parser.add_argument("--deadline-unix", required=True, type=float)
    parser.add_argument("--acceptance", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--client", required=True, type=Path)
    parser.add_argument("--auth", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--formal-service", required=True)
    parser.add_argument("--formal-root", required=True, type=Path)
    parser.add_argument("--formal-project", required=True, type=Path)
    parser.add_argument("--formal-vero-source", required=True, type=Path)
    parser.add_argument("--formal-output", required=True, type=Path)
    parser.add_argument("--formal-log", required=True, type=Path)
    args = parser.parse_args()

    if args.deadline_unix - args.started_unix < 7200:
        atomic_json(args.status, {"state": "invalid", "reason": "deadline shorter than two hours"})
        return 2
    while time.time() < args.deadline_unix:
        active = property_value(args.test_service, "ActiveState")
        atomic_json(args.status, {
            "state": "running", "observed_unix": time.time(),
            "deadline_unix": args.deadline_unix, "test_service_active_state": active,
            "remaining_wall_seconds": max(0, args.deadline_unix - time.time()),
        })
        if active not in {"active", "activating"}:
            result = {
                "schema": "vero-seal-idempotency-acceptance-v1", "passed": False,
                "failures": [f"test service stopped unexpectedly before deadline: {active}"],
                "started_unix": args.started_unix, "deadline_unix": args.deadline_unix,
                "observed_unix": time.time(), "formal_started": False,
            }
            atomic_json(args.acceptance, result)
            atomic_json(args.status, {"state": "failed", "acceptance": str(args.acceptance)})
            return 1
        time.sleep(min(30, max(1, args.deadline_unix - time.time())))

    run(["systemctl", "--user", "kill", "--signal=SIGINT", "--kill-who=main", args.test_service], check=False)
    stop_deadline = time.time() + 240
    while time.time() < stop_deadline and property_value(args.test_service, "ActiveState") not in {"inactive", "failed"}:
        time.sleep(5)
    if property_value(args.test_service, "ActiveState") not in {"inactive", "failed"}:
        run(["systemctl", "--user", "stop", args.test_service], check=False)
    stopped_at = time.time()
    result = audit(args, stopped_at)
    result["formal_started"] = False
    atomic_json(args.acceptance, result)
    if not result["passed"]:
        atomic_json(args.status, {"state": "failed", "acceptance": str(args.acceptance)})
        return 1

    launch = launch_formal(args)
    result["formal_started"] = launch["started"]
    result["formal_launch"] = str(args.formal_root / "LAUNCH.json")
    if not launch["started"]:
        result["passed"] = False
        result["failures"].append("formal service failed to enter active state")
    atomic_json(args.acceptance, result)
    atomic_json(args.status, {
        "state": "formal_started" if launch["started"] else "formal_launch_failed",
        "acceptance": str(args.acceptance), "formal_launch": str(args.formal_root / "LAUNCH.json"),
    })
    return 0 if launch["started"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
