"""
risk/account_guard.py
----------------------
Account-level risk guard for LIVE trading (paper trading is unaffected).

What existed before this module: server/app.py's `_handle_place_order`
already rejects a single live order for a bad lot size, an over-cap
quantity, or too many orders/minute — all *per-order* checks. Nothing
tracked risk *across* orders over the course of a trading day. A human (or
a future automated strategy — see PROJECT-ARCHITECTURE.md's algo-readiness
notes) could stay within every per-order cap and still lose far more than
intended by simply placing many small compliant orders.

This module adds three account-level guards, all evaluated against the
CURRENT TRADING DAY and persisted to SQLite (survives a server restart —
same reasoning paper_trading.py already uses CACHE_DIR/SQLite for, since
nothing else in the live-tick pipeline is stateful across restarts):

  1. Daily max loss  — LIVE_MAX_DAILY_LOSS_RUPEES. Trips the moment the
     account's total daily P&L (from AngelOne's own position book — see
     `pnl_from_positions()`) breaches -<limit>.
  2. Max open exposure — LIVE_MAX_OPEN_LOTS. Rejects a new order if
     (current open lots across all live positions) + (this order's lots)
     would exceed the cap. Checked BEFORE submission, not after.
  3. Drawdown-streak breaker — LIVE_MAX_CONSECUTIVE_DRAWDOWNS. If the
     account's total daily P&L gets WORSE than its own running peak N
     times in a row (each check is one broker position-book refresh,
     i.e. roughly one per live order), trips early — before the full
     daily-loss limit is hit — on the theory that a losing streak is a
     signal something's wrong with the strategy/market regime, not just
     "still within budget."

A trip does two things: (a) marks this trading day tripped in the guard's
own SQLite state, and (b) touches the SAME kill-switch file
server/app.py already checks (LIVE_TRADING_KILL_SWITCH_FILE) — so
there is exactly ONE kill switch, not two competing mechanisms. Once
tripped, the file stays until a human removes it; a new trading day does
NOT auto-clear it. That's deliberate — an auto-tripped guard should
require a human to look at what happened before trading resumes, not
silently reset at 9:15 the next morning.

NOTE on P&L/exposure field names: AngelOne SmartAPI's `position` response
field names (`pnl`, `netqty`, etc.) are read defensively here with
fallbacks across the a few known naming variants seen in SmartAPI
responses, but have NOT been verified against a live account by this
change. Same posture as lot_sizes.py's refuse-to-guess policy: if the
position book can't be parsed into numbers this module trusts, treat it
as "unknown" and fail closed (block new orders) rather than silently
allowing an unmeasured risk through — see `pnl_from_positions()` and
`open_lots_from_positions()` docstrings.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

from infrastructure.paths import CACHE_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(CACHE_DIR, "live_risk_guard.db")

LIVE_MAX_DAILY_LOSS_RUPEES = float(os.environ.get("LIVE_MAX_DAILY_LOSS_RUPEES", "5000"))
LIVE_MAX_OPEN_LOTS = int(os.environ.get("LIVE_MAX_OPEN_LOTS", "5"))
LIVE_MAX_CONSECUTIVE_DRAWDOWNS = int(os.environ.get("LIVE_MAX_CONSECUTIVE_DRAWDOWNS", "3"))

_lock = threading.Lock()


def _today_str() -> str:
    # Trading-day granularity, not calendar-precise IST cutover — matches
    # nse_eod_fetch.py's existing trading-day guards' precision level,
    # good enough for "which day's loss bucket does this belong to".
    return datetime.now().strftime("%Y-%m-%d")


@dataclass
class GuardState:
    trading_date: str
    peak_pnl: float
    last_pnl: float
    consecutive_drawdowns: int
    tripped: bool
    trip_reason: str | None


class LiveAccountRiskGuard:
    """One instance, created once at server/app.py startup, shared
    across every _handle_place_order call for the process lifetime."""

    def __init__(self, kill_switch_path: str, db_path: str = DB_PATH):
        self.kill_switch_path = kill_switch_path
        self.db_path = db_path
        self._init_schema()

    # ── persistence ──────────────────────────────────────────────────────

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_state (
                    trading_date TEXT PRIMARY KEY,
                    peak_pnl REAL NOT NULL,
                    last_pnl REAL NOT NULL,
                    consecutive_drawdowns INTEGER NOT NULL,
                    tripped INTEGER NOT NULL,
                    trip_reason TEXT
                )
            """)

    def _load(self) -> GuardState:
        today = _today_str()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT trading_date, peak_pnl, last_pnl, consecutive_drawdowns, "
                "tripped, trip_reason FROM daily_state WHERE trading_date = ?",
                (today,),
            ).fetchone()
            if row is None:
                # Fresh trading day — new row, guard starts untripped.
                # (The shared kill-switch FILE, if still present from a
                # previous day's trip, still blocks orders independently —
                # see server/app.py's own _live_trading_kill_switch_active().)
                state = GuardState(today, 0.0, 0.0, 0, False, None)
                self._save(state)
                return state
            return GuardState(
                trading_date=row[0], peak_pnl=row[1], last_pnl=row[2],
                consecutive_drawdowns=row[3], tripped=bool(row[4]), trip_reason=row[5],
            )

    def _save(self, state: GuardState):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO daily_state (trading_date, peak_pnl, last_pnl, "
                "consecutive_drawdowns, tripped, trip_reason) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trading_date) DO UPDATE SET peak_pnl=excluded.peak_pnl, "
                "last_pnl=excluded.last_pnl, "
                "consecutive_drawdowns=excluded.consecutive_drawdowns, "
                "tripped=excluded.tripped, trip_reason=excluded.trip_reason",
                (state.trading_date, state.peak_pnl, state.last_pnl,
                 state.consecutive_drawdowns, int(state.tripped), state.trip_reason),
            )

    # ── public interface ────────────────────────────────────────────────

    def is_tripped(self) -> tuple[bool, str | None]:
        """Check BEFORE allowing a live order. Also true if the shared
        kill-switch file exists for any other reason (manual touch) —
        that check already lives in server/app.py; this only reports
        THIS guard's own trip state."""
        state = self._load()
        return state.tripped, state.trip_reason

    def get_status(self) -> dict:
        """Read-only snapshot of today's guard state for status reporting
        (e.g. server/app.py's algoStatus broadcast). Reuses the same
        _load() as is_tripped()/check_new_order() — one SQLite read, no
        extra state — so this can be polled periodically without adding
        a second source of truth."""
        state = self._load()
        return {
            "tripped": state.tripped,
            "trip_reason": state.trip_reason,
            "daily_pnl": state.last_pnl,
            "peak_pnl": state.peak_pnl,
            "consecutive_drawdowns": state.consecutive_drawdowns,
            "daily_loss_limit_rupees": LIVE_MAX_DAILY_LOSS_RUPEES,
            "max_open_lots": LIVE_MAX_OPEN_LOTS,
            "max_consecutive_drawdowns": LIVE_MAX_CONSECUTIVE_DRAWDOWNS,
        }

    def check_new_order(self, qty_lots: int, current_open_lots: int) -> tuple[bool, str | None]:
        """Pre-trade exposure check. current_open_lots comes from the
        broker's own position book (see open_lots_from_positions()) —
        call that right before this, in the same order-handling pass, so
        the check reflects the account's actual current exposure rather
        than a locally-tracked approximation that could drift from truth.
        """
        with _lock:
            tripped, reason = self.is_tripped()
            if tripped:
                return False, f"account risk guard tripped: {reason}"
            if current_open_lots is None:
                # Broker position book couldn't be parsed — fail closed,
                # same posture as lot_sizes.py's refusal to guess.
                return False, "could not verify current open exposure — refusing to size a new live order blind"
            if current_open_lots + qty_lots > LIVE_MAX_OPEN_LOTS:
                return False, (f"would exceed max open exposure "
                                f"({current_open_lots}+{qty_lots} > {LIVE_MAX_OPEN_LOTS} lots)")
            return True, None

    def update_pnl(self, total_daily_pnl: float | None):
        """Call after every live order fills, with the account's CURRENT
        total daily P&L pulled fresh from the broker's position book (see
        pnl_from_positions()). Updates the drawdown-streak counter and
        checks both the daily-loss limit and the streak limit, tripping
        the shared kill switch if either breaches.

        Pass None if the position book couldn't be parsed this cycle —
        this is a no-op in that case (doesn't advance the streak counter
        off a value we don't trust), it does NOT reset it either.
        """
        if total_daily_pnl is None:
            logger.warning("[account_guard] skipping P&L update — position book unparseable this cycle")
            return

        with _lock:
            state = self._load()
            if state.tripped:
                return  # already tripped today; nothing further to do

            if total_daily_pnl < state.last_pnl:
                state.consecutive_drawdowns += 1
            else:
                state.consecutive_drawdowns = 0
            state.last_pnl = total_daily_pnl
            state.peak_pnl = max(state.peak_pnl, total_daily_pnl)

            reason = None
            if total_daily_pnl <= -LIVE_MAX_DAILY_LOSS_RUPEES:
                reason = (f"daily loss limit breached: P&L {total_daily_pnl:+.2f} "
                          f"<= -{LIVE_MAX_DAILY_LOSS_RUPEES:.2f}")
            elif state.consecutive_drawdowns >= LIVE_MAX_CONSECUTIVE_DRAWDOWNS:
                reason = (f"{state.consecutive_drawdowns} consecutive drawdown checks "
                          f"(limit {LIVE_MAX_CONSECUTIVE_DRAWDOWNS})")

            if reason:
                state.tripped = True
                state.trip_reason = reason
                self._save(state)
                self._trip_kill_switch(reason)
            else:
                self._save(state)

    def _trip_kill_switch(self, reason: str):
        try:
            with open(self.kill_switch_path, "w") as f:
                f.write(f"auto-tripped by account_guard: {reason}\n")
            logger.error(f"[account_guard] LIVE TRADING KILL SWITCH TRIPPED — {reason}")
            print(f"[account_guard] LIVE TRADING KILL SWITCH TRIPPED — {reason}", flush=True)
        except OSError as e:
            # Can't write the kill-switch file — this is the worst-case
            # failure mode for this module (silent, unenforced trip), so
            # it's logged loudly rather than swallowed.
            logger.critical(f"[account_guard] FAILED TO WRITE KILL SWITCH FILE after trip "
                             f"({reason}) — live trading is NOT actually disabled: {e}")


