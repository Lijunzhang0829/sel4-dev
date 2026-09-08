#!/usr/bin/env python3
"""OSDI 2026 Stepwise-Codex backend for one certified theorem node."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from regen.llm_transcript import allocate_artifact_prefix, call_llm
from regen.module_run_log import attach_module_outputs, write_llm_module_log
from regen.progress_events import emit_event
from regen.premise_retrieval import (
    retrieve_proof_premises, semantic_bridge_guidance, semantic_bridge_tokens,
)
from regen.stepwise_codex import (
    StepwiseModelServer,
    build_step_prompt,
    decode_state_request,
    parse_steps,
    ranked_steps,
)
from regen.isabelle_node_checker import SESSION_CATALOGS, wrap_context
from regen.automation_advisor import advice_prompt, build_probe_advice
from regen.local_proof_outline import outline_prompt_block, plan_initial_outline


def allocate_stepwise_port() -> int:
    """Ask the OS for a currently unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def classify_stepwise_failure(
    stepwise: dict[str, Any], *, launcher_stdout: str = "",
    launcher_stderr: str = "", returncode: int | None = None,
) -> dict[str, Any]:
    """Separate runtime failures from theorem-search evidence."""
    text = "\n".join(filter(None, [
        str(stepwise.get("error", "")), launcher_stdout, launcher_stderr,
    ])).lower()
    infrastructure_patterns = [
        (
            "STEPWISE_PORT_CONFLICT",
            ("bindexception", "address already in use", "eaddrinuse"),
        ),
        (
            "STEPWISE_TARGET_INITIALIZATION_ERROR",
            (
                "failed to start isarepl", "failed to initialize isarepl",
                "target initialization failed", "could not start isarepl",
            ),
        ),
        (
            "STEPWISE_MODEL_PROVIDER_ERROR",
            (
                "model server is closing", "codex returned no executable",
                "failed to get results from prover",
                "model provider request failed", "provider error",
                "llm request failed",
            ),
        ),
        (
            "STEPWISE_CONTAINER_ERROR",
            (
                "cannot connect to the docker daemon", "docker: error",
                "no such container", "mount denied", "unable to find image",
            ),
        ),
    ]
    for failure_class, patterns in infrastructure_patterns:
        if any(pattern in text for pattern in patterns):
            return {
                "failure_class": failure_class,
                "failure_scope": "infrastructure",
                "consumes_proof_budget": False,
                "graph_revision_eligible": False,
            }
    if returncode == 124 or "wall-time budget exhausted" in text:
        return {
            "failure_class": "STEPWISE_SEARCH_TIMEOUT",
            "failure_scope": "proof-search",
            "consumes_proof_budget": True,
            "graph_revision_eligible": True,
        }
    return {
        "failure_class": "STEPWISE_SEARCH_EXHAUSTED",
        "failure_scope": "proof-search",
        "consumes_proof_budget": True,
        "graph_revision_eligible": True,
    }


