#!/usr/bin/env python3
"""spec_delivery_lifecycle.py — run the delivery state machine over the ledger.

execute-additive-design.md §2.3 / §3.4 / §6.2 define a lifecycle for
`named`/`block` candidates that the *driver* cannot run inside a single
strengthen.sh invocation, because it depends on what lands LATER:

    substate=realized  -> delivery_state=realized   (permanent)
    substate=planned   -> delivery_state=pending
                          -> realized  (a consumer/downstream landed)
                          -> orphan    (grace period elapsed, still no consumer)

This is the periodic sweep that actually moves `pending` candidates. For each
candidate whose latest delivery_state is `pending`, it:

  1. REALIZE  — locate L's theory (from the key `<slot>:<thy>:<lemma>`), grep
                for a consumer that references the lemma by name. If found,
                append a `delivery_realized` event (state -> realized). For a
                `block` candidate, "consumer" = any named downstream candidate
                key now in a landed (applied/trial_passed) state.
  2. ORPHAN   — else if the entry is older than its grace_period_weeks, append
                a `delivery_orphan` event (state -> orphan) for GC review (the
                lemma is NOT deleted — §6.2 says GC aggregates, never deletes).
  3. else      leave pending (still within grace).

Run periodically (e.g. weekly) or on demand:

  python3 spec-strengthen/scripts/spec_delivery_lifecycle.py            # report only
  python3 spec-strengthen/scripts/spec_delivery_lifecycle.py --apply    # append events
  python3 ... --orphan-after-weeks 26   # override grace for the orphan threshold
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = REPO_ROOT / "spec-strengthen/candidates/candidate-ledger.jsonl"
LANDED = {"applied", "trial_passed"}
PENDING_STATES = {"pending"}


def load_events(ledger):
    rows = []
    for line in Path(ledger).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def latest_by_key(rows):
    latest = {}
    for r in rows:
        k = r.get("key")
        if k:
            latest[k] = r
    return latest


def parse_ts(ts):
    if not ts:
        return None
    try:
        return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def locate_theory(theory_base):
    for root in ("verification/l4v/proof", "verification/l4v/spec"):
        hits = list((REPO_ROOT / root).rglob(f"{theory_base}.thy"))
        if hits:
            return hits[0]
    return None


def consumer_in_source(theory_base, lemma_name):
    thy = locate_theory(theory_base)
    if not thy:
        return None  # cannot tell
    text = thy.read_text(encoding="utf-8", errors="replace")
    name = re.escape(lemma_name)
    # remove the lemma's own declaration, then look for any remaining use
    text = re.sub(rf"^\s*lemmas?\s+{name}\b.*$", "", text, flags=re.MULTILINE)
    return re.search(rf"\b{name}\b", text) is not None


def block_downstream_realizes(entry, block_lemma, latest):
    """A block is realized only when a named downstream candidate BOTH landed
    AND its source actually references the block lemma. Merely landing is not
    enough — the downstream may have bypassed the helper entirely, which is the
    exact failure a `planned` block guards against (design §2.4)."""
    tgt = entry.get("delivery_target")
    if isinstance(tgt, str):
        tgt = [t for t in re.split(r"[,\s]+", tgt.strip()) if t]
    elif not isinstance(tgt, list):
        tgt = []
    for k in tgt:
        ev = (latest.get(k) or {}).get("event")
        if ev not in LANDED:
            continue
        ds_parts = k.split(":")
        if len(ds_parts) < 3:
            continue
        ds_thy = ds_parts[1]
        # does the landed downstream's source actually cite the block lemma?
        if consumer_in_source(ds_thy, block_lemma):
            return True, k
    return False, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--apply", action="store_true",
                    help="append delivery_realized / delivery_orphan events")
    ap.add_argument("--orphan-after-weeks", type=int, default=None,
                    help="override the per-entry grace_period_weeks threshold")
    ap.add_argument("--now", default=None,
                    help="ISO8601 'now' override (testing)")
    args = ap.parse_args()

    rows = load_events(args.ledger)
    latest = latest_by_key(rows)
    now = parse_ts(args.now) if args.now else dt.datetime.now(dt.timezone.utc)

    realized, orphaned, still_pending, untracked = [], [], [], []
    new_events = []
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for key, entry in latest.items():
        state = entry.get("delivery_state")
        if state not in PENDING_STATES:
            continue
        parts = key.split(":")
        if len(parts) < 3:
            untracked.append((key, "unparseable key"))
            continue
        _slot, thy_base, lemma = parts[0], parts[1], parts[2]
        delivery = entry.get("delivery")

        # 1. REALIZE
        if delivery == "block":
            landed, via = block_downstream_realizes(entry, lemma, latest)
            note = f"downstream {via} landed AND references this block" if via else ""
        else:
            landed = consumer_in_source(thy_base, lemma)
            note = "a consumer now references this lemma in source"
        if landed:
            realized.append(key)
            new_events.append({"ts": ts, "key": key, "event": "delivery_realized",
                               "delivery_state": "realized", "note": note})
            continue

        # 2. ORPHAN (grace elapsed)
        grace = args.orphan_after_weeks or entry.get("grace_period_weeks") or 8
        age = parse_ts(entry.get("ts"))
        overdue = age is not None and (now - age) > dt.timedelta(weeks=grace)
        if overdue:
            orphaned.append((key, grace))
            new_events.append({"ts": ts, "key": key, "event": "delivery_orphan",
                               "delivery_state": "orphan",
                               "note": f"no consumer after {grace}w grace; GC review"})
        else:
            wks = (now - age).days / 7 if age else None
            still_pending.append((key, grace, wks))

    # ---- report ----
    print(f"delivery lifecycle sweep @ {ts}")
    print(f"  pending entries examined: "
          f"{len(realized)+len(orphaned)+len(still_pending)+len(untracked)}")
    print(f"  -> realized now : {len(realized)}")
    for k in realized:
        print(f"       ✓ {k}")
    print(f"  -> orphaned     : {len(orphaned)}")
    for k, g in orphaned:
        print(f"       ⚠ {k}  (grace {g}w elapsed)")
    print(f"  -> still pending: {len(still_pending)}")
    for k, g, wks in still_pending:
        agestr = f"{wks:.1f}w/{g}w" if wks is not None else f"?/{g}w"
        print(f"       · {k}  ({agestr})")
    if untracked:
        print(f"  -> untracked    : {len(untracked)}")
        for k, why in untracked:
            print(f"       ? {k}  ({why})")

    if args.apply and new_events:
        with open(args.ledger, "a", encoding="utf-8") as f:
            for ev in new_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        print(f"\nappended {len(new_events)} lifecycle event(s) to {args.ledger}")
    elif new_events:
        print(f"\n(dry-run) would append {len(new_events)} event(s); pass --apply")


if __name__ == "__main__":
    main()