# ── broker-response parsing helpers (kept separate from the guard so ─────
# ── they can be unit-tested against fixture payloads without SQLite) ─────

def pnl_from_positions(positions: list[dict]) -> float | None:
    """Sum whatever P&L field AngelOne's position rows expose. Tries the
    known field-name variants in order; returns None (not 0.0 — 0.0 is a
    valid "flat" P&L, not the same as "couldn't tell") if a row has none
    of them, so callers fail closed instead of trusting a silent zero.
    """
    if not positions:
        return 0.0
    total = 0.0
    for p in positions:
        for key in ("pnl", "netpnl", "realised", "realisedprofit"):
            if key in p and p[key] not in (None, ""):
                try:
                    total += float(p[key])
                    break
                except (TypeError, ValueError):
                    continue
        else:
            return None  # a row had none of the known P&L fields
    return total


def open_lots_from_positions(positions: list[dict], lot_size_lookup: dict[str, int]) -> int | None:
    """Sum absolute net quantity across all open positions, converted to
    lots via lot_size_lookup (should be PT_LOT_SIZES — the same single
    source of truth _handle_place_order already uses, so this check's
    idea of "how big is a lot" can't drift from the order-sizing logic).
    Returns None if any row's quantity or symbol can't be resolved.
    """
    if not positions:
        return 0
    total_lots = 0
    for p in positions:
        qty = None
        for key in ("netqty", "quantity", "netQty"):
            if key in p and p[key] not in (None, ""):
                try:
                    qty = int(float(p[key]))
                    break
                except (TypeError, ValueError):
                    continue
        if qty is None:
            return None
        symbol = (p.get("symbolname") or p.get("tradingsymbol") or "").upper()
        lot_size = None
        # BUGFIX: iterating lot_size_lookup in whatever order it happens to
        # have (dict insertion order) meant a shorter key that's a prefix of
        # a longer one — e.g. "SENSEX" is a prefix of "SENSEX50" — could
        # match first and return the WRONG symbol's lot size, silently
        # corrupting this guard's exposure math. Sorting by key length
        # (longest/most-specific first) guarantees "SENSEX50..." matches
        # "SENSEX50" before it can ever match the shorter "SENSEX". Same
        # fix already applied below in projected_open_lots_from_positions().
        for sym_key, size in sorted(lot_size_lookup.items(), key=lambda item: len(item[0]), reverse=True):
            if symbol.startswith(sym_key):
                lot_size = size
                break
        if not lot_size:
            return None
        total_lots += abs(qty) // lot_size
    return total_lots


