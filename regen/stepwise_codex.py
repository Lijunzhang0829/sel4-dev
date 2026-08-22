#!/usr/bin/env python3
"""Run the Stepwise Isabelle tree search with an audited Codex relay adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


FORBIDDEN_STEPS = {"oops", "sorry"}
STATE_ENVELOPE_KEY = "regen_stepwise_state_v1"
OUTER_PROOF_COMMANDS = {
    "apply", "apply_end", "back", "by", "case", "defer", "done", "finally",
    "fix", "from", "have", "moreover", "next", "note", "prefer", "proof",
    "qed", "show", "subgoal", "supply", "then", "ultimately", "unfolding",
    "using", "with",
}


class PermanentModelError(RuntimeError):
    """A model response that retrying unchanged cannot repair."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_json(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # A literal Isabelle symbol escape (for example \<lambda>) is valid in
        # proof source but not as a JSON escape. Repair only this boundary and
        # leave standard JSON escapes untouched.
        repaired = re.sub(r"(?<!\\)\\(?=<)", r"\\\\", stripped)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
        start_candidates = [i for i in (stripped.find("{"), stripped.find("[")) if i >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(stripped.rfind("}"), stripped.rfind("]"))
        if end < start:
            raise
        return json.loads(stripped[start : end + 1])


def parse_steps(text: str, limit: int) -> list[str]:
    try:
        payload = extract_json(text)
    except (json.JSONDecodeError, ValueError):
        payload = [line.strip() for line in text.splitlines() if line.strip()]

    if isinstance(payload, dict):
        payload = payload.get("steps", payload.get("candidates", []))
    if not isinstance(payload, list):
        return []

    steps: list[str] = []
    for item in payload:
        if isinstance(item, dict):
            item = item.get("step", item.get("command", ""))
        if not isinstance(item, str):
            continue
        step = item.strip()
        if step.startswith("```") and step.endswith("```"):
            step = re.sub(r"^```(?:isabelle)?\s*|\s*```$", "", step, flags=re.I | re.S).strip()
        if not step or step in FORBIDDEN_STEPS or re.search(r"\b(sorry|oops)\b", step):
            continue
        first = step.split(None, 1)[0]
        if first not in OUTER_PROOF_COMMANDS:
            step = f"apply ({step})"
        if step not in steps:
            steps.append(step)
        if len(steps) >= limit:
            break
    return steps


def ranked_steps(steps: list[str]) -> list[list[Any]]:
    size = max(1, len(steps))
    ranked = []
    for index, step in enumerate(steps):
        score = 1.0 - (index / size)
        if re.search(r"\b(?:auto|fastforce)\b", step):
            score -= 0.3
        ranked.append([step, score])
    return ranked


def encode_state_request(state: str, proof_prefix: list[str]) -> str:
    return json.dumps({
        STATE_ENVELOPE_KEY: True,
        "proof_state": state,
        "proof_prefix": proof_prefix,
    }, ensure_ascii=False)


def decode_state_request(value: str) -> tuple[str, list[str]]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return value, []
    if not isinstance(payload, dict) or not payload.get(STATE_ENVELOPE_KEY):
        return value, []
    state = payload.get("proof_state", "")
    prefix = payload.get("proof_prefix", [])
    if not isinstance(state, str) or not isinstance(prefix, list):
        return value, []
    return state, [item for item in prefix if isinstance(item, str)]


def build_step_prompt(
    state: str, width: int, *, proof_prefix: list[str] | None = None,
    forbidden_facts: set[str] | None = None,
) -> str:
    prefix = proof_prefix or []
    rejected = sorted(forbidden_facts or set())
    return f"""You propose the next command in an Isabelle/HOL proof search.

Return JSON only, with this exact shape:
{{"steps": ["one Isabelle command", "another Isabelle command"]}}

Rules:
- Return at most {width} distinct candidates, ordered most promising first.
- Each candidate must be one executable Isabelle toplevel proof command.
- Start tactic applications with `apply`, or use a complete `by ...` command.
- For example, return `apply (simp add: foo_def)`, not bare `simp add: foo_def`.
- Do not restate the lemma and do not return a complete theory file.
- Never use sorry, oops, axiomatization, or other proof bypasses.
- Use only facts visible in the proof state or explicitly listed in the legal
  retrieved premise hints supplied after this state block. Do not inspect files
  or call tools.
- Prefer targeted rule/simp/wp commands before broad expensive automation.
- When a legal named rule directly matches a local side condition, apply that
  rule before unfolding definitions into lower-level arithmetic or bit logic.
- Preserve the checked prefix. Propose only the next command needed from the
  current state; do not restart the proof from the theorem statement.
- When prior audited repair guidance contains a local proof outline, respect its
  ordering. In particular, do not apply a downstream semantic rule until the
  outline's constructor, guard, and normalization milestones are established.
- When the outline lists GREEN generated dependencies, use their exact statements
  from the legal premise hints. Advance one outline milestone at a time. Do not
  combine several dependency theorems inside one fastforce, clarsimp, auto, or
  metis call; preserve the assumptions needed to instantiate the next theorem.
- With multiple subgoals, address the first pending subgoal and its matching
  outline branch. Do not unfold both branches merely to reduce surface syntax.
- Do not invent a `*_def` fact. Cite one only when it appears in the legal
  premise hints or the current proof state.
- Avoid broad `auto` or `fastforce` when a sequence of named intermediate facts
  can make the dependency chain explicit.

Successful proof prefix already checked by Isabelle:
<proof_prefix>
{json.dumps(prefix, ensure_ascii=False)}
</proof_prefix>

Facts rejected by earlier Isabelle diagnostics; never cite them:
<forbidden_facts>
{json.dumps(rejected, ensure_ascii=False)}
</forbidden_facts>

Current Isabelle proof state:
<proof_state>
{state}
</proof_state>
"""


class CodexRelayStepGenerator:
    def __init__(
        self,
        *,
        audit_dir: Path,
        relay_url: str,
        relay_token: str,
        repo: str,
        model: str,
        width: int,
        timeout: int,
    ) -> None:
        self.audit_dir = audit_dir
        self.relay_url = relay_url
        self.relay_token = relay_token
        self.repo = repo
        self.model = model
        self.width = width
        self.timeout = timeout
        self.sequence = 0
        self.lock = threading.Lock()

    def generate(self, state: str) -> list[list[Any]]:
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            prefix = self.audit_dir / "llm" / f"{sequence:04d}-proof-step"
            prefix.mkdir(parents=True, exist_ok=False)
            prompt = build_step_prompt(state, self.width)
            (prefix / "prompt.txt").write_text(prompt, encoding="utf-8")

            request_payload = {
                "repo": self.repo,
                "model": self.model,
                "prompt": prompt,
                "write": False,
            }
            request = urllib.request.Request(
                self.relay_url,
                data=json.dumps(request_payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": "Bearer " + self.relay_token,
                    "Content-Type": "application/json",
                },
            )
            started = time.time()
            status_code = None
            error = ""
            raw = ""
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status_code = response.status
                    raw = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                status_code = exc.code
                raw = exc.read().decode("utf-8", errors="replace")
                error = str(exc)
            except Exception as exc:
                error = repr(exc)

            (prefix / "raw.json").write_text(raw, encoding="utf-8")
            answer = ""
            response_payload: dict[str, Any] = {}
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        response_payload = parsed
                        answer = str(parsed.get("answer") or parsed.get("result") or "")
                except json.JSONDecodeError:
                    answer = raw
            (prefix / "final.txt").write_text(answer, encoding="utf-8")
            steps = parse_steps(answer, self.width)
            ranked = ranked_steps(steps)
            (prefix / "parsed.json").write_text(
                json.dumps({"steps": steps, "ranked_steps": ranked}, indent=2) + "\n",
                encoding="utf-8",
            )
            relay_error = error or str(response_payload.get("error") or "")
            meta = {
                "provider": "codex-relay",
                "model": self.model,
                "repo": self.repo,
                "relay_url": self.relay_url,
                "status_code": status_code,
                "error": relay_error,
                "session_id": response_payload.get("session_id"),
                "thread_id": response_payload.get("thread_id"),
                "wall_ms": int((time.time() - started) * 1000),
                "prompt_sha256": sha256_text(prompt),
                "raw_sha256": sha256_text(raw),
                "final_sha256": sha256_text(answer),
                "parsed_step_count": len(steps),
                "write": False,
                "model_tool_audit": "unavailable-from-current-relay-api",
                "sealable_generation": False,
            }
            (prefix / "meta.json").write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            if relay_error:
                raise PermanentModelError(f"Codex relay reported an error: {relay_error}")
            if status_code != 200:
                raise RuntimeError(f"Codex relay call failed: HTTP {status_code}")
            if not steps:
                raise PermanentModelError(f"Codex returned no usable Isabelle commands: {prefix}")
            return ranked


class StepwiseModelServer:
    def __init__(self, generator: CodexRelayStepGenerator) -> None:
        self.generator = generator
        generator_ref = generator

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if self.path == "/generate_batch":
                        outputs = [generator_ref.generate(str(state)) for state in body.get("items", [])]
                    elif self.path == "/compute_logprob":
                        outputs = ranked_steps([str(step) for step in body.get("possible_steps", [])])
                    else:
                        self.send_error(404)
                        return
                    encoded = json.dumps({"outputs": outputs}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    encoded = json.dumps({"error": repr(exc)}).encode("utf-8")
                    self.send_response((422 if isinstance(exc, PermanentModelError) else 502))
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    try:
                        self.wfile.write(encoded)
                    except (BrokenPipeError, ConnectionResetError):
                        return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "StepwiseModelServer":
        self.thread.start()
        return self

    def stop_accepting(self) -> None:
        stop = getattr(self.generator, "stop_accepting", None)
        if stop is not None:
            stop()

    def __exit__(self, *_args: Any) -> None:
        self.stop_accepting()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        wait_idle = getattr(self.generator, "wait_idle", None)
        if wait_idle is not None:
            wait_idle()


class ExternalModelServer:
    """Use a separately audited host-side step generator."""

    def __init__(self, address: str) -> None:
        self.address = address

    def __enter__(self) -> "ExternalModelServer":
        return self

    def __exit__(self, *_args: Any) -> None:
        return


def container_path(path: Path) -> Path:
    host_root = Path("/home/lijun/seL4-docker-main")
    if not path.exists() and path.is_absolute() and path.is_relative_to(host_root):
        return Path("/workspace") / path.relative_to(host_root)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--theory-file", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--lemma-name", required=True)
    parser.add_argument("--theory-name", default="")
    parser.add_argument("--audit-dir", type=Path)
    parser.add_argument("--model", default=os.environ.get("REGEN_MODEL", "gpt-5.5"))
    parser.add_argument("--relay-url", default=os.environ.get("REGEN_CODEX_RELAY_URL", "http://127.0.0.1:18080/v1/codex"))
    parser.add_argument("--relay-repo", default=os.environ.get("REGEN_CODEX_RELAY_REPO", "default"))
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--selected-states", type=int, default=2)
    parser.add_argument("--max-depth", type=int, default=16)
    parser.add_argument("--hammer-leaves", type=int, default=8)
    parser.add_argument("--isa-port", type=int, default=25670)
    parser.add_argument("--llm-timeout", type=int, default=1800)
    parser.add_argument("--crafted-steps", action="store_true")
    parser.add_argument("--quickcheck", action="store_true")
    parser.add_argument("--external-model-url", default="")
    parser.add_argument("--warm-start-request", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    theory_file = container_path(args.theory_file.resolve())
    session_root = container_path(args.session_root.resolve())
    if not theory_file.is_file():
        raise SystemExit(f"theory file does not exist: {theory_file}")
    if args.statement not in theory_file.read_text(encoding="utf-8"):
        raise SystemExit("exact --statement is not present in --theory-file")
    try:
        theory_rel = theory_file.relative_to(session_root)
    except ValueError as exc:
        raise SystemExit("--theory-file must be below --session-root") from exc

    audit_dir = args.audit_dir or Path("reports/regen/stepwise-codex") / args.lemma_name / args.generation_id
    audit_dir = container_path(audit_dir.resolve())
    audit_dir.mkdir(parents=True, exist_ok=False)
    warm_start_request = None
    if args.warm_start_request is not None:
        warm_start_path = container_path(args.warm_start_request.resolve())
        warm_start_request = json.loads(warm_start_path.read_text(encoding="utf-8"))
        if warm_start_request.get("mode") != "verified-prefix-replay":
            raise SystemExit("unsupported warm-start request mode")
        if warm_start_request.get("statement_sha256") != sha256_text(args.statement):
            raise SystemExit("warm-start request belongs to another statement")
    run_config = {
        "generation_id": args.generation_id,
        "theory_file": str(theory_file),
        "theory_file_sha256": hashlib.sha256(theory_file.read_bytes()).hexdigest(),
        "session_root": str(session_root),
        "session": args.session,
        "statement": args.statement,
        "statement_sha256": sha256_text(args.statement),
        "lemma_name": args.lemma_name,
        "model": args.model,
        "width": args.width,
        "max_attempts": args.max_attempts,
        "selected_states": args.selected_states,
        "max_depth": args.max_depth,
        "hammer_leaves": args.hammer_leaves,
        "strict_prefix": True,
        "warm_start_mode": (
            "verified-prefix-replay" if warm_start_request else "statement-start"
        ),
        "warm_start_source_round": (
            warm_start_request.get("source_round") if warm_start_request else None
        ),
        "backend": "Stepwise TreeSearchProver + Codex relay",
        "stepwise_source_commit": os.environ.get("STEPWISE_SOURCE_COMMIT", "unknown"),
    }
    (audit_dir / "run.json").write_text(json.dumps(run_config, indent=2) + "\n", encoding="utf-8")
    if args.dry_run:
        (audit_dir / "result.json").write_text(json.dumps({"status": "DRY_RUN"}, indent=2) + "\n", encoding="utf-8")
        print(audit_dir)
        return 0

    token = os.environ.get("CODEX_RELAY_TOKEN", "")
    if not token and not args.external_model_url:
        raise SystemExit("CODEX_RELAY_TOKEN is not set")

    stepwise_root = Path(os.environ.get("STEPWISE_PROVER_ROOT", "/workspace/tools/seL4-proof-search/seL4-prover"))
    import sys

    # Stepwise loads tactic inventories from ./datasets during module import.
    os.chdir(stepwise_root)
    sys.path.insert(0, str(stepwise_root))
    from data.lemma import PureLemma
    from provers.treesearch_prover import (
        ScoredProofState, TreeSearchProver, TreeSearchProverConfig,
    )
    from utils.parser import parse_java_object
    from utils.repl import IsaRepl, ToplevelState

    original_run_jar = IsaRepl.run_jar

    def run_jar_and_wait(self: Any) -> None:
        original_run_jar(self)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("IsaREPL exited during startup")
            with socket.socket() as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.1)
        raise TimeoutError(f"IsaREPL did not listen on port {self.port} within 30 seconds")

    IsaRepl.run_jar = run_jar_and_wait

    original_initialize = IsaRepl.initialize
    extra_session_dirs = [
        item for item in os.environ.get("STEPWISE_SESSION_DIRS", "").split(os.pathsep)
        if item
    ]

    def initialize_with_session_dirs(
        self: Any, theory_file: str, working_dir: str = ".tmp",
        session: str = "HOL", session_dirs: list[str] | None = None,
    ) -> Any:
        directories = list(session_dirs or [])
        for item in extra_session_dirs:
            if item not in directories:
                directories.append(item)
        return original_initialize(
            self, theory_file, working_dir, session, directories,
        )

    IsaRepl.initialize = initialize_with_session_dirs

    def execute_steps_strict(self: Any, steps: list[str]) -> tuple[bool, str]:
        """Execute the target prefix without hiding a failed outer command."""
        trace: list[dict[str, Any]] = []
        message = "No proof found"
        execution_units: list[tuple[list[str], str]] = []
        cursor = 0
        while cursor < len(steps):
            source_steps = [steps[cursor]]
            command = steps[cursor]
            if (
                cursor + 1 < len(steps)
                and command.strip().startswith("interpretation ")
                and steps[cursor + 1].strip() == "."
            ):
                source_steps.append(steps[cursor + 1])
                command = command.rstrip() + " ."
                cursor += 1
            execution_units.append((source_steps, command))
            cursor += 1

        for index, (source_steps, command) in enumerate(execution_units, start=1):
            result = self.isa_repl._step(command)
            ok, msg = parse_java_object(result)
            trace.append({
                "index": index,
                "source_commands": source_steps,
                "command": command,
                "ok": ok,
                "message": msg,
            })
            (audit_dir / "target-initialization.json").write_text(
                json.dumps(trace, indent=2) + "\n", encoding="utf-8",
            )
            if not ok:
                raise RuntimeError(
                    f"target initialization failed at command {index}: "
                    f"{command!r}: {msg}"
                )
            self.current_tls = None
            self.current_prefix.extend(source_steps)
            self.current_msg = msg
            message = msg
        return True, message

    IsaRepl.execute_steps = execute_steps_strict

    original_select_state = TreeSearchProver.select_state
    original_focus = TreeSearchProver.focus
    original_get_next_state = TreeSearchProver.get_next_state

    def select_state_with_prefix(self: Any) -> list[str]:
        states = original_select_state(self)
        result = []
        for state in states:
            node_id = self.tree.state2id[state]
            proof_prefix = list(self.tree[str(node_id)].data.proof_steps)
            result.append(encode_state_request(state, proof_prefix))
        return result

    def focus_enveloped(
        self: Any, state: str, isa_repl: Any | None = None,
    ) -> None:
        raw_state, _ = decode_state_request(state)
        original_focus(self, raw_state, isa_repl)

    def get_next_state_penalized(
        self: Any, next_step: str, score_step: float, isa_repl: Any,
    ) -> Any:
        result = original_get_next_state(self, next_step, score_step, isa_repl)
        proof_state = result.result.state if result.result else ""
        if proof_state and proof_state in self.state2score:
            before = re.search(r"goal \((\d+) subgoals?\)", self.focus_state)
            after = re.search(r"goal \((\d+) subgoals?\)", proof_state)
            before_count = int(before.group(1)) if before else 1
            after_count = int(after.group(1)) if after else 1
            if after_count > max(before_count + 2, before_count * 2):
                self.state2score[proof_state] -= min(
                    0.5, 0.08 * (after_count - before_count),
                )
        return result

    TreeSearchProver.select_state = select_state_with_prefix
    TreeSearchProver.focus = focus_enveloped
    TreeSearchProver.get_next_state = get_next_state_penalized

    original_start_state = TreeSearchProver.start_state

    def start_state_with_verified_prefix(
        self: Any, isa_repl: Any,
    ) -> bool:
        if warm_start_request is None:
            return original_start_state(self, isa_repl)

        trace: dict[str, Any] = {
            "mode": "verified-prefix-replay",
            "source_round": warm_start_request.get("source_round"),
            "statement_sha256": warm_start_request.get("statement_sha256"),
            "candidate_replays": [],
            "accepted_prefix": [],
            "fallback_to_statement_start": False,
        }
        candidates = warm_start_request.get("proof_candidates") or [{
            "attempt": None,
            "proof_body": warm_start_request.get("proof_body", ""),
        }]
        def clone_tls(name: str) -> tuple[bool, Any]:
            try:
                ok, value = isa_repl.clone_tls(name)
            except ValueError as exc:
                if "unexpected result: True" not in str(exc):
                    raise
                ok, value = False, str(exc)
            if ok:
                return ok, value
            if "unexpected result: True" not in str(value):
                return ok, value
            state = ToplevelState(
                name=name,
                prefix=deepcopy(isa_repl.current_prefix),
                msg=isa_repl.current_msg,
            )
            isa_repl.current_tls = name
            isa_repl.tls_cache[name] = state
            return True, state

        def focus_tls(name: str) -> tuple[bool, str]:
            try:
                ok, value = isa_repl.focus_tls(name)
            except ValueError as exc:
                if "unexpected result: True" not in str(exc):
                    raise
                ok, value = False, str(exc)
            if ok:
                return ok, value
            if "unexpected result: True" not in str(value):
                return ok, value
            state = isa_repl.tls_cache[name]
            isa_repl.current_tls = name
            isa_repl.current_prefix = deepcopy(state.prefix)
            isa_repl.current_msg = state.msg
            return True, state.msg

        root_ok, root = clone_tls("warm_start_root")
        if not root_ok:
            trace["root_clone_error"] = str(root)
            (audit_dir / "warm-start-replay.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return original_start_state(self, isa_repl)

        best: tuple[int, int, list[str], str] | None = None
        for candidate_index, candidate in enumerate(candidates, start=1):
            focus_tls("warm_start_root")
            parsed_ok, parsed_steps = isa_repl.parse(
                candidate.get("proof_body", "")
            )
            parsed_steps = [step for step in parsed_steps if step.strip()]
            replay = {
                "candidate_index": candidate_index,
                "attempt": candidate.get("attempt"),
                "parse_ok": parsed_ok,
                "parsed_step_count": len(parsed_steps),
                "commands": [],
                "accepted_prefix": [],
            }
            accepted: list[str] = []
            if parsed_ok:
                for index, command in enumerate(parsed_steps, start=1):
                    if re.search(r"\b(?:sorry|oops)\b", command):
                        replay["commands"].append({
                            "index": index, "command": command, "ok": False,
                            "message": "proof bypass rejected by warm-start policy",
                        })
                        break
                    ok, message = isa_repl.step(command)
                    replay["commands"].append({
                        "index": index, "command": command, "ok": ok,
                        "message": message,
                    })
                    if not ok:
                        break
                    accepted.append(command)
            state = isa_repl.get_msg()
            match = re.search(r"goal \((\d+) subgoals?\)", state)
            subgoals = int(match.group(1)) if match else 0
            replay["accepted_prefix"] = accepted
            replay["accepted_step_count"] = len(accepted)
            replay["resulting_subgoals"] = subgoals
            replay["resulting_state"] = state
            trace["candidate_replays"].append(replay)
            score = (len(accepted), -subgoals)
            if accepted and (best is None or score > best[:2]):
                state_name = f"warm_candidate_{candidate_index}"
                clone_tls(state_name)
                best = (score[0], score[1], accepted, state_name)

        accepted = best[2] if best else []
        if best:
            focus_tls(best[3])
        else:
            focus_tls("warm_start_root")
        trace["selected_candidate_index"] = (
            int(best[3].rsplit("_", 1)[1]) if best else None
        )
        trace["accepted_prefix"] = accepted
        trace["accepted_step_count"] = len(accepted)
        trace["resulting_state"] = isa_repl.get_msg()
        trace["fallback_to_statement_start"] = not accepted
        (audit_dir / "warm-start-replay.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not accepted:
            return original_start_state(self, isa_repl)

        state = isa_repl.get_msg()
        self.tree.save_node(
            ScoredProofState(proof_steps=accepted, state=state, score=0.0),
            parent_node=None,
            isa_repl=isa_repl,
        )
        self.state2score[state] = 0.0
        return True

    TreeSearchProver.start_state = start_state_with_verified_prefix

    # Keep IsaREPL runtime logs inside this generation, not the official checkout.
    os.chdir(audit_dir)

    generator = None
    model_server_context: Any = ExternalModelServer(args.external_model_url)
    if not args.external_model_url:
        generator = CodexRelayStepGenerator(
            audit_dir=audit_dir,
            relay_url=args.relay_url,
            relay_token=token,
            repo=args.relay_repo,
            model=args.model,
            width=args.width,
            timeout=args.llm_timeout,
        )
        model_server_context = StepwiseModelServer(generator)
    started = time.time()
    proof: list[str] = []
    error = ""
    with model_server_context as model_server:
        config = TreeSearchProverConfig(
            max_attempts=args.max_attempts,
            selected_states_num=args.selected_states,
            selected_hammer_num=args.hammer_leaves,
            width=args.width,
            max_depth=args.max_depth,
            use_crafted_steps=args.crafted_steps,
            use_quickcheck=args.quickcheck,
            llm_address=model_server.address,
            log_dir=str(audit_dir / "stepwise-logs"),
        )
        prover = TreeSearchProver(config)
        lemma = PureLemma(
            name=args.lemma_name,
            statement=args.statement,
            theory=args.theory_name or theory_file.stem,
            session=args.session,
            path=theory_rel,
            base_dir=session_root,
        )
        try:
            proof = prover.prove(
                lemma,
                args.isa_port,
                session_root,
                exclude_list=[str(theory_file)],
                repl_envs={"L4V_ARCH": os.environ.get("L4V_ARCH", "ARM"), "JAVA_HOME": os.environ.get("JAVA_HOME", "")},
            )
        except Exception as exc:
            error = repr(exc)

    if not proof and not error:
        tree_log = audit_dir / "stepwise-logs" / "tree_search" / "TreeSearchProver.log"
        if tree_log.is_file():
            tree_text = tree_log.read_text(encoding="utf-8", errors="replace")
            error_lines = [
                line.split(" - ERROR - ", 1)[1]
                for line in tree_text.splitlines()
                if " - ERROR - " in line
            ]
            if error_lines:
                error = error_lines[0]
            elif tree_text:
                error = (
                    "proof search exhausted; tree-search residual follows:\n"
                    + tree_text[-12000:]
                )
        if not error:
            error = "proof search exhausted without an Isabelle-accepted proof"

    result = {
        "status": "GREEN" if proof else "RED",
        "proof": proof,
        "error": error,
        "wall_ms": int((time.time() - started) * 1000),
        "llm_calls": None if generator is None else generator.sequence,
        "model_tool_audit": (
            "audited-external-no-tool"
            if generator is None else "unavailable-from-current-relay-api"
        ),
        "sealable_generation": generator is None,
        "warm_start": (
            json.loads((audit_dir / "warm-start-replay.json").read_text(encoding="utf-8"))
            if (audit_dir / "warm-start-replay.json").is_file() else {
                "mode": "statement-start",
                "accepted_step_count": 0,
            }
        ),
    }
    (audit_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if proof else 2


if __name__ == "__main__":
    raise SystemExit(main())
