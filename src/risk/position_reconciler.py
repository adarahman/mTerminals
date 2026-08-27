"""
risk/position_reconciler.py
----------------------------
Diffs the LIVE order book against the LIVE position book — both pulled
fresh from AngelOne, same as risk/account_guard.py's exposure check — and
alerts on mismatch. Follows the same reconciliation shape as
ml/inference.py's VirtualOIEstimator.on_nse_refresh(): two independently
derived views of the same quantity are compared, drift is always logged,
and only a large enough drift escalates further.

Unlike VirtualOI's reconciliation (one side is our own ML accumulator vs
AngelOne's confirmed OI), BOTH sides here come from AngelOne itself. A
mismatch therefore doesn't mean "our math is wrong" — it means the two
AngelOne endpoints disagree, which can legitimately happen for reasons
that are NOT bugs:
  - A just-filled order hasn't propagated to the position book yet
    (see LIVE_RECONCILE_MIN_ORDER_AGE_SECONDS below).
  - A position was opened/closed manually via the AngelOne app/website,
    outside this dashboard entirely.
  - A partial fill, where filledshares != quantity.
This module cannot distinguish "stale propagation" from "something is
actually wrong" — it can only flag disagreement for a human to look at.
That's why every mismatch is logged unconditionally (cheap, low-severity
signal), but only a mismatch large enough to clear
LIVE_RECONCILE_TRIP_LOTS trips the shared kill switch (expensive,
account-halting signal) — same two-tier posture VirtualOI uses (log at
30% drift, but never itself halts anything) and account_guard uses (only
loss/exposure/drawdown breaches actually trip).

An algo running unattended can't rely on a human noticing a dead session
or a silently-diverged position book — this is the "somebody is still
watching" mechanism for the latter.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Lot-level mismatch that escalates from "logged" to "trips the shared
# live-trading kill switch". Same file account_guard.py already uses
# (LIVE_TRADING_KILL_SWITCH_FILE) — one kill switch, not two competing
# mechanisms, same reasoning as that module's docstring.
LIVE_RECONCILE_TRIP_LOTS = int(os.environ.get("LIVE_RECONCILE_TRIP_LOTS", "2"))

# Orders younger than this are excluded from the order-book side of the
# diff entirely — a fill that landed 3 seconds ago may not have
# propagated to the position book yet, and that lag is not a real
# mismatch. Value is deliberately generous relative to AngelOne's typical
# propagation delay; tighten only after confirming actual delay against
# a live account.
LIVE_RECONCILE_MIN_ORDER_AGE_SECONDS = int(
    os.environ.get("LIVE_RECONCILE_MIN_ORDER_AGE_SECONDS", "60")
)


@dataclass
class Mismatch:
    symbol: str
    order_book_lots: int
    position_lots: int

    @property
    def diff_lots(self) -> int:
        return self.order_book_lots - self.position_lots


@dataclass
class ReconciliationResult:
    mismatches: list[Mismatch] = field(default_factory=list)
    unparseable_symbols: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.mismatches and not self.unparseable_symbols

    def max_abs_diff_lots(self) -> int:
        return max((abs(m.diff_lots) for m in self.mismatches), default=0)


# ── broker-response parsing helpers (kept separate from the stateful ────
# ── reconciler so they can be unit-tested against fixture payloads) ─────

def _resolve_lot_size(symbol: str, lot_size_lookup: dict[str, int]) -> int | None:
    # BUGFIX: matching in dict-iteration order let a shorter key that's a
    # prefix of a longer one (e.g. "SENSEX" vs "SENSEX50") win first and
    # return the WRONG symbol's lot size — see account_guard.py's
    # open_lots_from_positions()/projected_open_lots_from_positions() for
    # the same fix. Longest/most-specific key first avoids the collision.
    for sym_key, size in sorted(lot_size_lookup.items(), key=lambda item: len(item[0]), reverse=True):
        if symbol.startswith(sym_key):
            return size
    return None


def net_lots_by_symbol_from_orders(
    orders: list[dict],
    lot_size_lookup: dict[str, int],
    now_ts: float | None = None,
    min_age_seconds: int = LIVE_RECONCILE_MIN_ORDER_AGE_SECONDS,
) -> tuple[dict[str, int], list[str]]:
    """Sums filled quantity per symbol from the order book, signed by
    transaction type (BUY=+, SELL=-), converted to lots.

    Only orders AngelOne reports as filled are counted — tries the known
    orderstatus field-name/value variants defensively, same posture as
    account_guard.py's pnl_from_positions(). Orders newer than
    min_age_seconds are skipped (see module docstring) rather than
    treated as a confirmed fill.

    Returns (net_lots_by_symbol, unparseable_symbols) — a symbol lands in
    the second list (and is EXCLUDED from the first) if any of its order
    rows can't be parsed into a lot size, quantity, or transaction type.
    Excluding rather than failing the whole call means one bad row
    doesn't blind the reconciler to every other symbol's mismatch — same
    "monitor degrades, it doesn't go dark" posture as the rest of this
    module.
    """
    now = now_ts if now_ts is not None else time.time()
    net_qty: dict[str, int] = {}
    bad_symbols: set[str] = set()

    for o in orders or []:
        status = str(o.get("orderstatus") or o.get("status") or "").strip().lower()
        if status not in ("complete", "completed", "filled"):
            continue

        symbol = (o.get("tradingsymbol") or "").upper()
        if not symbol:
            continue

        # Skip fills too recent to have propagated to the position book —
        # NOT an error, so not added to bad_symbols.
        order_ts = None
        for ts_key in ("updatetime", "exchtime", "filltime"):
            raw = o.get(ts_key)
            if raw:
                order_ts = _parse_broker_timestamp(raw)
                if order_ts is not None:
                    break
        if order_ts is not None and (now - order_ts) < min_age_seconds:
            continue

        qty = None
        for key in ("filledshares", "quantity", "filledqty"):
            if key in o and o[key] not in (None, ""):
                try:
                    qty = int(float(o[key]))
                    break
                except (TypeError, ValueError):
                    continue
        transaction_type = str(o.get("transactiontype") or "").strip().upper()

        if qty is None or transaction_type not in ("BUY", "SELL"):
            bad_symbols.add(symbol)
            continue

        lot_size = _resolve_lot_size(symbol, lot_size_lookup)
        if not lot_size:
            bad_symbols.add(symbol)
            continue

        signed_lots = (qty // lot_size) * (1 if transaction_type == "BUY" else -1)
        net_qty[symbol] = net_qty.get(symbol, 0) + signed_lots

    for bad in bad_symbols:
        net_qty.pop(bad, None)

    return net_qty, sorted(bad_symbols)


def net_lots_by_symbol_from_positions(
    positions: list[dict],
    lot_size_lookup: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """Same shape as net_lots_by_symbol_from_orders but for the position
    book — reuses the same defensive field-name fallbacks
    account_guard.py's open_lots_from_positions() already established for
    this exact payload (netqty/quantity/netQty, symbolname/tradingsymbol),
    kept in sync with that function's parsing choices deliberately."""
    net_qty: dict[str, int] = {}
    bad_symbols: set[str] = set()

    for p in positions or []:
        qty = None
        for key in ("netqty", "quantity", "netQty"):
            if key in p and p[key] not in (None, ""):
                try:
                    qty = int(float(p[key]))
                    break
                except (TypeError, ValueError):
                    continue

        symbol = (p.get("symbolname") or p.get("tradingsymbol") or "").upper()

        if qty is None or not symbol:
            if symbol:
                bad_symbols.add(symbol)
            continue

        lot_size = _resolve_lot_size(symbol, lot_size_lookup)
        if not lot_size:
            bad_symbols.add(symbol)
            continue

        net_qty[symbol] = net_qty.get(symbol, 0) + qty // lot_size

    for bad in bad_symbols:
        net_qty.pop(bad, None)

    return net_qty, sorted(bad_symbols)


