"""Read-only live MCX futures quote endpoint."""

from collections import deque
import asyncio
from datetime import datetime
import logging
import math
import time
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from analytics.commodity_option_analysis import analyze_commodity_option_chain

logger = logging.getLogger(__name__)

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
GOOGLE_NEWS_RSS_API = "https://news.google.com/rss/search"

# Providers whose APIs cannot serve MCX data at all. Their commodity
# adapter stubs always return None, which would otherwise surface as a
# misleading 404 "no live quote" — report the real limitation instead.
_MCX_UNSUPPORTED_PROVIDERS = {
    "BREEZE": "ICICI Breeze",
}


def _mcx_unsupported_response(provider_name: str):
    label = _MCX_UNSUPPORTED_PROVIDERS.get(provider_name)
    if label is None:
        return None
    return web.json_response(
        {
            "error": (
                f"{label} does not provide MCX market data. Switch the "
                "market-data provider to SMARTAPI, UPSTOX, or KOTAK for "
                "commodities."
            ),
            "provider": provider_name,
        },
        status=501,
    )


class CommodityPricesApi:
    _HISTORY_WINDOW_SECONDS = 300
    _HISTORY_MAX_AGE_SECONDS = 1200
    # Quotes are polled ~1s apart — an LTP jump beyond this vs the last
    # accepted quote is a bad tick (decimal error, wrong-contract print,
    # feed glitch), never a real trade. Held quotes are served instead.
    _MCX_MAX_TICK_PCT = 5.0

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
        self._previous_close_oi = {}
        self._previous_close_oi_retry_at = {}
        self._previous_close_oi_jobs = {}
        self._news_cache = {}
        # GDELT DOC API allows roughly one request per 5s globally — not
        # per symbol. Without a shared throttle, switching symbols (or a
        # cold cache after restart) bursts requests and every one comes
        # back HTTP 429, leaving the news panel permanently empty.
        self._news_lock = asyncio.Lock()
        self._last_news_fetch = 0.0
        self._last_good_quotes = {}
        self._tick_candidates = {}

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
            # Count the contiguous minute-run actually observed so the
            # dashboard shows a truthful countdown, not a hardcoded "1m".
            observed = 0
            expected = minute
            for at, _values in reversed(history):
                if at == expected:
                    observed += 1
                    expected -= 1
                elif at < expected:
                    break
            return {"available": False, "window_minutes": observed}

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
        history = self._chain_history.setdefault(key, deque(maxlen=400))
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

    _OI_VELOCITY_WINDOWS = (5, 15, 30)

    @staticmethod
    def _chain_rows_by_key(rows):
        indexed = {}
        for row in rows or []:
            try:
                strike = float(row.get("strike"))
            except (TypeError, ValueError):
                continue
            side = str(row.get("type") or "").upper()
            if side not in ("CE", "PE"):
                continue

            def _num(key):
                try:
                    value = float(row.get(key))
                except (TypeError, ValueError):
                    return 0.0
                return value if math.isfinite(value) else 0.0

            indexed[(strike, side)] = {"oi": _num("oi"), "ltp": _num("ltp")}
        return indexed

    def _oi_velocity(self, symbol, expiry):
        """5/15/30-minute CE/PE OI deltas from the in-memory chain snapshots.

        Same windowed-velocity concept the equity engine computes off its
        parquet OI history (oi_analysis.get_oi_velocity), but sourced from
        _chain_history since MCX chains arrive over REST polling rather
        than the engine's snapshot recorder. Emits the identical payload
        shape ({window, rows:[{strike, ceDOI, peDOI, ...}]}) so the shared
        OI Flow / Velocity card consumes it unchanged.
        """
        blocks = [
            {"window": window, "rows": []}
            for window in self._OI_VELOCITY_WINDOWS
        ]
        history = self._chain_history.get((symbol, expiry))
        if not history or len(history) < 2:
            return blocks

        now = time.monotonic()
        current = self._chain_rows_by_key(history[-1][1])
        current_strikes = sorted({strike for strike, _ in current})

        for block in blocks:
            window = block["window"]
            target_age = window * 60
            best_ts, best_diff = None, float("inf")
            for timestamp, _rows in history:
                age = now - timestamp
                diff = abs(age - target_age)
                if diff < best_diff and age >= target_age * 0.5:
                    best_diff = diff
                    best_ts = timestamp
            if best_ts is None:
                continue

            baseline_rows = next(
                rows for ts, rows in history if ts == best_ts
            )
            baseline = self._chain_rows_by_key(baseline_rows)
            baseline_strikes = {strike for strike, _ in baseline}
            actual_age = now - best_ts
            scale = target_age / actual_age if actual_age > 0 else 1.0

            for strike in current_strikes:
                if strike not in baseline_strikes:
                    continue
                ce = current.get((strike, "CE"), {})
                pe = current.get((strike, "PE"), {})
                ce_now = ce.get("oi", 0.0)
                pe_now = pe.get("oi", 0.0)
                if ce_now == 0 and pe_now == 0:
                    continue
                ce_doi = (
                    ce_now - baseline.get((strike, "CE"), {}).get("oi", 0.0)
                ) * scale
                pe_doi = (
                    pe_now - baseline.get((strike, "PE"), {}).get("oi", 0.0)
                ) * scale

                if ce_doi > 0 and pe_doi > 0:
                    signal = "Writing BuildUp"
                elif ce_doi < 0 and pe_doi < 0:
                    signal = "Unwinding"
                elif ce_doi > 0 and pe_doi <= 0:
                    signal = "CE Build / PE Unwind"
                elif ce_doi <= 0 and pe_doi > 0:
                    signal = "PE Build / CE Unwind"
                else:
                    signal = "Neutral"

                block["rows"].append(
                    {
                        "strike": int(strike)
                        if float(strike).is_integer()
                        else strike,
                        "ceNow": ce_now,
                        "ceDOI": round(ce_doi, 2),
                        "ceLTP": round(ce.get("ltp", 0.0), 1),
                        "peNow": pe_now,
                        "peDOI": round(pe_doi, 2),
                        "peLTP": round(pe.get("ltp", 0.0), 1),
                        "signal": signal,
                    }
                )
        return blocks

    def _schedule_previous_close_oi(self, provider, symbol, expiry, rows):
        fetch_oi = getattr(provider, "get_commodity_option_previous_close_oi", None)
        if not callable(fetch_oi):
            return "unsupported"

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        job_key = (symbol, expiry, today)
        if job_key in self._previous_close_oi_jobs:
            return "loading"

        self._previous_close_oi = {
            key: value for key, value in self._previous_close_oi.items()
            if key[3] == today
        }
        self._previous_close_oi_retry_at = {
            key: value for key, value in self._previous_close_oi_retry_at.items()
            if key[3] == today
        }
        tokens = {
            str(row.get("token"))
            for row in rows
            if row.get("token") is not None
        }
        due = []
        now = time.monotonic()
        for token in tokens:
            baseline_key = (symbol, expiry, token, today)
            if self._previous_close_oi.get(baseline_key) is not None:
                continue
            if self._previous_close_oi_retry_at.get(baseline_key, 0) > now:
                continue
            due.append((token, baseline_key))
        if not due:
            return "ready" if any(
                self._previous_close_oi.get((symbol, expiry, token, today)) is not None
                for token in tokens
            ) else "unavailable" if tokens else "pending"

        async def load_baselines():
            try:
                for index, (token, baseline_key) in enumerate(due):
                    try:
                        value = await asyncio.to_thread(fetch_oi, token)
                        if value is None:
                            raise ValueError("historical OI is unavailable")
                        value = float(value)
                        if not math.isfinite(value) or value < 0:
                            raise ValueError("historical OI is invalid")
                        self._previous_close_oi[baseline_key] = value
                        self._previous_close_oi_retry_at.pop(baseline_key, None)
                    except Exception as error:
                        self._previous_close_oi_retry_at[baseline_key] = (
                            time.monotonic() + 300
                        )
                        logger.warning(
                            "Previous-close OI unavailable for MCX %s %s token %s: %s",
                            symbol, expiry, token, error,
                        )
                    if index < len(due) - 1:
                        await asyncio.sleep(0.35)
            finally:
                self._previous_close_oi_jobs.pop(job_key, None)

        self._previous_close_oi_jobs[job_key] = asyncio.create_task(load_baselines())
        return "loading"

    def _attach_previous_close_oi(self, chain, symbol, expiry):
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        rows = chain.get("rows") or []
        current_count = 0
        matched_count = 0
        for row in rows:
            token = row.pop("token", None)
            try:
                oi = float(row["oi"])
                ltp = float(row["ltp"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(oi) or oi < 0 or not math.isfinite(ltp) or ltp <= 0:
                continue
            current_count += 1
            baseline_key = (symbol, expiry, str(token), today)
            previous_oi = self._previous_close_oi.get(baseline_key)
            if previous_oi is None:
                continue
            oi_change = oi - previous_oi
            row["oi_change"] = oi_change
            row["capital_flow"] = oi_change * ltp
            matched_count += 1

        chain["flow_baseline"] = "previous_close"
        chain["flow_baseline_status"] = (
            "ready" if matched_count == current_count and matched_count > 0
            else "partial" if matched_count > 0
            else "pending"
        )

    @staticmethod
    def _active_market_data():
        from brokers.market_data_registry import market_data

        return market_data

    @staticmethod
    def _get_active_provider():
        from brokers.market_data_registry import get_active_provider

        return get_active_provider()

    def _guard_against_bad_tick(self, symbol, which, quote):
        """Reject single-poll LTP spikes before they reach the dashboard.

        A bad MCX print (decimal error, wrong-contract tick, feed glitch)
        otherwise flashes through the top-bar price for one poll and
        writes a spike candle into the chart history. Quotes arrive ~1s
        apart, so a >5% jump vs the last accepted quote cannot be a real
        trade on its own — but a genuine gap must not be suppressed
        forever. A rejected level is held for up to 3 consecutive polls
        (~3s); if it persists it is accepted as real, otherwise the last
        good quote keeps being served so the top bar and chart never
        flash or spike on a bad print.
        """
        if not quote:
            return quote
        try:
            ltp = float(quote.get("LTP"))
        except (TypeError, ValueError):
            ltp = None
        key = (
            symbol,
            which,
            str(quote.get("Contract") or ""),
        )
        last = self._last_good_quotes.get(key)
        if ltp is None or not math.isfinite(ltp) or ltp <= 0:
            return last if last is not None else quote
        # Stateless sanity vs previous close — catches absurd prints even
        # when no last-good baseline exists yet (first tick after start).
        try:
            prev_close = float(quote.get("PrevClose"))
        except (TypeError, ValueError):
            prev_close = None
        if (
            prev_close is not None
            and math.isfinite(prev_close)
            and prev_close > 0
            and (ltp > prev_close * 10 or ltp < prev_close / 10)
        ):
            return last if last is not None else quote
        if last is not None:
            try:
                last_ltp = float(last.get("LTP"))
            except (TypeError, ValueError):
                last_ltp = None
            if (
                last_ltp is not None
                and math.isfinite(last_ltp)
                and last_ltp > 0
                and abs(ltp - last_ltp) / last_ltp * 100.0
                > self._MCX_MAX_TICK_PCT
            ):
                candidate = self._tick_candidates.get(key)
                count = (candidate or {}).get("count", 0) + 1
                if count >= 3:
                    # The new level held for ~3 polls — a real gap, not
                    # a stray print. Accept it and re-baseline.
                    self._tick_candidates.pop(key, None)
                    self._last_good_quotes[key] = quote
                    return quote
                self._tick_candidates[key] = {"quote": quote, "count": count}
                return last
        self._tick_candidates.pop(key, None)
        self._last_good_quotes[key] = quote
        return quote

    async def quote(self, request):
        symbol = request.query.get("symbol", "CRUDEOIL").strip().upper()
        if symbol not in COMMODITY_SYMBOLS:
            return web.json_response(
                {"error": "Unsupported commodity symbol."}, status=400
            )
        which = request.query.get("which", "NEAR").strip().upper()
        if which not in {"NEAR", "NEXT", "FAR"}:
            return web.json_response(
                {"error": "Futures slot must be NEAR, NEXT, or FAR."}, status=400
            )

        provider = self._market_data()
        get_quote = getattr(provider, "get_commodity_futures_quote", None)
        provider_name = self._active_provider()
        unsupported = _mcx_unsupported_response(provider_name)
        if unsupported is not None:
            return unsupported
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

        if which == "NEAR":
            quote = await asyncio.to_thread(get_quote, symbol)
        else:
            quote = await asyncio.to_thread(get_quote, symbol, which=which)
        quote = self._guard_against_bad_tick(symbol, which, quote)
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
        unsupported = _mcx_unsupported_response(provider_name)
        if unsupported is not None:
            return unsupported
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
        chain = {
            **chain,
            "rows": [dict(row) for row in (chain.get("rows") or [])],
        }
        chain["expiry"] = expiry
        chain["symbol"] = symbol
        price_closes = [
            (float(minute), float(price))
            for minute, price in self._quote_history.get(symbol, ())
        ]
        previous_rows, elapsed = self._observe_chain(symbol, expiry, chain)
        baseline_status = self._schedule_previous_close_oi(
            provider, symbol, expiry, chain.get("rows") or []
        )
        self._attach_previous_close_oi(chain, symbol, expiry)
        chain["oi_velocity"] = self._oi_velocity(symbol, expiry)
        if chain["flow_baseline_status"] == "pending":
            chain["flow_baseline_status"] = baseline_status
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
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with self._news_lock:
                # Space ALL GDELT calls ≥6s apart — the limit is per-IP,
                # not per-symbol, so concurrent symbol fetches must queue.
                wait = 6.0 - (time.monotonic() - self._last_news_fetch)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_news_fetch = time.monotonic()
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(GDELT_DOC_API, params=params) as response:
                        if response.status != 200:
                            raise RuntimeError(
                                f"GDELT HTTP {response.status}"
                            )
                        payload = await response.json(content_type=None)
            items = self._normalize_news(payload)
            source_name = "GDELT"
        except Exception as gdelt_error:
            # GDELT aggressively rate-limits (HTTP 429 after ~12s, often
            # past the timeout) — fall back to Google News RSS so the
            # panel still fills instead of sitting empty.
            try:
                items, source_name = await self._fetch_google_news(
                    symbol, COMMODITY_NEWS_TERMS[symbol]
                )
            except Exception as fallback_error:
                if cached:
                    stale = {**cached[1], "stale": True}
                    return web.json_response(stale)
                return web.json_response(
                    {
                        "symbol": symbol,
                        "source": "GDELT",
                        "items": [],
                        "error": (
                            "Commodity news is temporarily unavailable: "
                            f"{gdelt_error!r} / {fallback_error!r}"
                        ),
                    },
                    status=502,
                )
        result = {
            "symbol": symbol,
            "source": source_name,
            "updated_at": time.time(),
            "items": items,
        }
        self._news_cache[symbol] = (time.monotonic(), result)
        return web.json_response(result)

    @staticmethod
    async def _fetch_google_news(symbol, query):
        """Google News RSS fallback — returns (items, source_name)."""
        import re
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime
        import html as _html

        params = {
            "q": query,
            "hl": "en-IN",
            "gl": "IN",
            "ceid": "IN:en",
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(GOOGLE_NEWS_RSS_API, params=params) as response:
                if response.status != 200:
                    raise RuntimeError(f"Google News HTTP {response.status}")
                body = await response.text()
        root = ET.fromstring(body)
        items = []
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            src_el = item.find("source")
            title = (title_el.text or "").strip() if title_el is not None else ""
            # Google wraps matched terms in <b> and HTML-escapes entities.
            title = re.sub(r"</?b>", "", _html.unescape(title)).strip()
            url = (link_el.text or "").strip() if link_el is not None else ""
            parsed = urlparse(url)
            if not title or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            published_at = None
            if pub_el is not None and pub_el.text:
                try:
                    published_at = parsedate_to_datetime(pub_el.text.strip()).isoformat()
                except (TypeError, ValueError):
                    pass
            items.append(
                {
                    "title": title,
                    "url": url,
                    "source": (src_el.text or "").strip() if src_el is not None else "Google News",
                    "published_at": published_at,
                }
            )
            if len(items) >= 8:
                break
        if not items:
            raise RuntimeError("Google News returned no items")
        return items, "Google News"

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
