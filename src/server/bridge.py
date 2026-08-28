"""Dashboard-relay service kept separate from the live trading coordinator."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import orjson
from aiohttp import web

from analytics.fii_dii_market_bias import get_market_bias_report
from analytics.fii_dii_sentiment import get_report_for_trading_day
from analytics.nse_fii_dii_flow_fetch import get_flow_series
from server.websocket_clients import WebSocketClientHub

SECTOR_MAP = {
    "IT": ["INFY", "TCS"], "BANKING": ["HDFCBANK", "ICICIBANK"],
    "AUTO": ["MARUTI", "M&M"], "ENERGY": ["RELIANCE", "ONGC"],
    "METALS": ["TATASTEEL", "JSWSTEEL"], "PHARMA": ["SUNPHARMA", "DRREDDY"],
}


class DashboardBridge:
    """Serve the relay dashboard without coupling it to live-server globals.

    `state` returns the current process snapshot.  The coordinator remains
    the single source of live market state; this service only reshapes and
    publishes it for the relay protocol.
    """

    def __init__(
        self,
        state: Callable[[], dict[str, Any]],
        origin_allowed: Callable[[Any], bool],
        json_default: Callable[[Any], Any],
        market_api: Any,
        broker_futures_fetcher: Callable[[str, str], Any],
        public_futures_fetcher: Callable[[str, str], Any],
    ) -> None:
        self._state = state
        self._origin_allowed = origin_allowed
        self._json_default = json_default
        self._market_api = market_api
        self._broker_futures_fetcher = broker_futures_fetcher
        self._public_futures_fetcher = public_futures_fetcher
        self._clients = WebSocketClientHub()
        self._sectors = {"value": [], "fetched_at": 0.0}
        self._oi = {"ratio": None, "value": None, "fetched_at": 0.0}
        self._flow = {"value": None, "fetched_at": 0.0}
        self._bias = {"value": None, "fetched_at": 0.0}
        self._futures = {"value": None, "fetched_at": 0.0}

    @property
    def clients(self) -> set[Any]:
        return self._clients.clients

    def _quote(self, label, value, change, percent):
        return {
            "label": label, "val": f"{value:,.2f}",
            "chg": f"{'+' if (change or 0) >= 0 else ''}{change:.2f}" if change is not None else "—",
            "pct": f"{'+' if (percent or 0) >= 0 else ''}{percent:.2f}%" if percent is not None else "—",
            "dir": "up" if (change or percent or 0) >= 0 else "down",
        }

    def _fetch_futures(self):
        state = self._state()
        try:
            fetch = (
                self._broker_futures_fetcher
                if state["use_smartapi"]
                else self._public_futures_fetcher
            )
            frame = fetch(state["symbol"], state["futures_expiry"])
            if frame is None or frame.empty or frame.iloc[0]["LTP"] is None:
                return None
            row = frame.iloc[0]
            return self._quote(
                f"{state['symbol']} FUT (CUR)",
                row["LTP"], row.get("Change"), row.get("PctChange"),
            )
        except Exception as exc:
            print(f"[bridge] futures fetch failed: {exc}", flush=True)
            return None

    def _build_quotes(self):
        state, quotes = self._state(), []
        payload = state["last_payload"] or {}
        if payload.get("spot") is not None:
            quotes.append(self._quote(
                state["symbol"], payload["spot"], payload.get("spotChange"),
                payload.get("spotChgPct"),
            ))
        if payload.get("indiaVix") is not None:
            quotes.append(self._quote(
                "INDIA VIX", payload["indiaVix"], None, payload.get("indiaVixChgPct"),
            ))
        if self._futures["value"] is not None:
            quotes.append(self._futures["value"])
        for label, quote in state["index_quotes"].items():
            if quote.get("spot") is not None:
                quotes.append(self._quote(
                    label, quote["spot"], quote.get("spotChange"), quote.get("spotChgPct")
                ))
        return quotes

    @staticmethod
    def _build_skew(rows):
        strikes = sorted(
            (row["strike"], float(row["iv"])) for row in rows or []
            if row.get("iv") is not None
        )
        return [[index / max(len(strikes) - 1, 1), iv] for index, (_, iv) in enumerate(strikes)]

    def _fetch_sectors(self):
        try:
            rows = self._market_api.fetch_all_indices([self._market_api.FNO_STOCK_INDEX])
            by_symbol = {row.get("Symbol"): row for row in rows.to_dict("records")}
            result = []
            for name, symbols in SECTOR_MAP.items():
                stocks = []
                for symbol in symbols:
                    row = by_symbol.get(symbol) or {}
                    try:
                        pct = float(row.get("% Change", 0))
                    except (TypeError, ValueError):
                        pct = 0.0
                    stocks.append({"n": symbol, "v": f"{'+' if pct >= 0 else ''}{pct:.1f}%", "dir": "up" if pct >= 0 else "down"} if row else {"n": symbol, "v": "—", "dir": "flat"})
                result.append({"name": name, "tag": "—", "cls": "tag-neutral", "stocks": stocks})
            return result
        except Exception as exc:
            print(f"[bridge] sector fetch failed: {exc}", flush=True)
            return []

    @staticmethod
    def _fetch_oi():
        try:
            report = get_report_for_trading_day(datetime.now())
            if not report.get("available"):
                return None, None
            participants, colors = report["participants"], {
                "fii": "var(--violet)", "pro": "var(--amber)",
                "retail": "var(--grey)", "dii": "var(--green)",
            }
            keys = ("fii", "pro", "retail", "dii")
            totals = {key: sum(participants[key]["raw"].get(field, 0.0) for field in ("total_long_contracts", "total_short_contracts")) for key in keys}
            total = sum(totals.values()) or 1.0
            oi = [{"name": key.upper(), "pct": round(totals[key] / total * 1000) / 10, "color": colors[key], "trend": "LONG BUILD" if participants[key]["derived"]["index_fut_net"] >= 0 else "SHORT BUILD", "dir": "up" if participants[key]["derived"]["index_fut_net"] >= 0 else "down"} for key in keys]
            raw = participants["fii"]["raw"]
            long, short = raw.get("future_index_long", 0.0), raw.get("future_index_short", 0.0)
            return (round(long / (long + short) * 1000) / 10 if long + short else None), oi
        except Exception as exc:
            print(f"[bridge] OI fetch failed: {exc}", flush=True)
            return None, None

    @staticmethod
    def _fetch_flow():
        try:
            result = get_flow_series(30)
            return result if result.get("fii") and result.get("dii") else None
        except Exception as exc:
            print(f"[bridge] flow fetch failed: {exc}", flush=True)
            return None

    @staticmethod
    def _fetch_bias():
        try:
            return get_market_bias_report(datetime.now())
        except Exception as exc:
            print(f"[bridge] bias fetch failed: {exc}", flush=True)
            return None

    async def _refresh(self, cache, ttl, fetch, *keys):
        now = time.monotonic()
        if cache["value"] is not None and now - cache["fetched_at"] < ttl:
            return
        result = await asyncio.to_thread(fetch)
        if len(keys) == 1:
            if result is not None:
                cache["value"], cache["fetched_at"] = result, now
        elif result[1] is not None:
            cache["ratio"], cache["value"], cache["fetched_at"] = result[0], result[1], now

    async def _refresh_all(self):
        await self._refresh(self._sectors, 20, self._fetch_sectors, "value")
        await self._refresh(self._oi, 6 * 3600, self._fetch_oi, "ratio", "value")
        await self._refresh(self._flow, 6 * 3600, self._fetch_flow, "value")
        await self._refresh(self._bias, 6 * 3600, self._fetch_bias, "value")
        await self._refresh(self._futures, 5, self._fetch_futures, "value")

    def snapshot(self):
        payload = self._state()["last_payload"] or {}
        return {
            "quotes": self._build_quotes(), "skew": self._build_skew(payload.get("greeks")),
            "sectors": self._sectors["value"], "ratio": self._oi["ratio"],
            "oi": self._oi["value"], "flow": self._flow["value"], "bias": self._bias["value"],
        }

    async def broadcast(self, payload):
        message = orjson.dumps(payload, default=self._json_default).decode()
        await self._clients.broadcast(
            message, on_error=lambda error: print(f"[bridge] broadcast failed: {error}")
        )

    async def handle(self, request):
        if not self._origin_allowed(request):
            return web.Response(status=403, text="Origin not allowed")
        websocket = web.WebSocketResponse(heartbeat=20)
        await websocket.prepare(request)
        self._clients.add(websocket)
        try:
            await websocket.send_str(orjson.dumps(self.snapshot(), default=self._json_default).decode())
            async for _ in websocket:
                pass
        finally:
            self._clients.discard(websocket)
        return websocket

    async def run(self):
        while True:
            if self.clients:
                await self._refresh_all()
                await self.broadcast(self.snapshot())
            await asyncio.sleep(2)