def _parse_broker_timestamp(raw) -> float | None:
    """AngelOne timestamps are seen as either epoch seconds/millis or a
    'DD-MMM-YYYY HH:MM:SS' string depending on endpoint/SDK version —
    tries numeric first, falls back to the known string format. Returns
    None (not 0.0) on failure so the caller treats the order's age as
    unknown rather than infinitely old."""
    if raw in (None, ""):
        return None
    try:
        val = float(raw)
        return val / 1000.0 if val > 1e12 else val
    except (TypeError, ValueError):
        pass
    try:
        return time.mktime(time.strptime(str(raw), "%d-%b-%Y %H:%M:%S"))
    except ValueError:
        return None


def reconcile(
    orders: list[dict],
    positions: list[dict],
    lot_size_lookup: dict[str, int],
    now_ts: float | None = None,
    min_age_seconds: int = LIVE_RECONCILE_MIN_ORDER_AGE_SECONDS,
) -> ReconciliationResult:
    """Pure function — no I/O, no logging, no state. Call
    PositionReconciler.check() to actually log/trip on the result; kept
    separate so the diff logic is unit-testable against fixture payloads
    alone, same split account_guard.py uses between its parsing helpers
    and LiveAccountRiskGuard itself."""
    order_lots, bad_order_symbols = net_lots_by_symbol_from_orders(
        orders, lot_size_lookup, now_ts=now_ts, min_age_seconds=min_age_seconds
    )
    position_lots, bad_position_symbols = net_lots_by_symbol_from_positions(
        positions, lot_size_lookup
    )

    all_symbols = set(order_lots) | set(position_lots)
    mismatches = [
        Mismatch(symbol=sym, order_book_lots=order_lots.get(sym, 0),
                  position_lots=position_lots.get(sym, 0))
        for sym in sorted(all_symbols)
        if order_lots.get(sym, 0) != position_lots.get(sym, 0)
    ]
    unparseable = sorted(set(bad_order_symbols) | set(bad_position_symbols))

    return ReconciliationResult(mismatches=mismatches, unparseable_symbols=unparseable)


