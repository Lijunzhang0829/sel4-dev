# Local Codex client update and dual-Planner continuation

The user explicitly requested using the latest locally installed Codex plugin
client after an IDE extension update removed the old executable directory.
This is a client runtime update, not a model or proof-policy change.

## Binding

- Old extension: `openai.chatgpt-26.901.22334-linux-x64`; client `0.153.0`.
- Installed extension: `openai.chatgpt-26.917.62051-linux-x64`;
  client `0.155.0-alpha.16.3`.
- New executable SHA-256:
  `a969c7c42b842c759d157d2500bbf33a562ff95f22dcb7112aa4d8f9e68833d3`.
- Changed only `codex_bin`, `codex_sha256`, and `codex_version` in
  `regen/config/proof-models.json`. Role models remain `gpt-6-astra`, reasoning
  remains `medium`; budgets, fixed contracts and concurrency remain unchanged.
- No download, plugin reinstall, global configuration change, or auth change.

## Compatibility checks

The [official App Server documentation](https://learn.chatgpt.com/docs/app-server)
and locally installed CLI were used to check the protocol entry point. A real
client process passed the harness launcher flags, initialize/initialized, and
thread/start with dynamic tool registration. This probe started zero model turns
and did not supply any target-specific proof guidance.

Evidence:
`reports/regen/client-update-20260927/handshake-3bzbae91/result.json`.
An earlier probe omitted the harness's `_timeout` initialization and failed in
the probe itself; it was closed, retained, and rerun with correct initialization.

Focused transport, continuation, document recovery, and parallel Planner tests:
38 passed, no failures, 3.591 seconds. Mock protocol tests are not a substitute
for the real handshake above or for successful repository proof completion.
Full default current regression suite: 846 tests, zero failures, four skipped,
41.820 seconds (842 executed successfully). Opt-in environment tests were not
enabled for this run; the real new-client handshake was run separately.

## Recovery

Run `repository-theory-document-astra-20260927-007` remains preserved as a
failed startup with no new model requests. New continuation `...20260927-008`
uses the verified `...20260926-006` directly to avoid adding a redundant history
layer. The authorized harness continuation verifies that the only changed frozen
implementation file is the client configuration. It preserves all accepted
proofs, seals, graph state, charged requests, budgets and semantic environment.

Operational preparation:
`.agent-staging/recover_expanded_disk_20260927.py --client-update --destination
reports/regen/refine-first-run-001/repository-theory-document-astra-20260927-008`.
The new run's `recovery-verification.json` and service receipts are authoritative
for completed preparation and actual launch; naming the destination is not
evidence of a running service or sealed repository.

Preparation completed at `2026-09-27T07:44:15Z`. Exact inherited state remains
63 accepted contract proofs, 76 helper proofs, two sealed theories, and request
counts 9/115/77/25. Only `regen/config/proof-models.json` differs from the old
frozen implementation; model roles and all semantic/budget fields match.

The new service launched at `2026-09-27T07:44:29Z`:
`regen-proof-77bfc47bc52a1c633cf78741.service`, PID 29118, invocation
`03a5fedb26304e1089380340c5b1e1f2`. It passed the executable-hash preflight and
persisted the original two-Planner/two-native-slot policy. Startup liveness is
not proof completion; live model/tool observations must be checked separately.

At `2026-09-27T07:49:49Z`, the same service identity remained active. Both
Thread_Object_State and Translation_Entries had acknowledged initialize,
thread/start and turn/start, and each issued an actual `read_theory` dynamic
tool callback. Both recorded the new executable SHA-256 and `gpt-6-astra` model.
Evidence is persisted in run 008's `client-update-live-validation.json`.
This establishes two real live Planner sessions on the new client; neither a
new seal nor repository completion is claimed by this startup observation.
