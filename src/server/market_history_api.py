"""Market-history HTTP endpoints, isolated from live-feed orchestration."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from aiohttp import web

RANGES = {
    "1m": {"interval": "ONE_MINUTE", "days": 5},
    "5m": {"interval": "FIVE_MINUTE", "days": 7},
    "15m": {"interval": "FIFTEEN_MINUTE", "days": 30},
    "1h": {"interval": "ONE_HOUR", "days": 90},
    "1d": {"interval": "ONE_DAY", "days": 730},
    "all": {"interval": "ONE_DAY", "days": 2000},
}


class MarketHistoryApi:
    """Serve chart history while owning its request de-duplication cache."""

    def __init__(
        self,
        state: Callable[[], dict[str, Any]],
        get_candle_data: Callable[..., Any],
        get_index_candles: Callable[..., Any],
    ) -> None:
        self._state = state
        self._get_candle_data = get_candle_data
        self._get_index_candles = get_index_candles
        self._cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        self._failures: dict[tuple[str, str], float] = {}

    async def spot_history(self, request):
        try:
            minutes = int(request.query.get("minutes", "15"))
        except (TypeError, ValueError):
            minutes = 15
        minutes = max(1, min(minutes, 24 * 60))
        state, symbol = self._state(), self._state()["symbol"]
        index = state["index_tokens"].get(symbol)
        if index is None:
            return web.json_response([])
        now = datetime.now()
        try:
            candles = await asyncio.to_thread(
                self._get_candle_data, index["exchange"], index["token"], "ONE_MINUTE",
                (now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M"),
                now.strftime("%Y-%m-%d %H:%M"),
            )
        except Exception as exc:
            print(f"[http] /api/spot-history failed for {symbol}: {exc}", flush=True)
            return web.json_response([])
        rows = []
        for candle in candles or []:
            try:
                rows.append({
                    "t": int(datetime.fromisoformat(candle["time"]).timestamp() * 1000),
                    "p": candle["close"],
                })
            except (ValueError, TypeError, KeyError):
                continue
        return web.json_response(rows)

    async def _cached(self, symbol, range_key, config):
        key, now = (symbol, range_key), time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < 20:
            return cached[1]
        if now - self._failures.get(key, float("-inf")) < 60:
            return []
        if future := self._inflight.get(key):
            return await future
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            now_dt = datetime.now()
            candles = await asyncio.to_thread(
                self._get_index_candles, symbol, config["interval"],
                (now_dt - timedelta(days=config["days"])).strftime("%Y-%m-%d %H:%M"),
                now_dt.strftime("%Y-%m-%d %H:%M"),
            )
            rows = []
            for candle in candles or []:
                try:
                    rows.append({
                        "t": int(datetime.fromisoformat(candle["timestamp"]).timestamp() * 1000),
                        "o": candle.get("open"), "h": candle.get("high"),
                        "l": candle.get("low"), "c": candle.get("close"),
                        "v": candle.get("volume"),
                    })
                except (ValueError, TypeError, KeyError):
                    continue
            self._cache[key] = (now, rows)
            self._failures.pop(key, None)
            future.set_result(rows)
            return rows
        except Exception as exc:
            self._failures[key] = time.monotonic()
            future.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)

    async def history(self, request):
        state = self._state()
        range_key = request.query.get("range", "1d")
        config = RANGES.get(range_key, RANGES["1d"])
        symbol = (request.query.get("symbol") or state["symbol"]).strip().upper()
        instrument = (request.query.get("instrument") or "EQ").strip().upper()
        expiry = (request.query.get("expiry") or "").strip().upper()
        if not state["broker_services_enabled"]:
            from brokers.public_history import fetch_public_history
            interval = {
                "ONE_MINUTE": "1m", "FIVE_MINUTE": "5m",
                "FIFTEEN_MINUTE": "15m", "ONE_HOUR": "60m", "ONE_DAY": "1d",
            }[config["interval"]]
            rows = await asyncio.to_thread(
                fetch_public_history, symbol, interval, config["days"],
                instrument=instrument, expiry=expiry,
            )
            response = web.json_response(rows)
            response.headers["X-MTerminals-History-Source"] = "public-cache"
            response.headers["X-MTerminals-Instrument"] = instrument
            return response
        if symbol not in state["index_tokens"]:
            return web.json_response([])
        try:
            return web.json_response(await self._cached(symbol, range_key, config))
        except Exception as exc:
            print(f"[http] /api/history failed for {symbol} range={range_key}: {exc}", flush=True)
            return web.json_response([])

    async def lot_sizes(self, _request):
        try:
            from brokers.smartapi.instruments import get_all_lot_sizes
            return web.json_response(await asyncio.to_thread(get_all_lot_sizes))
        except Exception as exc:
            print(f"[http] /api/lot-sizes failed: {exc}", flush=True)
            return web.json_response({"error": str(exc)}, status=500)


@web.middleware
async def no_cache_middleware(request, handler):
    response = await handler(request)
    if request.path == "/" or request.path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

