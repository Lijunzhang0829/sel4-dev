# P0 interaction measurement and result confirmation diagnosis

P0 adds a read-only audit and a diagnostic event. It does not change the model
prompt, tool schemas, wait duration, native checks, proof acceptance, budget
accounting, publication policy, or experiment runtime arguments. P1–P4 remain
separate planned work. Current experiments keep their frozen application.

## Measurement

Run the offline tool against app-server transcript files:

```sh
python3 -m regen.tools.audit_interaction_cost path/to/session-transcript.jsonl --output /tmp/interaction-audit.json
```

Multiple positional inputs are supported. The output option belongs only to the
offline analysis command; no new proof-generation parameter is required.

Each report records the input snapshot hash/size, tool and action counts, unique
cumulative usage updates, duplicate updates, payload sizes by tool/top-level
field, identical wait replies, and call/thread/turn identities. Call records
include timestamps, source bindings, entry IDs, requested/returned task IDs and
response hashes without repeating proof bodies. These are navigation and
measurement records, not proof authority or GREEN certificates.

Token counters are summed once per thread from the last cumulative value.
Duplicate updates do not add usage. Nonmonotonic counters exclude that thread
from token totals and explicitly report the problem. Cached input is already
included in input; reasoning output is already included in output. Active or
interrupted sessions can have unreported tails. A partial final JSONL write is
reported; a corrupt complete line is an error.

Waiting attribution counts usage strictly after the first five seconds and
before the last second of a completed wait in the same thread. It is a temporal
association, not proof of wasted model work. Repeated waiting compares exact
responses for the same task IDs within a thread. Payload sizes use canonical
JSON bytes and text characters, not token estimates or transport byte counts.

Existing official-baseline telemetry exports containing a `runs` mapping can
also be read. Their reported usage, completeness, response counts and tool
operation counts are preserved. This adapter is not an independent replay of
the baseline raw logs: payload sizes and unchanged-wait counts remain unknown.
Wrapper and nested tools must not be added to estimate model rounds. Baseline
response records and app-server usage updates are approximate comparison units.

## Confirmed binomial failure mechanism

The real Pop_Transaction transcript contains a `wait_checks` request whose
server notification reports `status=failed`, `success=false`, and no content
after about 18 seconds. The local callback later returns `success=true` with a
running-task result, about 60 seconds after the request. The failed notification
arrives about 42.79 seconds before the response is sent.

Strict comparison consequently raises SessionProtocolError. This is an early
failed server item, not JSON whitespace/key ordering and not an Isabelle
counterexample. Available records do not explain why the server ended that
item early. Do not infer a 60-second server deadline from this case.

The offline audit labels this timeline `failed_before_response_sent`. Runtime
confirmation rejection now also emits `tool_result_confirmation_rejected`, with
call identity, sent-response hash, status fields and a diagnostic classification.
An empty failed item is labeled `server_failed_empty_receipt`; chronology is
established by the offline timeline. The original strict comparison, exception,
cleanup, and no-callback-replay behavior remain intact. Neither this event nor a
matching delivery receipt establishes proof validity or model understanding.

## Validation and policy preservation

- The pre-P0 functionality was committed as `4daf7be7`; 120 targeted tests passed.
- P0 and associated protocol/cancellation/transcript/publication tests: 112 passed.
- A real stdio fixture reproduces an early empty failure, verifies that the
  callback executes only once, emits the diagnostic, and does not confirm delivery.
- Coverage includes matching early notifications, missing receipts, legitimate
  error responses, changed payload/success flags, duplicate usage, decreasing
  counters, partial writes, UTF-8 size differences, and incomplete baseline data.
- Real logs were replayed from binomial Pop_Transaction/Tree_Linking and the
  current Refine sessions; the actual Pop_Transaction failure was identified.
- The active Refine release manifest was verified. Its run still binds
  `complete_context_policy=arch-reuse-v1` and
  `audit_policy=declaration-export-deferred-v2`. No running artifact was edited.

Evidence and the staged plan are in
[the interaction plan directory](../../../reports/regen/interaction-overhead-plan-20261116-001/PLAN.md),
including `P0-AUDIT.json` and `P0-POLICY-CHECK.json`. Audit snapshots of active
logs have their own hashes and are not interchangeable with earlier counts.

Remaining work before broader behavior changes: independently replay baseline
raw output payloads, determine the server-side cause of the early failed item,
and validate any recovery policy separately. P0 does not claim that the protocol
failure has been repaired or that repeated polling has been eliminated.