def projected_open_lots_from_positions(
    positions: list[dict],
    lot_size_lookup: dict[str, int],
    target_tradingsymbol: str,
    side: str,
    order_quantity: int,
) -> float | None:
    """Return gross open lots after applying a proposed signed order.

    Positions are netted by exact trading symbol before the proposed order
    is applied. A SELL against a long (or BUY against a short) therefore
    reduces exposure, while an order in another contract adds exposure.
    Unknown position data returns ``None`` so callers can fail closed.
    """
    target = str(target_tradingsymbol or "").upper()
    normalized_side = str(side or "").upper()
    if not target or normalized_side not in ("BUY", "SELL") or order_quantity <= 0:
        return None
    signed_order_qty = order_quantity if normalized_side == "BUY" else -order_quantity

    net_by_instrument: dict[str, int] = {}
    for position in positions or []:
        qty = None
        for key in ("netqty", "quantity", "netQty"):
            if key in position and position[key] not in (None, ""):
                try:
                    qty = int(float(position[key]))
                    break
                except (TypeError, ValueError):
                    continue
        tradingsymbol = str(position.get("tradingsymbol") or "").upper()
        if qty is None or not tradingsymbol:
            return None
        net_by_instrument[tradingsymbol] = net_by_instrument.get(tradingsymbol, 0) + qty

    net_by_instrument[target] = net_by_instrument.get(target, 0) + signed_order_qty
    lot_sizes = sorted(lot_size_lookup.items(), key=lambda item: len(item[0]), reverse=True)
    total_lots = 0.0
    for tradingsymbol, net_qty in net_by_instrument.items():
        if net_qty == 0:
            continue
        lot_size = next(
            (size for symbol, size in lot_sizes if tradingsymbol.startswith(symbol.upper())),
            None,
        )
        if not lot_size:
            return None
        total_lots += abs(net_qty) / lot_size
    return total_lots