"""
paper_trading.py — standalone paper trading engine for fno-dashboard.

Design goals:
  - No dependency on engine.py / ws_server_live.py — import this module and
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
  - ws_server_live.py: on a new "place_order" WS message, call place_order()
    with the LTP for that leg pulled from the same tick's option chain data.
    On every broadcast tick, also call check_pending_orders() and
    mark_to_market() with a {instrument_key: ltp} map built from that tick,
    then push a new "portfolio" message type alongside your existing
    spot/oi/greeks messages — dashboard.js's onWsMessage() merge (deepMerge
    into _wsState) already handles arbitrary new message types for free.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

DB_PATH = "paper_trading.db"

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
OrderStatus = Literal["PENDING", "FILLED", "CANCELLED", "REJECTED"]
InstrumentType = Literal["CE", "PE", "FUT", "EQ", "INDEX"]

# NSE revises F&O lot sizes periodically (typically each quarterly review).
# These are UNVERIFIED as of this file's creation — confirm against NSE's
# current circular before relying on this for margin/notional calculations.
# Wrong lot size silently produces wrong P&L, same failure class as the
# VWAP bug — it won't error, it'll just be quietly incorrect.
LOT_SIZES = {
    "NIFTY": 75,
    "BANKNIFTY": 35,
    "MIDCPNIFTY": 140,
    "SENSEX": 20,
    "FINNIFTY": 65,
}


def _instrument_key(symbol: str, expiry: str, strike: float | None,
                     instrument_type: str) -> str:
    """Canonical key used to match orders/positions to a live price. Must
    match however you key LTPs in the {instrument_key: ltp} map you pass
    into check_pending_orders()/mark_to_market() each tick."""
    if instrument_type in ("CE", "PE"):
        return f"{symbol}|{expiry}|{strike}|{instrument_type}"
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
        self._conn.commit()

    # ── Order placement ──────────────────────────────────────────────
    def place_order(self, symbol: str, side: OrderSide, qty_lots: int,
                     instrument_type: InstrumentType = "INDEX",
                     expiry: str = "", strike: float | None = None,
                     order_type: OrderType = "MARKET",
                     limit_price: float | None = None,
                     current_ltp: float | None = None) -> Order:
        if qty_lots <= 0:
            return self._reject(symbol, expiry, strike, instrument_type,
                                 side, qty_lots, order_type, limit_price,
                                 "qty_lots must be positive")

        order = Order(
            id=str(uuid.uuid4()), timestamp=time.time(), symbol=symbol,
            expiry=expiry, strike=strike, instrument_type=instrument_type,
            side=side, qty_lots=qty_lots, order_type=order_type,
            limit_price=limit_price, status="PENDING",
        )

        if order_type == "MARKET":
            if current_ltp is None:
                return self._reject(symbol, expiry, strike, instrument_type,
                                     side, qty_lots, order_type, limit_price,
                                     "MARKET order requires current_ltp")
            self._fill(order, current_ltp)
        else:
            if limit_price is None:
                return self._reject(symbol, expiry, strike, instrument_type,
                                     side, qty_lots, order_type, limit_price,
                                     "LIMIT order requires limit_price")
            self._save_order(order)

        return order

    def _reject(self, symbol, expiry, strike, instrument_type, side,
                qty_lots, order_type, limit_price, reason) -> Order:
        order = Order(
            id=str(uuid.uuid4()), timestamp=time.time(), symbol=symbol,
            expiry=expiry, strike=strike, instrument_type=instrument_type,
            side=side, qty_lots=qty_lots, order_type=order_type,
            limit_price=limit_price, status="REJECTED", reject_reason=reason,
        )
        self._save_order(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE orders SET status='CANCELLED' WHERE id=? AND status='PENDING'",
            (order_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── Pending LIMIT order matching — call once per WS tick ────────
    def check_pending_orders(self, current_prices: dict[str, float]):
        """current_prices: {instrument_key: ltp} built from this tick's
        option chain / futures / spot data using _instrument_key()."""
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
            lot_size = LOT_SIZES.get(order.symbol, 65)
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
            "status, fill_price, fill_timestamp, reject_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "fill_price=excluded.fill_price, fill_timestamp=excluded.fill_timestamp",
            (order.id, order.timestamp, order.symbol, order.expiry, order.strike,
             order.instrument_type, order.side, order.qty_lots, order.order_type,
             order.limit_price, order.status, order.fill_price,
             order.fill_timestamp, order.reject_reason))
        self._conn.commit()

    # ── Mark-to-market — call once per WS tick ───────────────────────
    def mark_to_market(self, current_prices: dict[str, float]):
        rows = self._conn.execute("SELECT * FROM positions").fetchall()
        for row in rows:
            ltp = current_prices.get(row["instrument_key"])
            if ltp is None or row["net_qty_lots"] == 0:
                continue
            # unrealized = (ltp - avg) * qty for longs, inverse for shorts —
            # signed net_qty_lots already encodes direction, so one formula
            # covers both.
            # (Computed on read in get_portfolio(), not stored, to avoid a
            # write on every tick for a value that's cheap to derive.)
            pass  # positions table stays cost-basis only; see get_portfolio()

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
            d["unrealized_pnl"] = (
                (ltp - row["avg_price"]) * row["net_qty_lots"]
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
    print("\nAll orders:", eng.get_orders())
