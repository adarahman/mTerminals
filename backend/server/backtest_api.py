"""HTTP serialization for the decision-history backtest endpoint."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiohttp import web


async def handle_backtest(
    request: Any,
    *,
    default_symbol: str,
    run_backtest: Callable[..., Awaitable[Any]],
):
    """Run a requested replay and return the dashboard's stable JSON shape."""
    symbol = (request.query.get("symbol") or default_symbol).strip().upper()

    def int_param(name: str, default: int) -> int:
        raw = request.query.get(name)
        try:
            return default if raw in (None, "") else int(raw)
        except ValueError:
            return default

    enabled = lambda name: str(request.query.get(name, "")).strip().lower() in {
        "1", "true", "yes"
    }
    try:
        result = await run_backtest(
            symbol,
            start=request.query.get("start") or None,
            end=request.query.get("end") or None,
            qty_lots=int_param("qtyLots", 1),
            min_confidence=int_param("minConfidence", 40),
            cooldown_seconds=int_param("cooldownSeconds", 300),
            max_trades_per_symbol_per_day=int_param("maxTradesPerSymbolPerDay", 10),
            use_account_guard=enabled("useAccountGuard"),
            override_execute_recommended=enabled("overrideExecuteRecommended"),
        )
    except Exception as exc:
        print(f"[http] /api/backtest failed for {symbol}: {exc}", flush=True)
        return web.json_response({"error": str(exc)}, status=500)

    cumulative = 0.0
    equity_curve = []
    for sequence, trade in enumerate(result.closed_trades, start=1):
        cumulative += trade.pnl
        equity_curve.append(
            {"seq": sequence, "ts": trade.exit_time, "cumPnl": round(cumulative, 2)}
        )

    fields = {
        "symbol": "symbol", "expiry": "expiry", "instrumentType": "instrument_type",
        "side": "side", "strike": "strike", "qtyLots": "qty_lots",
        "entryTime": "entry_time", "entryPrice": "entry_price",
        "exitTime": "exit_time", "exitPrice": "exit_price",
        "exitReason": "exit_reason", "pnl": "pnl",
    }
    trades = [{response: getattr(trade, model) for response, model in fields.items()}
              for trade in result.trades]
    return web.json_response({
        "symbol": symbol, "summary": result.summary(), "metadata": result.metadata(),
        "trades": trades, "equityCurve": equity_curve,
    })