class PositionReconciler:
    """One instance, created once at server/app.py startup alongside
    _ACCOUNT_GUARD. Stateless across calls (no SQLite, unlike
    LiveAccountRiskGuard) — every mismatch is independently re-derived
    from the broker's current order/position book each call, so there's
    nothing to persist or roll over per trading day. Trip state IS
    shared, though: it goes through the same kill-switch file
    account_guard.py uses, so a trip here behaves identically to a
    trip there from every other module's point of view."""

    def __init__(self, kill_switch_path: str,
                 trip_lots: int = LIVE_RECONCILE_TRIP_LOTS):
        self.kill_switch_path = kill_switch_path
        self.trip_lots = trip_lots

    def check(
        self,
        orders: list[dict],
        positions: list[dict],
        lot_size_lookup: dict[str, int],
        now_ts: float | None = None,
    ) -> ReconciliationResult:
        result = reconcile(orders, positions, lot_size_lookup, now_ts=now_ts)

        for m in result.mismatches:
            logger.info(
                f"[position_reconciler] {m.symbol} | order book: {m.order_book_lots:+d} lots "
                f"| positions: {m.position_lots:+d} lots | diff: {m.diff_lots:+d}"
            )

        if result.unparseable_symbols:
            logger.warning(
                f"[position_reconciler] could not parse order/position rows for: "
                f"{', '.join(result.unparseable_symbols)} — excluded from this check, "
                f"not flagged as a mismatch"
            )

        if result.max_abs_diff_lots() >= self.trip_lots:
            worst = max(result.mismatches, key=lambda m: abs(m.diff_lots))
            self._trip_kill_switch(
                f"position reconciliation mismatch: {worst.symbol} off by "
                f"{worst.diff_lots:+d} lots (order book {worst.order_book_lots:+d} "
                f"vs positions {worst.position_lots:+d}, threshold {self.trip_lots})"
            )

        return result

    def _trip_kill_switch(self, reason: str):
        try:
            with open(self.kill_switch_path, "w") as f:
                f.write(f"auto-tripped by position_reconciler: {reason}\n")
            logger.error(f"[position_reconciler] LIVE TRADING KILL SWITCH TRIPPED — {reason}")
            print(f"[position_reconciler] LIVE TRADING KILL SWITCH TRIPPED — {reason}", flush=True)
        except OSError as e:
            # Same worst-case failure mode as account_guard._trip_kill_switch —
            # logged loudly rather than swallowed.
            logger.critical(
                f"[position_reconciler] FAILED TO WRITE KILL SWITCH FILE after trip "
                f"({reason}) — live trading is NOT actually disabled: {e}"
            )