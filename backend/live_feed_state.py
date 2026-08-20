"""Provider-neutral mutation of dashboard snapshots from live tick deltas."""
from __future__ import annotations


def _merge_rows(chain_rows, changed_rows) -> None:
    if not chain_rows:
        return
    by_strike = {row.get("strike"): row for row in chain_rows if isinstance(row, dict)}
    for changed in changed_rows:
        target = by_strike.get(changed.get("strike"))
        if target is not None:
            target.update({key: value for key, value in changed.items() if key != "strike"})


def merge_live_feed_update(message, current_snapshot, last_sent_snapshot, matches_expiry):
    """Merge a normalized tick into snapshots and return its safe wire form.

    Chain deltas are discarded from the outbound message when their expiry
    does not match the currently displayed chain; spot updates are expiry-free.
    The supplied snapshots are mutated in place to retain full/delta parity.
    """
    applied = False
    payload = message.get("payload") if isinstance(message, dict) else None
    chain_delta = (payload or {}).get("chain") if isinstance(payload, dict) else None
    if isinstance(chain_delta, dict) and chain_delta.get("_keyed"):
        changed_rows = chain_delta.get("changed") or []
        current_expiry = (current_snapshot or {}).get("expiry")
        if changed_rows and matches_expiry(current_expiry):
            applied = True
            for snapshot in (current_snapshot, last_sent_snapshot):
                if isinstance(snapshot, dict):
                    _merge_rows(snapshot.get("chain"), changed_rows)
        elif changed_rows:
            payload = {key: value for key, value in payload.items() if key != "chain"}
            message = {**message, "payload": payload}

    if isinstance(payload, dict) and "spot" in payload:
        applied = True
        for snapshot in (current_snapshot, last_sent_snapshot):
            if not isinstance(snapshot, dict):
                continue
            snapshot["spot"] = payload["spot"]
            for key in ("spotChange", "spotChgPct"):
                if key in payload:
                    snapshot[key] = payload[key]
            if "futLtp" in payload and "futVwap" in payload:
                snapshot["spotVwap"] = payload["futVwap"] - (
                    payload["futLtp"] - payload["spot"]
                )
                if "futVolume" in payload:
                    snapshot["spotVolume"] = payload["futVolume"]
    return message, applied
