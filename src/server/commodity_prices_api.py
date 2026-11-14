"""Read-only live MCX futures quote endpoint."""

from collections import deque
import asyncio
import math
import time
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from analytics.commodity_option_analysis import analyze_commodity_option_chain


COMMODITY_SYMBOLS = (
    "ALUMINIUM",
    "COPPER",
    "CRUDEOIL",
    "GOLD",
    "GOLDM",
    "LEAD",
    "NATURALGAS",
    "SILVER",
    "SILVERM",
    "ZINC",
)
COMMODITY_NEWS_TERMS = {
    "ALUMINIUM": '("aluminium prices" OR "aluminum prices" OR aluminium market)',
    "COPPER": '("copper prices" OR copper market OR copper supply)',
    "CRUDEOIL": '("crude oil" OR "oil prices" OR petroleum market)',
    "GOLD": '(gold prices OR gold market OR bullion)',
    "GOLDM": '(gold prices OR gold market OR bullion)',
    "LEAD": '("lead metal" OR "lead prices" OR lead market)',
    "NATURALGAS": '("natural gas prices" OR "natural gas market")',
    "SILVER": '(silver prices OR silver market OR bullion)',
    "SILVERM": '(silver prices OR silver market OR bullion)',
    "ZINC": '(zinc prices OR zinc market OR zinc supply)',
}
GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


