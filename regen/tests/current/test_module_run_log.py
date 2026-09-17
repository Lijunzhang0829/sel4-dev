#!/usr/bin/env python3
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from regen.infrastructure.audited_llm import call_codex
from regen.infrastructure.llm_transcript import (
    allocate_artifact_prefix, artifact_paths, reconcile_transcript_family,
    transcript_health,
)
from regen.infrastructure.module_run_log import attach_module_outputs, write_llm_module_log


class ModuleRunLogTests(unittest.TestCase):
    def make_transcript(self, root: Path, name: str = "call") -> Path:
        prefix = root / name
        payloads = {
            "prompt": "large prompt body",
            "raw": "{\"type\":\"turn.completed\"}\n",
            "final": "{\"proof_body\":\"by simp\"}",
            "stderr": "",
            "audit": json.dumps({"no_tool_events": True}),
        }
        paths = artifact_paths(prefix)
        for key, text in payloads.items():
            paths[key].parent.mkdir(parents=True, exist_ok=True)
            paths[key].write_text(text)
        paths["meta"].write_text(json.dumps({
            "returncode": 0, "timed_out": False,
            "sealable_generation": True,
        }))
        return prefix

    def test_module_log_is_an_index_not_a_payload_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = self.make_transcript(root)
            output = root / "result.json"
            output.write_text(json.dumps({
                "status": "green", "node_id": "node:x",
                "proof_body": "very large proof body",
            }))
            log = root / "module.json"
            write_llm_module_log(
                prefix, log, module="Lemma Prover", run_name="x",
            )
            attach_module_outputs(log, {"proof_result": output})
            value = json.loads(log.read_text())
            self.assertEqual(value["schema_version"], 2)
            attempt = value["attempts"][0]
            self.assertNotIn("prompt", attempt)
            self.assertNotIn("raw_provider_stream", attempt)
            self.assertNotIn("module_outputs", attempt)
            self.assertEqual(
                attempt["output_artifacts"]["proof_result"]["summary"]["status"],
                "green",
            )
            self.assertEqual(
                attempt["transcript_artifacts"]["prompt"]["sha256"],
                hashlib.sha256(b"large prompt body").hexdigest(),
            )

    def test_allocator_never_reuses_existing_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferred = self.make_transcript(root)
            allocated = allocate_artifact_prefix(preferred)
            self.assertEqual(allocated.name, "call--call-001")
            self.assertFalse(any(
                path.exists() for path in artifact_paths(allocated).values()
            ))

    def test_low_level_codex_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "call"
            artifact_paths(prefix)["prompt"].write_text("old")
            with self.assertRaises(FileExistsError):
                call_codex("new", prefix)

    def test_interrupted_call_is_recoverable_and_superseded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferred = self.make_transcript(root)
            meta = artifact_paths(preferred)["meta"]
            value = json.loads(meta.read_text())
            value.update({"state": "running", "returncode": None})
            meta.write_text(json.dumps(value))
            self.assertEqual(
                transcript_health(preferred, orphaned=True)["state"],
                "interrupted",
            )
            replacement = allocate_artifact_prefix(preferred)
            self.make_transcript(root, replacement.name)
            history = reconcile_transcript_family(preferred)
            self.assertEqual(
                [item["state"] for item in history],
                ["superseded", "complete"],
            )

    def test_hash_damage_is_classified_not_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = self.make_transcript(root)
            paths = artifact_paths(prefix)
            meta = json.loads(paths["meta"].read_text())
            meta["prompt_sha256"] = "0" * 64
            paths["meta"].write_text(json.dumps(meta))
            health = transcript_health(prefix)
            self.assertEqual(health["state"], "corrupt")
            self.assertIn("prompt_sha256", health["hash_mismatches"])


if __name__ == "__main__":
    unittest.main()
