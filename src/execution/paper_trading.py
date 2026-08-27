"""
execution.paper_trading — standalone paper trading engine for fno-dashboard.

Design goals:
  - No dependency on engine.py / server/app.py — import this module and
    call into it; nothing here reaches back into your existing pipeline.
  - SQLite for storage (stdlib only, no new package) so orders/positions
    survive a WS server restart, unlike everything else in the live-tick
    pipeline which is intentionally stateless per your WebSocket push
    architecture.
  - Fills are driven by whatever LTP you already have on hand from your
    live tick (option chain / futures / spot) — this module never fetches
    its own market data, it only prices against what you feed it. That
    keeps it decoupled from market_api.py entirely.

Suggested integration:
  - server/app.py: on a new "place_order" WS message, call place_order()
    with the LTP for that leg pulled from the same tick's option chain data.
    On every broadcast tick, also call check_pending_orders() and
    mark_to_market() with a {instrument_key: ltp} map built from that tick,
    then push a new "portfolio" message type alongside your existing
    spot/oi/greeks messages — dashboard.js's onWsMessage() merge (deepMerge
    into _wsState) already handles arbitrary new message types for free.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

from infrastructure.paths import CACHE_DIR
import os

# Previously a bare relative filename ("paper_trading.db"), resolved
# against whatever the process's cwd happened to be at launch -- same
# class of problem paths.py already fixed for the OI/ScripMaster/FII-DII
# caches. Now lives alongside them in runtime/cache/.
DB_PATH = os.path.join(CACHE_DIR, "paper_trading.db")

logger = logging.getLogger("paper_trading")

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
OrderStatus = Literal["PENDING", "FILLED", "CANCELLED", "REJECTED"]
InstrumentType = Literal["CE", "PE", "FUT", "EQ", "INDEX"]


def _instrument_lot_size(symbol: str, instrument_type: str) -> int:
    """Cash equity/index quantity is already units; derivatives use lots."""
    return 1 if instrument_type in ("EQ", "INDEX") else get_lot_size(symbol)

# Last-resort fallback ONLY — used if the live instrument master can't be
# reached (network/API down) and this symbol has never been resolved
# RECONCILED (this pass): this module used to carry its own independent
# _FALLBACK_LOT_SIZES / _lot_size_cache / get_lot_size() / _LiveLotSizes,
# separate from lot_sizes.py's version — including a FINNIFTY value (65)
# that disagreed with lot_sizes.py's (60; confirmed correct). That's now
# consolidated in lot_sizes.py (the canonical shared module per the v4
# migration plan); this file just re-exports it so existing call sites
# (`from paper_trading import get_lot_size, LOT_SIZES`, incl.
# server/app.py) keep working unchanged.
from market.instruments.lot_sizes import get_lot_size, LOT_SIZES  # noqa: F401

# ── Risk / RMS-style order checks ────────────────────────────────────────
# Loose simulator-side stand-ins for the categories a real broker's Risk
# Management System checks before accepting an order (insufficient margin,
# price bands, max position/order value). This is NOT a real SPAN+exposure
# margin engine — it exists so the dashboard's rejection-handling UI
# (pt-status-tap already renders reject_reason) has realistic REJECTED
# orders to render, not just the three input-validation rejects that used
# to be the only ones this module could produce. Tune these constants
# rather than the check logic if the numbers feel off for your account.
PT_STARTING_CAPITAL = 100_000.0        # ₹1,00,000 paper capital — mirrors
                                        # PT_STARTING_CAPITAL in paper-trading.js;
                                        # keep both in sync manually, same as LOT_SIZES.
SHORT_MARGIN_PCT = 0.12                # crude SPAN+exposure stand-in for short/
                                        # written options — mirrors PT_SHORT_MARGIN_PCT
                                        # in paper-trading-shared.js's ptEstimateMarginBlocked.
MAX_NOTIONAL_PER_ORDER = 1_00_00_000.0  # ₹1 crore per-order cap ("Max Position
                                        # Limits" / total order value category).
PRICE_BAND_PCT = 0.20                  # LIMIT orders priced more than ±20% away
                                        # from current_ltp are rejected as a stand-in
                                        # for exchange LPP/price-band rejection.


def _fmt_strike(strike: float | None) -> str:
    """Normalize a strike to one canonical string regardless of whether
    it arrives as a Python int or float. Matters because SQLite always
    returns a REAL column as a float on read (25000 -> 25000.0), while a
    freshly-placed order still in memory keeps whatever type the caller
    passed (usually a plain int, since NSE strikes are typically whole
    numbers). Without this, _instrument_key(25000, ...) and
    _instrument_key(25000.0, ...) produced two DIFFERENT key strings for
    the same instrument — meaning a LIMIT order, whose Order object is
    always rebuilt from a DB row inside check_pending_orders(), could
    silently never match a current_prices dict keyed with int strikes
    (the natural way to build one) and would sit PENDING forever, or
    fragment into a second position row instead of merging with a MARKET
    order on the same strike."""
    if strike is None:
        return ""
    s = float(strike)
    return str(int(s)) if s == int(s) else repr(s)


def _instrument_key(symbol: str, expiry: str, strike: float | None,
                     instrument_type: str) -> str:
    """Canonical key used to match orders/positions to a live price. Must
    match however you key LTPs in the {instrument_key: ltp} map you pass
    into check_pending_orders()/mark_to_market() each tick."""
    if instrument_type in ("CE", "PE"):
        return f"{symbol}|{expiry}|{_fmt_strike(strike)}|{instrument_type}"
    return f"{symbol}|{expiry or ''}|{instrument_type}"


@dataclass
class Order:
    id: str
    timestamp: float
    symbol: str
    expiry: str
    strike: Optional[float]
    instrument_type: str          # CE / PE / FUT / EQ / INDEX
    side: OrderSide
    qty_lots: int
    order_type: OrderType
    limit_price: Optional[float]
    status: OrderStatus
    fill_price: Optional[float] = None
    fill_timestamp: Optional[float] = None
    reject_reason: Optional[str] = None
    client_order_id: Optional[str] = None
    price_source: Optional[str] = None
    fill_delay_ms: Optional[int] = None
    slippage_assumption: Optional[str] = None


@dataclass
class Position:
    instrument_key: str
    symbol: str
    expiry: str
    strike: Optional[float]
    instrument_type: str
    net_qty_lots: int            # +ve = net long, -ve = net short
    avg_price: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    last_price: Optional[float] = None


class PaperTradingEngine:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        # check_same_thread=False only disables Python's own thread-owner
        # check on the connection — it does NOT make concurrent access
        # safe. _apply_fill_to_position() does a SELECT then a separate
        # UPDATE derived from that row (blend avg_price / realize P&L);
        # two overlapping fills on the same instrument_key could both read
        # the same starting state and one write would clobber the other.
        # An RLock (not Lock) because check_pending_orders() calls _fill()
        # -> _apply_fill_to_position() while already holding it.
        self._write_lock = threading.RLock()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                expiry TEXT,
                strike REAL,
                instrument_type TEXT NOT NULL,
                side TEXT NOT NULL,
                qty_lots INTEGER NOT NULL,
                order_type TEXT NOT NULL,
                limit_price REAL,
                status TEXT NOT NULL,
                fill_price REAL,
                fill_timestamp REAL,
                reject_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                instrument_key TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                expiry TEXT,
                strike REAL,
                instrument_type TEXT NOT NULL,
                net_qty_lots INTEGER NOT NULL,
                avg_price REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0
            );
        """)
        # Additive migrations for databases created before PDS-07 P0.
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(orders)")}
        for name, sql_type in (
            ("client_order_id", "TEXT"), ("price_source", "TEXT"),
            ("fill_delay_ms", "INTEGER"), ("slippage_assumption", "TEXT"),
        ):
            if name not in existing:
                self._conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {sql_type}")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_client_order_id "
            "ON orders(client_order_id) WHERE client_order_id IS NOT NULL")
        self._conn.commit()

    # ── Order placement ──────────────────────────────────────────────
    def place_order(self, symbol: str, side: OrderSide, qty_lots: int,
                     instrument_type: InstrumentType = "INDEX",
                     expiry: str = "", strike: float | None = None,
                     order_type: OrderType = "MARKET",
                     limit_price: float | None = None,
                     current_ltp: float | None = None,
                     spot_price: float | None = None,
                     account_capital: float = PT_STARTING_CAPITAL,
                     enforce_risk_checks: bool = True,
                     client_order_id: str | None = None) -> Order:
        """spot_price and account_capital only matter when enforce_risk_checks
        is True. spot_price is the underlying's current price, used to size
        short-option margin the same way ptEstimateMarginBlocked() does in
        paper-trading.js; if omitted, the option's own price is used as a
        rougher stand-in rather than hard-failing on missing spot data.
        Pass enforce_risk_checks=False to get the old (input-validation-only)
        behavior, e.g. for backtests that don't care about account sizing.

        Held under self._write_lock end-to-end (including the margin check)
        so two orders on the same/overlapping instruments can't both read
        the same pre-fill position/margin state and both pass a check that,
        applied together, should have rejected the second one."""
        with self._write_lock:
            # Browser reconnects/retries may deliver the same submission
            # more than once. Return the original durable result instead of
            # creating a second fill/position mutation.
            if client_order_id:
                prior = self._conn.execute(
                    "SELECT * FROM orders WHERE client_order_id=?", (client_order_id,)
                ).fetchone()
                if prior:
                    return Order(**dict(prior))
            if order_type not in ("MARKET", "LIMIT"):
                return self._reject(symbol, expiry, strike, instrument_type,
                                    side, qty_lots, order_type, limit_price,
                                    f"Unsupported paper order type: {order_type}",
                                    client_order_id)
            if qty_lots <= 0:
                return self._reject(symbol, expiry, strike, instrument_type,
                                     side, qty_lots, order_type, limit_price,
                                     "qty_lots must be positive", client_order_id)
            if order_type == "MARKET" and current_ltp is None:
                return self._reject(symbol, expiry, strike, instrument_type,
                                     side, qty_lots, order_type, limit_price,
                                     "MARKET order requires current_ltp", client_order_id)
            if order_type == "LIMIT" and limit_price is None:
                return self._reject(symbol, expiry, strike, instrument_type,
                                     side, qty_lots, order_type, limit_price,
                                     "LIMIT order requires limit_price", client_order_id)

            ref_price = current_ltp if order_type == "MARKET" else limit_price
            lot_size = _instrument_lot_size(symbol, instrument_type)

            if enforce_risk_checks:
                # 1) Price band / LPP stand-in — only meaningful for LIMIT
                # orders, since a MARKET order fills at current_ltp itself and
                # can't be "far" from it.
                if order_type == "LIMIT" and current_ltp:
                    band = abs(limit_price - current_ltp) / current_ltp
                    if band > PRICE_BAND_PCT:
                        return self._reject(
                            symbol, expiry, strike, instrument_type, side,
                            qty_lots, order_type, limit_price,
                            f"Price {limit_price:.2f} outside allowed band "
                            f"(±{PRICE_BAND_PCT:.0%} of LTP {current_ltp:.2f})", client_order_id)

                # 2) Max order value — premium turnover, same "turnover" concept
                # ptCalcCharges() uses in paper-trading.js.
                order_notional = ref_price * qty_lots * lot_size
                if order_notional > MAX_NOTIONAL_PER_ORDER:
                    return self._reject(
                        symbol, expiry, strike, instrument_type, side, qty_lots,
                        order_type, limit_price,
                        f"Order value \u20b9{order_notional:,.0f} exceeds max "
                        f"per-order limit (\u20b9{MAX_NOTIONAL_PER_ORDER:,.0f})", client_order_id)

                # 3) Margin/funds — only bites if this order OPENS or ADDS to a
                # position. Closing/reducing an existing position releases
                # margin rather than requiring more, same as a real broker.
                key = _instrument_key(symbol, expiry, strike, instrument_type)
                existing = self._conn.execute(
                    "SELECT net_qty_lots FROM positions WHERE instrument_key=?",
                    (key,)).fetchone()
                existing_net = existing["net_qty_lots"] if existing else 0
                signed_qty = qty_lots if side == "BUY" else -qty_lots
                is_reducing = (existing_net != 0
                               and (existing_net > 0) != (signed_qty > 0)
                               and abs(signed_qty) <= abs(existing_net))

                if not is_reducing:
                    if side == "BUY":
                        order_margin = order_notional  # premium paid in full
                    else:
                        underlying_ref = spot_price or ref_price
                        order_margin = SHORT_MARGIN_PCT * underlying_ref * qty_lots * lot_size
                    existing_margin = self._estimate_margin_blocked(spot_price)
                    if existing_margin + order_margin > account_capital:
                        free = max(0.0, account_capital - existing_margin)
                        return self._reject(
                            symbol, expiry, strike, instrument_type, side,
                            qty_lots, order_type, limit_price,
                            f"Insufficient margin — order needs \u20b9{order_margin:,.0f}, "
                            f"only \u20b9{free:,.0f} free", client_order_id)

            order = Order(
                id=str(uuid.uuid4()), timestamp=time.time(), symbol=symbol,
                expiry=expiry, strike=strike, instrument_type=instrument_type,
                side=side, qty_lots=qty_lots, order_type=order_type,
                limit_price=limit_price, status="PENDING",
                client_order_id=client_order_id,
                price_source="server_live_tick" if order_type == "MARKET" else "user_limit",
                slippage_assumption="none",
            )

            if order_type == "MARKET":
                self._fill(order, current_ltp)
            else:
                self._save_order(order)

            return order

    def _estimate_margin_blocked(self, spot_price: float | None = None) -> float:
        """Approximate margin currently locked by open positions — mirrors
        ptEstimateMarginBlocked() in paper-trading.js. Longs: premium
        already paid (avg_price * qty * lot_size). Shorts: SHORT_MARGIN_PCT
        of notional (spot * qty * lot_size); falls back to the position's
        own avg_price if no spot_price is supplied, which is rougher but
        avoids a hard dependency on a live spot feed being threaded through
        every call site."""
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE net_qty_lots != 0").fetchall()
        total = 0.0
        for row in rows:
            lot_size = _instrument_lot_size(row["symbol"], row["instrument_type"])
            qty = abs(row["net_qty_lots"])
            if row["net_qty_lots"] > 0:
                total += row["avg_price"] * qty * lot_size
            else:
                underlying_ref = spot_price or row["avg_price"]
                total += SHORT_MARGIN_PCT * underlying_ref * qty * lot_size
        return total

    def get_fund_summary(self, spot_price: float | None = None,
                          current_prices: dict[str, float] | None = None,
                          account_capital: float = PT_STARTING_CAPITAL) -> dict:
        """Server-side equivalent of paper-trading-shared.js's ptComputeFundSummary()
        for the paper-mode branch — lets a caller show the same fund/margin
        figures without duplicating the estimation logic client-side."""
        margin_blocked = self._estimate_margin_blocked(spot_price)
        portfolio = self.get_portfolio_summary(current_prices)
        equity = account_capital + portfolio["total_pnl"]
        fund = equity - margin_blocked
        return {
            "capital": account_capital,
            "realized_pnl": portfolio["realized_pnl"],
            "unrealized_pnl": portfolio["unrealized_pnl"],
            "equity": round(equity, 2),
            "margin_blocked": round(margin_blocked, 2),
            "fund": round(fund, 2),
            "low_fund": fund < account_capital * 0.20,
        }

    def _reject(self, symbol, expiry, strike, instrument_type, side,
                qty_lots, order_type, limit_price, reason,
                client_order_id=None) -> Order:
        order = Order(
            id=str(uuid.uuid4()), timestamp=time.time(), symbol=symbol,
            expiry=expiry, strike=strike, instrument_type=instrument_type,
            side=side, qty_lots=qty_lots, order_type=order_type,
            limit_price=limit_price, status="REJECTED", reject_reason=reason,
            client_order_id=client_order_id, price_source="unavailable",
            slippage_assumption="none",
        )
        self._save_order(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        with self._write_lock:
            cur = self._conn.execute(
                "UPDATE orders SET status='CANCELLED' WHERE id=? AND status='PENDING'",
                (order_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ── Pending LIMIT order matching — call once per WS tick ────────
    def check_pending_orders(self, current_prices: dict[str, float]):
        """current_prices: {instrument_key: ltp} built from this tick's
        option chain / futures / spot data using _instrument_key()."""
        with self._write_lock:
            pending = self._conn.execute(
                "SELECT * FROM orders WHERE status='PENDING' AND order_type='LIMIT'"
            ).fetchall()

            for row in pending:
                key = _instrument_key(row["symbol"], row["expiry"], row["strike"],
                                       row["instrument_type"])
                ltp = current_prices.get(key)
                if ltp is None:
                    continue

                crosses = (ltp <= row["limit_price"] if row["side"] == "BUY"
                           else ltp >= row["limit_price"])
                if crosses:
                    order = Order(**{k: row[k] for k in row.keys()})
                    self._fill(order, ltp)

    # ── Fill + position update ───────────────────────────────────────
    def _fill(self, order: Order, fill_price: float):
        order.status = "FILLED"
        order.fill_price = fill_price
        order.fill_timestamp = time.time()
        order.fill_delay_ms = max(0, round((order.fill_timestamp - order.timestamp) * 1000))
        if order.order_type == "LIMIT":
            order.price_source = "server_live_tick_at_limit_cross"
        self._save_order(order)
        self._apply_fill_to_position(order)

    def _apply_fill_to_position(self, order: Order):
        key = _instrument_key(order.symbol, order.expiry, order.strike,
                               order.instrument_type)
        row = self._conn.execute(
            "SELECT * FROM positions WHERE instrument_key=?", (key,)
        ).fetchone()

        signed_qty = order.qty_lots if order.side == "BUY" else -order.qty_lots

        if row is None:
            self._conn.execute(
                "INSERT INTO positions (instrument_key, symbol, expiry, strike, "
                "instrument_type, net_qty_lots, avg_price, realized_pnl) "
                "VALUES (?,?,?,?,?,?,?,0)",
                (key, order.symbol, order.expiry, order.strike,
                 order.instrument_type, signed_qty, order.fill_price))
            self._conn.commit()
            return

        net_qty, avg_price, realized = row["net_qty_lots"], row["avg_price"], row["realized_pnl"]
        new_net = net_qty + signed_qty

        same_direction = (net_qty >= 0 and signed_qty > 0) or (net_qty <= 0 and signed_qty < 0)

        if net_qty == 0 or same_direction:
            # Opening or adding to a position — blend average price.
            total_cost = avg_price * abs(net_qty) + order.fill_price * abs(signed_qty)
            avg_price = total_cost / abs(new_net) if new_net != 0 else 0.0
        else:
            # Reducing or flipping — realize P&L on the closed portion.
            closed_qty = min(abs(signed_qty), abs(net_qty))
            # BUY closing a short realizes (avg_price - fill_price) per lot;
            # SELL closing a long realizes (fill_price - avg_price) per lot.
            pnl_per_lot = (avg_price - order.fill_price) if order.side == "BUY" \
                else (order.fill_price - avg_price)
            lot_size = _instrument_lot_size(order.symbol, order.instrument_type)
            realized += pnl_per_lot * closed_qty * lot_size
            if abs(signed_qty) > abs(net_qty):
                # Flipped through zero — remainder opens a new position at fill price.
                avg_price = order.fill_price

        self._conn.execute(
            "UPDATE positions SET net_qty_lots=?, avg_price=?, realized_pnl=? "
            "WHERE instrument_key=?",
            (new_net, avg_price, realized, key))
        self._conn.commit()

    def _save_order(self, order: Order):
        self._conn.execute(
            "INSERT INTO orders (id, timestamp, symbol, expiry, strike, "
            "instrument_type, side, qty_lots, order_type, limit_price, "
            "status, fill_price, fill_timestamp, reject_reason, client_order_id, "
            "price_source, fill_delay_ms, slippage_assumption) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "fill_price=excluded.fill_price, fill_timestamp=excluded.fill_timestamp, "
            "price_source=excluded.price_source, fill_delay_ms=excluded.fill_delay_ms, "
            "slippage_assumption=excluded.slippage_assumption",
            (order.id, order.timestamp, order.symbol, order.expiry, order.strike,
             order.instrument_type, order.side, order.qty_lots, order.order_type,
             order.limit_price, order.status, order.fill_price,
             order.fill_timestamp, order.reject_reason, order.client_order_id,
             order.price_source, order.fill_delay_ms, order.slippage_assumption))
        self._conn.commit()

    # ── Mark-to-market ────────────────────────────────────────────────
    def mark_to_market(self, current_prices: dict[str, float]):
        """Currently an intentional no-op — kept only as a documented
        integration point (see the module docstring's suggested WS
        integration). unrealized_pnl is NOT stored; it's computed fresh on
        every read inside get_positions()/get_portfolio_summary() from
        whatever current_prices dict is passed to THOSE calls. Calling
        mark_to_market() is therefore harmless but has no effect — it does
        NOT need to run before get_positions()/get_portfolio_summary() for
        their numbers to be correct. If per-tick position snapshots ever
        need to be persisted (e.g. for a P&L history chart), that logic
        belongs here; until then there's nothing to do."""
        return None

    # ── Read APIs ─────────────────────────────────────────────────────
    def get_orders(self, status: OrderStatus | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM orders WHERE status=? ORDER BY timestamp DESC",
                (status,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM orders ORDER BY timestamp DESC").fetchall()
        return [dict(r) for r in rows]

    def get_positions(self, current_prices: dict[str, float] | None = None) -> list[dict]:
        current_prices = current_prices or {}
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE net_qty_lots != 0").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            ltp = current_prices.get(row["instrument_key"])
            d["last_price"] = ltp
            # Must match the *_apply_fill_to_position realized-P&L formula
            # (pnl_per_lot * closed_qty * lot_size) — this was previously
            # missing the lot_size factor, so unrealized P&L (and therefore
            # portfolio total_pnl) understated the real figure by whatever
            # the instrument's lot size is (e.g. 65x for NIFTY).
            d["unrealized_pnl"] = (
                (ltp - row["avg_price"]) * row["net_qty_lots"] * _instrument_lot_size(row["symbol"], row["instrument_type"])
                if ltp is not None else None
            )
            out.append(d)
        return out

    def get_portfolio_summary(self, current_prices: dict[str, float] | None = None) -> dict:
        positions = self.get_positions(current_prices)
        total_realized = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS r FROM positions"
        ).fetchone()["r"]
        total_unrealized = sum(
            p["unrealized_pnl"] for p in positions if p["unrealized_pnl"] is not None
        )
        return {
            "positions": positions,
            "open_position_count": len(positions),
            "realized_pnl": round(total_realized, 2),
            "unrealized_pnl": round(total_unrealized, 2),
            "total_pnl": round(total_realized + total_unrealized, 2),
        }


if __name__ == "__main__":
    # Smoke test — not a real market tick, just proves the fill/position/
    # P&L math end-to-end without needing your live WS feed.
    eng = PaperTradingEngine(db_path=":memory:")

    o1 = eng.place_order("NIFTY", "BUY", qty_lots=2, instrument_type="CE",
                          expiry="31-Jul-2026", strike=25000,
                          order_type="MARKET", current_ltp=120.5)
    print("Order 1:", o1)

    o2 = eng.place_order("NIFTY", "SELL", qty_lots=1, instrument_type="CE",
                          expiry="31-Jul-2026", strike=25000,
                          order_type="MARKET", current_ltp=145.0)
    print("Order 2 (partial close):", o2)

    key = _instrument_key("NIFTY", "31-Jul-2026", 25000, "CE")
    print("\nPortfolio:", eng.get_portfolio_summary({key: 150.0}))

    # Price-band reject: LIMIT price >20% away from current_ltp.
    o3 = eng.place_order("NIFTY", "BUY", qty_lots=1, instrument_type="CE",
                          expiry="31-Jul-2026", strike=25000,
                          order_type="LIMIT", limit_price=200.0,
                          current_ltp=120.5)
    print("\nOrder 3 (price band reject):", o3.status, "-", o3.reject_reason)

    # Margin reject: a large fresh position against a small paper account.
    o4 = eng.place_order("BANKNIFTY", "SELL", qty_lots=50, instrument_type="PE",
                          expiry="31-Jul-2026", strike=52000,
                          order_type="MARKET", current_ltp=300.0,
                          spot_price=52000.0, account_capital=100_000.0)
    print("Order 4 (margin reject):", o4.status, "-", o4.reject_reason)

    print("\nFund summary:", eng.get_fund_summary(spot_price=25100.0))
    print("\nAll orders:", eng.get_orders())