class CommodityPricesApi:
    _HISTORY_WINDOW_SECONDS = 300
    _HISTORY_MAX_AGE_SECONDS = 1200

    def __init__(self, *, market_data=None, active_provider=None):
        self._market_data = (
            market_data if market_data is not None else self._active_market_data
        )
        self._active_provider = (
            active_provider if active_provider is not None else self._get_active_provider
        )
        self._chain_history = {}
        self._quote_history = {}
        self._quote_metrics_history = {}
        self._news_cache = {}

    def _observe_quote(self, symbol, quote):
        """Keep one futures close per minute for the commodity 2–3m view."""
        try:
            price = float(quote.get("LTP"))
        except (AttributeError, TypeError, ValueError):
            return []
        if not price or price <= 0:
            return list(self._quote_history.get(symbol, ()))
        now = time.monotonic()
        minute = int(now // 60)
        history = self._quote_history.setdefault(symbol, deque(maxlen=8))
        if history and minute - history[-1][0] > 1:
            history.clear()
        if history and history[-1][0] == minute:
            history[-1] = (minute, price)
        else:
            history.append((minute, price))
        return list(history)

    def _observe_quote_metrics(self, symbol, quote):
        """Return measured five-minute price, OI, and cumulative-volume changes."""
        try:
            price = float(quote.get("LTP"))
        except (AttributeError, TypeError, ValueError):
            return {"available": False}
        if not math.isfinite(price) or price <= 0:
            return {"available": False}

        now = time.monotonic()
        minute = int(now // 60)
        history = self._quote_metrics_history.setdefault(symbol, deque(maxlen=20))
        try:
            oi = float(quote.get("OI"))
            if not math.isfinite(oi) or oi < 0:
                oi = None
        except (TypeError, ValueError):
            oi = None
        try:
            volume = float(quote.get("Volume"))
            if not math.isfinite(volume) or volume < 0:
                volume = None
        except (TypeError, ValueError):
            volume = None

        sample = {"price": price, "oi": oi, "volume": volume}
        if history and history[-1][0] == minute:
            history[-1] = (minute, sample)
        else:
            history.append((minute, sample))
        baseline = next(
            ((at, values) for at, values in reversed(history)
             if at <= minute - 5),
            None,
        )
        if baseline is None or minute - baseline[0] > 7:
            return {"available": False, "window_minutes": 5}

        old = baseline[1]
        result = {
            "available": True,
            "window_minutes": minute - baseline[0],
            "price_change": round(price - old["price"], 2),
            "price_change_pct": round((price / old["price"] - 1) * 100, 2),
            "oi_change": (
                round(oi - old["oi"], 2)
                if oi is not None and old["oi"] is not None else None
            ),
            "volume_change": (
                round(volume - old["volume"], 2)
                if volume is not None and old["volume"] is not None
                    and volume >= old["volume"] else None
            ),
        }
        return result

    def _observe_chain(self, symbol, expiry, chain):
        now = time.monotonic()
        key = (symbol, expiry)
        history = self._chain_history.setdefault(key, deque(maxlen=180))
        cutoff = now - self._HISTORY_WINDOW_SECONDS
        baseline = next(
            ((timestamp, rows) for timestamp, rows in reversed(history)
             if timestamp <= cutoff),
            None,
        )
        current_rows = [dict(row) for row in (chain.get("rows") or [])]
        history.append((now, current_rows))

        expired_keys = [
            history_key for history_key, snapshots in self._chain_history.items()
            if snapshots and now - snapshots[-1][0] > self._HISTORY_MAX_AGE_SECONDS
        ]
        for history_key in expired_keys:
            self._chain_history.pop(history_key, None)

        if baseline is None:
            return None, None
        elapsed = now - baseline[0]
        if elapsed > self._HISTORY_MAX_AGE_SECONDS / 2:
            return None, None
        return baseline[1], elapsed

    @staticmethod
    def _active_market_data():
        from brokers.market_data_registry import market_data

        return market_data

    @staticmethod
    def _get_active_provider():
        from brokers.market_data_registry import get_active_provider

        return get_active_provider()

    async def quote(self, request):
        symbol = request.query.get("symbol", "CRUDEOIL").strip().upper()
        if symbol not in COMMODITY_SYMBOLS:
            return web.json_response(
                {"error": "Unsupported commodity symbol."}, status=400
            )

        provider = self._market_data()
        get_quote = getattr(provider, "get_commodity_futures_quote", None)
        provider_name = self._active_provider()
        if not callable(get_quote):
            return web.json_response(
                {
                    "error": (
                        "The active market-data provider has no MCX futures "
                        "quote adapter configured."
                    ),
                    "provider": provider_name,
                },
                status=501,
            )

        quote = await asyncio.to_thread(get_quote, symbol)
        if not quote:
            return web.json_response(
                {
                    "error": f"No live MCX quote available for {symbol}.",
                    "provider": provider_name,
                    "symbol": symbol,
                },
                status=404,
            )
        self._observe_quote(symbol, quote)
        quote = {**quote, "FiveMinute": self._observe_quote_metrics(symbol, quote)}
        return web.json_response(
            {"provider": provider_name, "symbol": symbol, "quote": quote}
        )

    async def option_chain(self, request):
        symbol = request.query.get("symbol", "CRUDEOIL").strip().upper()
        if symbol not in COMMODITY_SYMBOLS:
            return web.json_response(
                {"error": "Unsupported commodity symbol."}, status=400
            )

        provider = self._market_data()
        get_expiries = getattr(provider, "get_commodity_option_expiries", None)
        get_chain = getattr(provider, "get_commodity_option_chain", None)
        provider_name = self._active_provider()
        if not callable(get_expiries) or not callable(get_chain):
            return web.json_response(
                {
                    "error": (
                        "The active market-data provider has no MCX option-chain "
                        "adapter configured."
                    ),
                    "provider": provider_name,
                },
                status=501,
            )

        expiries = await asyncio.to_thread(get_expiries, symbol)
        if not expiries:
            return web.json_response(
                {
                    "error": f"No MCX option expiries found for {symbol}.",
                    "provider": provider_name,
                    "symbol": symbol,
                },
                status=404,
            )
        expiry = request.query.get("expiry", "").strip().upper() or expiries[0]
        if expiry not in expiries:
            return web.json_response(
                {"error": "Expiry is not available for this commodity."},
                status=400,
            )

        chain = await asyncio.to_thread(
            get_chain, symbol, expiry, strikes_around_atm=10
        )
        if not chain:
            return web.json_response(
                {
                    "error": f"No option-chain data available for {symbol}.",
                    "provider": provider_name,
                    "symbol": symbol,
                    "expiry": expiry,
                },
                status=404,
            )
        chain["expiry"] = expiry
        chain["symbol"] = symbol
        price_closes = [
            (float(minute), float(price))
            for minute, price in self._quote_history.get(symbol, ())
        ]
        previous_rows, elapsed = self._observe_chain(symbol, expiry, chain)
        return web.json_response(
            {
                "provider": provider_name,
                "symbol": symbol,
                "expiry": expiry,
                "expiries": expiries,
                "chain": chain,
                "analysis": analyze_commodity_option_chain(
                    chain,
                    previous_rows=previous_rows,
                    elapsed_seconds=elapsed,
                    price_closes=price_closes,
                ),
            }
        )

    async def news(self, request):
        symbol = request.query.get("symbol", "CRUDEOIL").strip().upper()
        if symbol not in COMMODITY_SYMBOLS:
            return web.json_response(
                {"error": "Unsupported commodity symbol."}, status=400
            )

        now = time.monotonic()
        cached = self._news_cache.get(symbol)
        if cached and now - cached[0] < 300:
            return web.json_response(cached[1])

        params = {
            "query": COMMODITY_NEWS_TERMS[symbol],
            "mode": "artlist",
            "format": "json",
            "timespan": "1d",
            "sort": "datedesc",
            "maxrecords": "8",
        }
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(GDELT_DOC_API, params=params) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            f"News service returned HTTP {response.status}"
                        )
                    payload = await response.json(content_type=None)
            items = self._normalize_news(payload)
            result = {
                "symbol": symbol,
                "source": "GDELT",
                "updated_at": time.time(),
                "items": items,
            }
            self._news_cache[symbol] = (time.monotonic(), result)
            return web.json_response(result)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            RuntimeError,
            ValueError,
        ) as error:
            if cached:
                stale = {**cached[1], "stale": True}
                return web.json_response(stale)
            return web.json_response(
                {
                    "symbol": symbol,
                    "source": "GDELT",
                    "items": [],
                    "error": f"Commodity news is temporarily unavailable: {error}",
                },
                status=502,
            )

    @staticmethod
    def _normalize_news(payload):
        items = []
        seen = set()
        articles = payload.get("articles") or [] if isinstance(payload, dict) else []
        for article in articles:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            url = str(article.get("url") or "").strip()
            parsed = urlparse(url)
            if (
                not title
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
            ):
                continue
            key = url
            if key in seen:
                continue
            seen.add(key)
            seen_date = str(article.get("seendate") or "")
            published_at = None
            try:
                published_at = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.strptime(seen_date, "%Y%m%dT%H%M%SZ"),
                )
            except ValueError:
                pass
            items.append(
                {
                    "title": title,
                    "url": url,
                    "source": str(article.get("domain") or "News source"),
                    "published_at": published_at,
                }
            )
        return items[:8]