class AuditedCodexStepGenerator:
    def __init__(
        self, audit_dir: Path, *, model: str, width: int, timeout: int,
        premise_hints: list[dict[str, Any]], theory: str,
        repair_guidance: str = "", premise_database: Path | None = None,
        generated_records: Path | None = None, retrieval_query: str = "",
        local_proof_outline: dict[str, Any] | None = None,
    ) -> None:
        self.audit_dir = audit_dir
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.width = width
        self.timeout = timeout
        self.premise_hints = premise_hints
        self.theory = theory
        self.repair_guidance = repair_guidance
        self.premise_database = premise_database
        self.generated_records = generated_records
        self.retrieval_query = retrieval_query
        self.local_proof_outline = local_proof_outline
        self.forbidden_facts = extract_forbidden_facts(repair_guidance)
        self.sequence = 0
        self.lock = threading.Lock()
        self.accepting = threading.Event()
        self.accepting.set()

    def stop_accepting(self) -> None:
        self.accepting.clear()

    def wait_idle(self) -> None:
        # Drain the in-flight Codex turn before another backend starts.
        with self.lock:
            pass

    def generate(self, state: str) -> list[list[Any]]:
        if not self.accepting.is_set():
            raise RuntimeError("Stepwise model server is closing")
        with self.lock:
            if not self.accepting.is_set():
                raise RuntimeError("Stepwise model server is closing")
            self.sequence += 1
            state, proof_prefix = decode_state_request(state)
            state_guidance = semantic_bridge_guidance(state)
            premise_hints = self.premise_hints
            if self.premise_database is not None:
                dynamic = retrieve_proof_premises(
                    self.premise_database,
                    " ".join([self.retrieval_query, self.repair_guidance]),
                    limit=32,
                    generated_records=self.generated_records,
                    retrieval_hint=self.repair_guidance,
                    residual=state,
                )
                premise_hints = merge_premises(self.premise_hints, dynamic)
            automation_advice = build_probe_advice(premise_hints, state)
            prompt = (
                "This is a closed proof-state call. Do not invoke tools, skills, "
                "shell commands, file reads, repository search, or network search. "
                "Use only this prompt and return proof-step candidates directly.\n\n"
                + build_step_prompt(
                    state, self.width, proof_prefix=proof_prefix,
                    forbidden_facts=self.forbidden_facts,
                )
            )
            prompt += (
                "\nIsabelle identifiers are case-sensitive. Do not replace a "
                "lower-case abstract operation with a similarly named capitalized "
                "concrete operation. A name in forbidden_facts is forbidden as a "
                "cited theorem; it may still occur as an object-language constant "
                "in the fixed goal.\n"
            )
            if premise_hints:
                prompt += "\nLegal retrieved premise hints:\n" + "\n".join(
                    f"- {item['canonical_name']} ({item['owner_theory']}): "
                    f"{item['exact_statement'][:1600]}"
                    for item in premise_hints[:24]
                )
            prompt += advice_prompt(automation_advice)
            prompt += outline_prompt_block(self.local_proof_outline)
            if state_guidance:
                prompt += "\nState-phase guidance:\n" + "\n".join(
                    f"- {item}" for item in state_guidance
                )
            if self.repair_guidance:
                prompt += "\nPrior audited repair guidance:\n" + self.repair_guidance
            prefix = allocate_artifact_prefix(
                self.audit_dir / f"{self.sequence:04d}-proof-step"
            )
            prefix.with_suffix(".premises.json").write_text(
                json.dumps({
                    "proof_state": state,
                    "proof_prefix": proof_prefix,
                    "retrieval_query": self.retrieval_query,
                    "repair_guidance": self.repair_guidance,
                    "state_bridge_terms": semantic_bridge_tokens(state),
                    "state_phase_guidance": state_guidance,
                    "forbidden_facts": sorted(self.forbidden_facts),
                    "records": premise_hints,
                    "automation_advice": automation_advice,
                    "local_proof_outline": self.local_proof_outline,
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            answer = call_llm(
                "codex", prompt, prefix, model=self.model, timeout=self.timeout,
                theory=self.theory, stage="stepwise-lemma-prover",
                clean_runtime=True,
            )
            steps = parse_steps(answer, self.width)
            steps = [
                step for step in steps
                if not any(
                    re.search(rf"(?<![A-Za-z0-9_']){re.escape(fact)}(?![A-Za-z0-9_'])", step)
                    for fact in self.forbidden_facts
                )
            ]
            if not steps:
                raise RuntimeError("Codex returned no executable Stepwise candidates")
            advisor_steps = [
                item["command"]
                for item in automation_advice["candidate_actions"]
            ]
            combined = []
            ordered_steps = [*steps[:1], *advisor_steps, *steps[1:]]
            for step in ordered_steps:
                if step not in combined:
                    combined.append(step)
            return ranked_steps(combined[: self.width])


def merge_premises(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for group in groups:
        for item in group:
            identity = item.get("record_id", item.get("canonical_name"))
            if identity in seen:
                continue
            seen.add(identity)
            result.append(item)
    return result


def extract_forbidden_facts(text: str) -> set[str]:
    result = set(re.findall(
        r'Undefined fact:\s*["`]?([A-Za-z][A-Za-z0-9_\'.]*)', text,
        flags=re.I,
    ))
    result.update(re.findall(
        r'(?:avoid|without)\s+(?:the\s+)?(?:nonexistent|undefined|unavailable)\s+'
        r'["`]?([A-Za-z][A-Za-z0-9_\'.]*)',
        text, flags=re.I,
    ))
    for match in re.finditer(r"\bAvoid\s+([^;\n]+)", text, flags=re.I):
        result.update(re.findall(
            r"`([A-Za-z][A-Za-z0-9_'.]*)`", match.group(1),
        ))
    return {item.rstrip(".") for item in result}


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)[-80:]


def build_warm_start_request(
    node: dict[str, Any], previous_round_feedback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Bind a prior generated proof to this exact statement for REPL replay."""
    if not previous_round_feedback:
        return None
    if previous_round_feedback.get("statement") != node["statement"]:
        return None
    candidates = [
        {
            "attempt": item.get("attempt"),
            "proof_body": str(item.get("proof_body", "")).strip(),
            "diagnostic": str(item.get("diagnostic", "")),
        }
        for item in previous_round_feedback.get("proof_candidates", [])
        if str(item.get("proof_body", "")).strip()
    ][-5:]
    proof_body = previous_round_feedback.get("last_proof_body", "").strip()
    if proof_body and not candidates:
        candidates = [{
            "attempt": None, "proof_body": proof_body,
            "diagnostic": previous_round_feedback.get("residual", ""),
        }]
    if not candidates:
        return None
    return {
        "schema_version": 1,
        "mode": "verified-prefix-replay",
        "statement_sha256": hashlib.sha256(
            node["statement"].encode("utf-8")
        ).hexdigest(),
        "source_round": previous_round_feedback.get("round_id"),
        "source_backend": previous_round_feedback.get("backend"),
        "proof_body": candidates[-1]["proof_body"],
        "proof_candidates": candidates,
        "residual": previous_round_feedback.get("residual", ""),
        "policy": {
            "accept_only_isabelle_replayed_commands": True,
            "stop_at_first_failed_command": True,
            "select_deepest_verified_candidate": True,
            "fallback_to_statement_start_when_empty": True,
        },
    }


def stepwise_search_budget(whole_proof_attempts: int) -> dict[str, int]:
    """Keep tree depth independent while respecting the wall-time envelope."""
    return {
        "max_attempts": max(8, whole_proof_attempts * 2),
        "selected_states": 2,
        "max_depth": 48,
    }


def _stepwise_context(context: list[str]) -> list[str]:
    # Search and final replay must elaborate the theorem in the same locale context.
    return list(context)


def _dependency_closure(by_id: dict[str, dict[str, Any]], node_id: str) -> set[str]:
    result: set[str] = set()
    pending = list(by_id[node_id]["dependencies"])
    while pending:
        dependency = pending.pop()
        if dependency in result:
            continue
        result.add(dependency)
        pending.extend(by_id[dependency]["dependencies"])
    return result


def _target_source(
    graph: dict[str, Any], node_id: str, proofs: dict[str, str],
    imports: list[str], theory_name: str,
) -> str:
    by_id = {node["id"]: node for node in graph["nodes"]}
    required = _dependency_closure(by_id, node_id)
    blocks = []
    for current in graph["execution_order"]:
        node = by_id[current]
        if current in required:
            blocks.append(wrap_context(
                _stepwise_context(node.get("context", [])),
                node["statement"] + "\n" + proofs[current].strip(),
            ))
        if current == node_id:
            blocks.append(wrap_context(
                _stepwise_context(node.get("context", [])),
                node["statement"] + "\n  oops",
            ))
            break
    return "\n".join([
        f"theory {theory_name}",
        "  imports",
        *(f'    "{item}"' for item in imports),
        "begin",
        "",
        "unbundle l4v_word_context",
        "",
        *blocks,
        "",
        "end",
        "",
    ])


def prove_node_stepwise(
    graph: dict[str, Any], node_id: str, proofs: dict[str, str], *,
    checker: Any, premise_database: Path, output_dir: Path,
    generated_records: Path | None = None, model: str = "gpt-5.5",
    timeout: int = 1800, width: int = 8, max_attempts: int = 10,
    selected_states: int = 2, max_depth: int = 48,
    hammer_leaves: int = 0, round_id: int = 1,
    retrieval_hint: str = "",
    previous_round_feedback: dict[str, Any] | None = None,
    think_timeout: int | None = None,
    outline_provider: str = "codex", outline_model: str = "gpt-5.5",
) -> dict[str, Any]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    node = by_id[node_id]
    retrieved = retrieve_proof_premises(
        premise_database,
        " ".join([node["statement"], node.get("strategy", ""), retrieval_hint]),
        limit=32, generated_records=generated_records,
        retrieval_hint=retrieval_hint,
        residual=(previous_round_feedback or {}).get("residual", ""),
    )
    dependency_premises = [{
        "record_id": f"generated-dependency:{dependency_id}",
        "canonical_name": by_id[dependency_id]["name"],
        "exact_statement": by_id[dependency_id]["statement"],
        "owner_theory": graph["theory"],
        "owner_session": "current_generation",
        "source_class": "green_generated_dependency",
        "attributes": [],
        "score": float("inf"),
    } for dependency_id in node["dependencies"]]
    premises = merge_premises(dependency_premises, retrieved)
    safe = _safe_name(node_id)
    node_dir = (
        output_dir / "nodes" / safe / f"round-{round_id:02d}"
    ).resolve()
    (node_dir / "premise-retrieval.json").parent.mkdir(
        parents=True, exist_ok=True,
    )
    per_think_timeout = max(
        1, min(timeout, think_timeout if think_timeout is not None else 180)
    )
    (node_dir / "premise-retrieval.json").write_text(
        json.dumps({
            "query_statement": node["statement"],
            "query_strategy": node.get("strategy", ""),
            "retrieval_hint": retrieval_hint,
            "search_budget": {
                "max_attempts": max_attempts,
                "selected_states": selected_states,
                "max_depth": max_depth,
                "width": width,
            },
            "round_timeout_seconds": timeout,
            "think_timeout_seconds": per_think_timeout,
            "records": premises,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    outline_dependencies = [{
        "id": dependency_id,
        "name": by_id[dependency_id]["name"],
        "statement": by_id[dependency_id]["statement"],
    } for dependency_id in node["dependencies"]]
    outline_log = (
        output_dir.parent.parent / "module-logs/local-proof-outline"
        / f"{graph['file_slot'].removesuffix('.thy')}--{safe}--round-{round_id:02d}.json"
    )
    local_outline = plan_initial_outline(
        node, outline_dependencies, retrieved,
        output_prefix=node_dir / "local-proof-outline" / "initial",
        provider=outline_provider, model=outline_model,
        timeout=per_think_timeout, theory=graph["theory"],
        module_log_path=outline_log,
    )
    target_dir = node_dir / "stepwise-target"
    target_dir.mkdir(parents=True, exist_ok=True)
    warm_start = build_warm_start_request(node, previous_round_feedback)
    warm_start_path = target_dir / "warm-start-request.json"
    if warm_start is not None:
        warm_start_path.write_text(
            json.dumps(warm_start, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    theory_name = f"Stepwise_{safe}"
    session_name = f"Stepwise_{safe}_Session"
    theory_file = target_dir / f"{theory_name}.thy"
    theory_file.write_text(
        _target_source(graph, node_id, proofs, checker.imports(), theory_name),
        encoding="utf-8",
    )
    parent = "Generated_Refine" if checker.previous_theories else checker.base_session
    (target_dir / "ROOT").write_text("\n".join([
        f"session {session_name} = {parent} +",
        "  theories",
        f"    {theory_name}",
        "",
    ]), encoding="utf-8")

    host_llm_dir = node_dir / "stepwise-host-llm"
    container_audit = node_dir / "stepwise-run"
    generator = AuditedCodexStepGenerator(
        host_llm_dir, model=model, width=width, timeout=per_think_timeout,
        premise_hints=premises, theory=graph["theory"],
        repair_guidance=retrieval_hint,
        premise_database=premise_database,
        generated_records=generated_records,
        retrieval_query=" ".join([node["statement"], node.get("strategy", "")]),
        local_proof_outline=local_outline,
    )

    repo = Path(__file__).resolve().parent.parent
    l4v = checker.l4v_dir
    workspace = checker.semantic_workspace
    isabelle = repo / "verification" / "isabelle"
    stepwise_tools = repo / "tools" / "seL4-proof-search"
    site_packages = repo / ".stepwise-codex" / "container-site-packages"
    py4j_root = repo / ".stepwise-codex" / "site-packages"
    py4j_package = py4j_root / "py4j"
    py4j_share = py4j_root / "share" / "py4j"
    adapter = repo / "regen" / "stepwise_codex.py"
    paths = [
        l4v, workspace, isabelle, stepwise_tools,
        site_packages, py4j_package, py4j_share, adapter,
    ]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise ValueError(f"Stepwise runtime is incomplete: {missing}")

    session_dirs = [
        checker.base_catalog,
        *(f"/sel4-project/verification/l4v/{item}" for item in SESSION_CATALOGS),
    ]
    if checker.previous_theories:
        session_dirs.append("/generated")
    isabelle_path = ":".join(session_dirs)
    container_name = re.sub(
        r"[^A-Za-z0-9_.-]", "-",
        f"regen-stepwise-{graph['run_id']}-{safe}-r{round_id}-{os.getpid()}",
    )[:180]
    isa_port = allocate_stepwise_port()
    command = [
        "docker", "run", "--rm", "--name", container_name,
        "--network", "host",
        "--mount", f"type=bind,src={l4v},dst=/sel4-project/verification/l4v,readonly",
        "--mount", f"type=bind,src={isabelle},dst=/workspace/verification/isabelle,readonly",
        "--mount", f"type=bind,src={workspace},dst=/m2,readonly",
        "--mount", f"type=bind,src={checker.heaps_dir},dst=/root/.isabelle/heaps",
        "--mount", f"type=bind,src={checker.generated_dir},dst=/generated,readonly",
        "--mount", f"type=bind,src={stepwise_tools},dst=/stepwise,readonly",
        "--mount", f"type=bind,src={site_packages},dst=/site-packages,readonly",
        "--mount", f"type=bind,src={py4j_package},dst=/py4j-site/py4j,readonly",
        "--mount", f"type=bind,src={py4j_share},dst=/py4j-site/share/py4j,readonly",
        "--mount", f"type=bind,src={adapter},dst=/adapter/stepwise_codex.py,readonly",
        "--mount", f"type=bind,src={target_dir},dst=/target,readonly",
        "--mount", f"type=bind,src={node_dir},dst=/output",
        "-e", "L4V_ARCH=ARM",
        "-e", "ISABELLE_HOME=/workspace/verification/isabelle",
        "-e", f"ISABELLE_PATH={isabelle_path}",
        "-e", f"STEPWISE_SESSION_DIRS={isabelle_path}",
        "-e", "ISA_REPL_PATH=/stepwise/Isa-Repl/target/IsaREPL.jar",
        "-e", "JAVA_HOME=/root/.isabelle/contrib/jdk-21.0.3/x86_64-linux",
        "-e", "STEPWISE_PROVER_ROOT=/stepwise/seL4-prover",
        "-e", "PYTHONPATH=/site-packages:/py4j-site:/stepwise/seL4-prover:/adapter",
        checker.image,
        "python3", "/adapter/stepwise_codex.py",
        "--generation-id", f"{graph['run_id']}:{graph['theory']}:{safe}:r{round_id}",
        "--theory-file", f"/target/{theory_file.name}",
        "--session-root", "/target",
        "--session", session_name,
        "--theory-name", theory_name,
        "--lemma-name", node["name"],
        "--statement", node["statement"],
        "--audit-dir", "/output/stepwise-run",
        "--model", model,
        "--external-model-url", "",
        "--width", str(width),
        "--max-attempts", str(max_attempts),
        "--selected-states", str(selected_states),
        "--max-depth", str(max_depth),
        "--hammer-leaves", str(hammer_leaves),
        "--llm-timeout", str(per_think_timeout),
        "--isa-port", str(isa_port),
    ]
    if warm_start is not None:
        command.extend([
            "--warm-start-request", f"/target/{warm_start_path.name}",
        ])

    stepwise_started = time.time()
    stepwise_finished = threading.Event()
    emit_event(
        "stepwise_started", theory=graph["theory"], node_id=node_id,
        node=node["name"], round_id=round_id, timeout_seconds=timeout,
        container=container_name, warm_start=(warm_start is not None),
        isa_port=isa_port,
    )

    def stepwise_heartbeat() -> None:
        while not stepwise_finished.wait(30):
            emit_event(
                "stepwise_heartbeat", theory=graph["theory"],
                node_id=node_id, node=node["name"], round_id=round_id,
                elapsed_seconds=int(time.time() - stepwise_started),
                container=container_name,
            )

    heartbeat_thread = threading.Thread(
        target=stepwise_heartbeat, daemon=True,
    )
    heartbeat_thread.start()
    process = None
    try:
        with StepwiseModelServer(generator) as server:
            index = command.index("--external-model-url") + 1
            command[index] = server.address
            try:
                process = subprocess.run(
                    command, text=True, capture_output=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired as error:
                server.stop_accepting()
                subprocess.run(
                    ["docker", "rm", "-f", container_name], capture_output=True,
                )
                stdout = error.stdout or ""
                stderr = error.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                process = subprocess.CompletedProcess(
                    command, 124, stdout,
                    stderr + f"\nStepwise wall-time budget exhausted after {timeout}s\n",
                )
            except BaseException:
                subprocess.run(
                    ["docker", "rm", "-f", container_name], capture_output=True,
                )
                raise
    finally:
        stepwise_finished.set()
        heartbeat_thread.join(timeout=1)
        emit_event(
            "stepwise_finished", theory=graph["theory"], node_id=node_id,
            node=node["name"], round_id=round_id,
            wall_ms=int((time.time() - stepwise_started) * 1000),
            returncode=(process.returncode if process is not None else None),
            timed_out=(process is not None and process.returncode == 124),
            container=container_name, isa_port=isa_port,
        )
    subprocess.run([
        "docker", "run", "--rm",
        "--mount", f"type=bind,src={node_dir},dst=/output",
        checker.image, "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/output",
    ], text=True, capture_output=True, check=False)
    (node_dir / "launcher.stdout.txt").write_text(process.stdout, encoding="utf-8")
    (node_dir / "launcher.stderr.txt").write_text(process.stderr, encoding="utf-8")
    result_path = container_audit / "result.json"
    stepwise = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.is_file() else {
            "status": "RED",
            "error": (process.stdout + process.stderr)[-12000:],
            "proof": [],
            "returncode": process.returncode,
        }
    )
    if not result_path.is_file():
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(stepwise, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    module_log_path = (
        output_dir.parent.parent / "module-logs/lemma-prover"
        / (
            f"{graph['file_slot'].removesuffix('.thy')}--{safe}"
            f"--round-{round_id:02d}.json"
        )
    )
    for meta_path in sorted(host_llm_dir.glob("*.meta.json")):
        prefix = Path(str(meta_path).removesuffix(".meta.json"))
        premise_path = prefix.with_suffix(".premises.json")
        write_llm_module_log(
            prefix, module_log_path, module="Lemma Prover",
            run_name=(
                f"{graph['file_slot'].removesuffix('.thy')}--{node_id}"
                f"--round-{round_id:02d}"
            ),
            context={
                "theory": graph["theory"], "node_id": node_id,
                "round_id": round_id, "backend": "stepwise-codex",
                "step_call": prefix.name,
            },
            outputs=(
                {"state_premise_retrieval": premise_path}
                if premise_path.is_file() else None
            ),
        )
    if module_log_path.is_file() and result_path.is_file():
        outputs = {"stepwise_result": result_path}
        warm_start_replay = container_audit / "warm-start-replay.json"
        if warm_start_replay.is_file():
            outputs["warm_start_replay"] = warm_start_replay
        latest_premises = sorted(host_llm_dir.glob("*.premises.json"))
        if latest_premises:
            outputs["state_premise_retrieval"] = latest_premises[-1]
        attach_module_outputs(
            module_log_path, outputs,
        )
    if stepwise.get("status") != "GREEN":
        classification = classify_stepwise_failure(
            stepwise, launcher_stdout=process.stdout,
            launcher_stderr=process.stderr, returncode=process.returncode,
        )
        return {
            "status": "red", "node_id": node_id, "backend": "stepwise-codex",
            **classification,
            "stepwise_search_status": stepwise.get("status", "RED"),
            "final_replay_status": "not-run",
            "attempts": [], "residual": stepwise.get("error", ""),
            "retrieved_premises": [item["record_id"] for item in premises],
            "retrieved_premise_names": [item["canonical_name"] for item in premises],
            "stepwise_result": stepwise,
            "local_proof_outline": local_outline,
            "isa_port": isa_port,
        }

    proof = "\n".join(stepwise["proof"]).strip()
    check = checker.check_node(graph, {**proofs, node_id: proof}, node_id)
    if check["verdict"] != "green":
        return {
            "status": "red", "node_id": node_id, "backend": "stepwise-codex",
            "failure_class": "STEPWISE_REPLAY_MISMATCH",
            "stepwise_search_status": "GREEN",
            "final_replay_status": "RED",
            "attempts": [{"proof_body": proof, "check": check}],
            "residual": check["diagnostic"],
            "retrieved_premises": [item["record_id"] for item in premises],
            "stepwise_result": stepwise,
            "local_proof_outline": local_outline,
        }
    return {
        "status": "green", "node_id": node_id, "backend": "stepwise-codex",
        "stepwise_search_status": "GREEN",
        "final_replay_status": "GREEN",
        "proof_body": proof,
        "attempts": [{"proof_body": proof, "check": check}],
        "retrieved_premises": [item["record_id"] for item in premises],
        "stepwise_result": stepwise,
        "local_proof_outline": local_outline,
    }
