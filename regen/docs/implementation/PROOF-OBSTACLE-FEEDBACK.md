# Proof obstacle feedback

The reviewed document workflow records a linked failure history for each entry. This is advisory evidence navigation, not proof acceptance, a difficulty score, or a retry limit.

Each new failure binds its entry, imports, accepted prefix hash, preceding unaccepted candidate hash, and checking mode (formal, conditional sketch, or probe). Exact diagnostic comparisons normalize generated source locations only. Different variables, local assumptions, and goal order remain different observations. Missing legacy bindings, timeouts, scoped infrastructure failures, and incomplete states yield `unknown`.

`check_theory` feedback exposes `proof_obstacles` immediately. `read_theory.workflow.pending_failures` includes a compact summary. `read_failure` returns the saved proof, complete diagnostic and an eight-record linked history, including prior repair plans and proof-body edit ranges. Older records remain addressable by ID; a bounded window is not a lifetime failure count.

- `same_observation`: matching rendered feedback under the same bound context, even if the candidate text changed.
- `changed_observation`: rendered feedback changed; this does not certify mathematical progress.
- `unknown`: no sound comparison is available.

No record grants GREEN, reuses an uncommitted tactic prefix, changes failure caching, or prevents continuing a hard proof. Successful checks clear pending failures but retain archived receipts. Historical failures without predecessor links are explicitly marked incomplete; chronology is never reconstructed from modification times.

The generation instructions ask the model to explain which failing command or obligation an edit addresses, and to distinguish obligations removed by a sketch from its remaining hard mechanism. This is guidance, not an enforced semantic judgment that an edit implements a diagnosis.

Validation covers context changes, changed unaccepted predecessors, probe/formal isolation, incomplete diagnostics, corruption, cross-target links, exact edit spans, bounded history, retained repair plans, GREEN cleanup, and tool feedback delivery. Controlled generation experiments must use independent historical forks with identical checkpoints/model/budget and must not import later successful proofs.
