# spec-strengthen survey — AInvs / Finalise_AI.thy — 2026-06-08

Source: `verification/l4v/proof/invariant-abstract/Finalise_AI.thy`

## Pattern C — unused premise

| Key | lemma / premise | ROI | prior |
|---|---|---|---|

_C candidates are not probed during survey (each probe is ~40s); the probe is the first step of `execute`._

## Pattern D — loose bound `≤` → `=`

**No automated detector.** D requires domain knowledge to identify a `≤`-bound
postcondition that's actually `=`. To execute a D candidate:
```
  spec_strengthen_run.sh execute --pattern D --patch <patch> \
    --theory <thy> --expid <expid> [--key <key>] [-y]
```

The shell does not judge D candidate quality; it only runs the standard
baseline/trial/apply/audit pipeline once you supply a patch.
